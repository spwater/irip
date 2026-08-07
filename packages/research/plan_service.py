"""计划服务：AI 检查数据 → 生成 DAG 计划 + 用户确认 + 版本管理。

PlanService 负责：
1. generate_plan: AI 检查证据快照数据结构与质量 → 生成步骤化 DAG 计划 → 保存不可变版本；
2. confirm_plan: 用户确认计划（draft → confirmed）；
3. list_plans / get_plan: 计划查询。

计划级授权：
- 确认后的计划记录 ScopeBoundary（snapshot_id / question_version / methods / resource_tier）；
- 越界行为（新增数据/改变目标/首次知识库/扩大资源/发布）触发重新确认。

参照 packages/research/snapshots.py 的 ScopedSessionMixin 模式。
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.context_router import ContextRouter
from packages.research.models_trusted import (
    CoverageDeclaration,
    DataProfile,
    PlanDetail,
    PlanStep,
    PlanVersionRef,
    ScopeBoundary,
)
from packages.research.repository import ResearchRepository
from packages.research.repository_trusted import ResearchRepositoryTrusted

logger = logging.getLogger("research.plan_service")


class PlanService(ScopedSessionMixin):
    """分析计划生成与确认服务。

    依赖注入 session_factory / department_id / actor_id
    / model_gateway / context_router / fact_provider。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _model_gateway: 模型网关。
        _context_router: 上下文路由器。
        _fact_provider: CoreFactProvider 只读适配器。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        model_gateway: object,
        context_router: ContextRouter,
        fact_provider: object,
    ) -> None:
        """初始化计划服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            model_gateway: 模型网关（ModelGateway 实例）。
            context_router: 上下文路由器。
            fact_provider: CoreFactProvider 只读适配器。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._model_gateway = model_gateway
        self._context_router = context_router
        self._fact_provider = fact_provider
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    async def generate_plan(
        self,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> PlanVersionRef:
        """AI 检查数据并生成分析计划。

        流程：
        1. 校验工作空间归属 + 快照归属；
        2. 构建数据 Profile（通过 CoreFactProvider 获取字段清单 + 数据摘要）；
        3. 调用 ModelGateway.call(PLANNING) 生成 DAG 步骤 JSON；
        4. ContextRouter 预分析每步 → 填入 analysis_mode / data_budget / mode_reason；
        5. 计算预估覆盖声明；
        6. 保存为不可变 ResearchAnalysisPlanVersion；
        7. 旧版本标记为 superseded；
        8. 审计。

        Args:
            workspace_id: 工作空间 ID。
            snapshot_id: 证据快照 ID。

        Returns:
            PlanVersionRef: 计划版本引用。

        Raises:
            AppError: code="not_found"，当工作空间或快照不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 校验工作空间归属
            workspace = await ResearchRepository.get_workspace(session, workspace_id, actor_id)
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 2. 获取快照
            snapshots = await ResearchRepository.list_snapshots(session, workspace_id)
            snapshot = None
            for s in snapshots:
                if s.id == snapshot_id:
                    snapshot = s
                    break
            if snapshot is None:
                raise AppError(
                    code="not_found",
                    message="证据快照不存在",
                    retryable=False,
                    fields={"snapshot_id": str(snapshot_id)},
                )

            # 3. 构建数据 Profile
            data_profile = await self._build_data_profile(session, snapshot)

            # 4. 获取研究问题（含子问题）
            question = await ResearchRepository.get_latest_question_version(session, workspace_id)
            research_question = question.question_text if question else ""
            sub_questions = question.sub_questions if question and question.sub_questions else []

            # 5. 调用 AI 生成计划
            dag_json = await self._call_ai_for_plan(data_profile, research_question, sub_questions)

            # 6. ContextRouter 预分析每步
            dag_structure = self._enrich_plan_with_modes(dag_json, data_profile)

            # 7. 计算预估覆盖声明
            coverage_declaration = self._estimate_coverage(dag_structure, data_profile)

            # 8. 获取版本号
            latest_plan = await ResearchRepositoryTrusted.get_latest_plan_version(
                session, workspace_id
            )
            version_number = (latest_plan.version_number + 1) if latest_plan else 1

            # 9. 插入计划版本
            plan = await ResearchRepositoryTrusted.insert_plan_version(
                session,
                workspace_id=workspace_id,
                version_number=version_number,
                dag_structure=dag_structure,
                coverage_declaration=coverage_declaration,
                created_by=actor_id,
            )

            # 10. 旧版本标记为 superseded
            if latest_plan is not None:
                await ResearchRepositoryTrusted.supersede_old_plans(session, workspace_id, plan.id)

            # 11. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.plan.generate",
                    actor_user_id=actor_id,
                    resource_type="research_analysis_plan_version",
                    resource_id=plan.id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "version_number": version_number,
                        "snapshot_id": str(snapshot_id),
                        "step_count": len(dag_structure.get("steps", [])),
                    },
                ),
            )

            step_count = len(dag_structure.get("steps", []))
            return PlanVersionRef(
                plan_id=plan.id,
                workspace_id=workspace_id,
                version_number=version_number,
                status="draft",
                step_count=step_count,
            )

    async def confirm_plan(
        self,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> PlanVersionRef:
        """确认分析计划。

        流程：
        1. 校验计划状态为 draft；
        2. 更新 status='confirmed', confirmed_at, confirmed_by；
        3. 审计。

        Args:
            workspace_id: 工作空间 ID。
            plan_id: 计划版本 ID。

        Returns:
            PlanVersionRef: 计划版本引用（status=confirmed）。

        Raises:
            AppError: code="not_found"，当计划不存在时。
            AppError: code="validation_failed"，当计划状态不是 draft 时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            plan = await ResearchRepositoryTrusted.get_plan(session, plan_id)
            if plan is None or plan.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="分析计划不存在",
                    retryable=False,
                    fields={"plan_id": str(plan_id)},
                )

            if plan.status != "draft":
                raise AppError(
                    code="validation_failed",
                    message=f"计划状态为 '{plan.status}'，仅 draft 状态可确认",
                    retryable=False,
                    fields={"plan_id": str(plan_id), "status": plan.status},
                )

            now = datetime.now(UTC)
            await ResearchRepositoryTrusted.update_plan_status(
                session,
                plan_id,
                "confirmed",
                confirmed_at=now,
                confirmed_by=actor_id,
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.plan.confirm",
                    actor_user_id=actor_id,
                    resource_type="research_analysis_plan_version",
                    resource_id=plan_id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "version_number": plan.version_number,
                    },
                ),
            )

            step_count = len(plan.dag_structure.get("steps", []))
            return PlanVersionRef(
                plan_id=plan.id,
                workspace_id=workspace_id,
                version_number=plan.version_number,
                status="confirmed",
                step_count=step_count,
            )

    async def revise_plan(
        self,
        workspace_id: UUID,
        plan_id: UUID,
        revised_steps: list[dict],
    ) -> PlanVersionRef:
        """基于已有计划创建修订版本（用户调整步骤后保存）。

        流程：
        1. 校验原计划存在且属于该工作空间；
        2. 旧版本标记为 superseded；
        3. 创建新版本（version_number+1, status=draft, dag_structure 用修订后的 steps）；
        4. 审计。

        Args:
            workspace_id: 工作空间 ID。
            plan_id: 原计划版本 ID。
            revised_steps: 修订后的步骤列表。

        Returns:
            PlanVersionRef: 新计划版本引用（status=draft）。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            old_plan = await ResearchRepositoryTrusted.get_plan(session, plan_id)
            if old_plan is None or old_plan.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="分析计划不存在",
                    retryable=False,
                    fields={"plan_id": str(plan_id)},
                )

            # 旧版本标记为 superseded
            await ResearchRepositoryTrusted.update_plan_status(
                session,
                plan_id,
                "superseded",
            )

            # 构建新 dag_structure
            new_dag = {
                "steps": revised_steps,
                "coverage_declaration": old_plan.dag_structure.get("coverage_declaration", {}),
            }

            # 创建新版本
            new_version = await ResearchRepositoryTrusted.insert_plan_version(
                session,
                workspace_id=workspace_id,
                version_number=old_plan.version_number + 1,
                dag_structure=new_dag,
                coverage_declaration=old_plan.coverage_declaration,
                created_by=actor_id,
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.plan.revise",
                    actor_user_id=actor_id,
                    resource_type="research_analysis_plan_version",
                    resource_id=new_version.id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "old_plan_id": str(plan_id),
                        "new_version": new_version.version_number,
                        "step_count": len(revised_steps),
                    },
                ),
            )

            return PlanVersionRef(
                plan_id=new_version.id,
                workspace_id=workspace_id,
                version_number=new_version.version_number,
                status="draft",
                step_count=len(revised_steps),
            )

    async def analyze_data(
        self,
        workspace_id: UUID,
        plan_id: UUID,
        snapshot_id: UUID,
        edited_advice: str | None = None,
    ) -> dict:
        """基于分析建议执行数据分析（Step 2，不含 Insight 提取）。

        Args:
            workspace_id: 工作空间 ID。
            plan_id: 计划版本 ID。
            snapshot_id: 证据快照 ID。
            edited_advice: 用户编辑后的分析建议文本（可选，为空则用原始建议）。

        Returns:
            dict: {analysis_result: str}
        """
        from packages.research.models_trusted import TaskType

        async with self._scoped_session() as session:
            # 1. 获取计划
            plan = await ResearchRepositoryTrusted.get_plan(session, plan_id)
            if plan is None or plan.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="分析计划不存在",
                    retryable=False,
                    fields={"plan_id": str(plan_id)},
                )

            # 2. 获取快照
            snapshots = await ResearchRepository.list_snapshots(session, workspace_id)
            snapshot = None
            for s in snapshots:
                if s.id == snapshot_id:
                    snapshot = s
                    break
            if snapshot is None:
                raise AppError(
                    code="not_found",
                    message="证据快照不存在",
                    retryable=False,
                    fields={"snapshot_id": str(snapshot_id)},
                )

            # 3. 构建完整数据
            import json as _json

            source_refs = snapshot.source_refs or []
            compact_data_parts: list[str] = []

            for _idx, ref in enumerate(source_refs):
                source_id = UUID(str(ref.get("id"))) if ref.get("id") else None
                if source_id is None:
                    continue
                fact_name = ""
                try:
                    _result = await session.execute(
                        sa.text("SELECT task_name FROM fact WHERE id = :fid"),
                        {"fid": str(source_id)},
                    )
                    _row = _result.fetchone()
                    if _row:
                        fact_name = _row[0] or ""
                except Exception:
                    pass
                get_data = getattr(self._fact_provider, "get_fact_data", None)
                if get_data is not None:
                    fact_data = await get_data(source_id)
                    if isinstance(fact_data, dict):
                        compact = _json.dumps(fact_data, ensure_ascii=False, separators=(",", ":"))
                        sample_label = fact_name or f"source_{_idx + 1}"
                        compact_data_parts.append(
                            f"### 样品: {sample_label}\n```json\n{compact}\n```"
                        )

            full_data_text = "\n\n".join(compact_data_parts)

            # 4. 获取研究问题（含子问题）
            question = await ResearchRepository.get_latest_question_version(session, workspace_id)
            research_question = question.question_text if question else ""
            sub_questions = question.sub_questions if question and question.sub_questions else []

            # 5. 使用编辑后的建议
            steps = plan.dag_structure.get("steps", []) if plan.dag_structure else []
            advice_text = edited_advice or (steps[0].get("expected_output", "") if steps else "")

            # 6. LLM 数据分析
            sub_q_section = ""
            sub_q_instruction = ""
            if sub_questions:
                sub_q_lines = "\n".join(f"  - {sq}" for sq in sub_questions if sq.strip())
                sub_q_section = f"子问题:\n{sub_q_lines}\n"
                sub_q_instruction = (
                    f"\n\n**你必须逐个回答以下子问题，每个子问题给出明确结论：**\n{sub_q_lines}\n"
                )
            analysis_system_prompt = (
                "你是一个数据分析专家。请根据以下分析建议，对提供的完整数据进行实际分析。\n"
                f"要求：\n"
                "1. 按建议中的分析路径逐步执行\n"
                "2. 给出具体的数值结论（如 A 组分在样品1中 X%，在样品2中 Y%，差 Z%）\n"
                "3. 识别关键差异和特征\n"
                "4. 根据分析建议中的可视化方案画出对应图表\n"
                "   - 单个样品的连续数据（如光谱、粒度分布）用 ```chart-ref 代码块\n"
                '     格式：{"sample":"样品标签","series_index":0,'
                '"x_col":0,"y_col":1,"chart_type":"line","title":"标题"}\n'
                "     前端会自动从已加载数据中提取完整数据画图，无需在指令中重复数据点\n"
                "   - 多样品对比、聚合统计等需要跨样品计算的场景用 ```echarts 代码块\n"
                "     必须是合法的 JSON，不能用 JavaScript 函数\n"
                '     tooltip formatter 用字符串模板如 "{b}: {c}%"，不要用 function\n'
                "     支持 bar/line/pie/scatter 类型，数值用原始数字\n"
                "   - 柱状图用于成分对比，折线图用于趋势/累积分布，散点图用于相关性\n"
                "5. 对比数据用 Markdown 表格\n"
                "6. 用中文回答，给出有数据支撑的结论\n"
                "7. 请根据问题内容，判断合适的结构化输出数据。对每个问题和子问题都执行一次：\n"
                "   - 在报告末尾为每个问题/子问题分别附加一个 ```data 代码块\n"
                '   - 代码块内为三段式 JSON：{"metadata": {}, "points": [], "series": []}\n'
                "   - metadata: 报告级单值信息（如分析范围、方法、时间等）\n"
                '   - points: 独立单值指标 [{"name": "指标名", "value": 数值, "unit": "单位"}]\n'
                '   - series: 表格/序列数据 [{"name": "表名",'
                ' "columns": ["列1", "列2"], "rows": [[值1, 值2], ...]}]\n'
                "   - 数据必须来自实际分析结果，不要臆造\n"
                "   - 如果某个问题/子问题不适合结构化输出（如纯定性判断），"
                "可跳过该问题的 ```data 块\n"
                f"{sub_q_instruction}"
            )
            analysis_context = (
                f"研究问题: {research_question}\n"
                f"{sub_q_section}"
                f"分析建议:\n{advice_text}\n\n"
                f"以下是完整实验数据：\n{full_data_text}"
            )

            analysis_result = ""
            try:
                response = await self._model_gateway.call(
                    task_type=TaskType.LONG_CONTEXT,
                    system_prompt=analysis_system_prompt,
                    data_context=full_data_text[:256000],
                    research_context=analysis_context,
                )
                analysis_result = response.answer if hasattr(response, "answer") else str(response)
                # 清洗 echarts
                import re as _re

                def _clean_echarts_block(match):
                    block = match.group(1)
                    block = _re.sub(
                        r'"formatter"\s*:\s*function\s*\([^)]*\)\s*\{[^}]*(?:\{[^}]*\}[^}]*)*\}',
                        '"formatter": "{b}: {c}"',
                        block,
                    )
                    block = _re.sub(
                        r'"formatter"\s*:\s*function[\s\S]*?\}',
                        '"formatter": "{b}: {c}"',
                        block,
                    )
                    return "```echarts\n" + block + "\n```"

                analysis_result = _re.sub(
                    r"```echarts\n([\s\S]*?)```",
                    _clean_echarts_block,
                    analysis_result,
                )
            except Exception as exc:
                logger.warning("Analysis step failed: %s", exc)
                analysis_result = f"分析失败: {exc}"

            # 7. 持久化分析结果到 dag_structure
            try:
                stored_steps = plan.dag_structure.get("steps", []) if plan.dag_structure else []
                if stored_steps:
                    stored_steps[0]["analysis_result"] = analysis_result
                    stored_steps[0]["data_context"] = full_data_text
                    new_dag = dict(plan.dag_structure)
                    new_dag["steps"] = stored_steps
                    await session.execute(
                        sa.text(
                            "UPDATE research_analysis_plan_version "
                            "SET dag_structure = :dag WHERE id = :pid"
                        ),
                        {"dag": _json.dumps(new_dag, ensure_ascii=False), "pid": str(plan.id)},
                    )
            except Exception as exc:
                logger.warning("Failed to persist analysis result: %s", exc)

            # 8. 解析 ```data 块 → 存为 RunArtifact (type=data, is_publishable=true)
            import re as _re2

            data_blocks = _re2.findall(r"```data\s*\n([\s\S]*?)```", analysis_result)
            echarts_blocks = _re2.findall(r"```echarts\s*\n([\s\S]*?)```", analysis_result)
            run_id = None
            if data_blocks or echarts_blocks:
                import hashlib
                import os as _os

                from packages.common.ids import new_id
                from packages.common.s3_repository import S3Repository
                from packages.research.validation import ThreeSegmentValidator

                # 查找 run
                runs = await ResearchRepositoryTrusted.list_runs(session, workspace_id)
                run_id = runs[0].id if runs else None
                if run_id is None:
                    run_id = await ResearchRepositoryTrusted.insert_run(
                        session,
                        workspace_id=workspace_id,
                        plan_id=plan_id,
                        status="succeeded",
                    )
                # 构建 S3 client
                _endpoint = _os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
                if not _endpoint.startswith("http"):
                    _endpoint = f"http://{_endpoint}"
                _s3 = S3Repository(
                    endpoint_url=_endpoint,
                    access_key=_os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
                    secret_key=_os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
                    bucket_name=_os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
                    region=_os.getenv("IRIP_MINIO_REGION", "us-east-1"),
                )
                # 8a. 存 data 块
                for idx, block in enumerate(data_blocks):
                    try:
                        result = ThreeSegmentValidator.validate(block.encode("utf-8"))
                        if result.valid and result.data is not None:
                            content_bytes = block.encode("utf-8")
                            content_hash = hashlib.sha256(content_bytes).hexdigest()
                            artifact_id = new_id()
                            artifact_key = f"analysis_data_{idx + 1}.json"
                            s3_key = f"research/artifacts/{run_id}/{artifact_id}/{artifact_key}"
                            _s3.put_object(s3_key, content_bytes, "application/json")
                            await session.execute(
                                sa.text(
                                    "INSERT INTO research_run_artifact "
                                    "(id, run_id, step_id, artifact_type, "
                                    "artifact_key, storage_path, content_hash, "
                                    "size_bytes, is_publishable, created_at) "
                                    "VALUES (:id, :run_id, NULL, "
                                    "'data', :key, :path, :hash, "
                                    ":size, true, now())"
                                ),
                                {
                                    "id": str(artifact_id),
                                    "run_id": str(run_id),
                                    "key": artifact_key,
                                    "path": s3_key,
                                    "hash": content_hash,
                                    "size": len(content_bytes),
                                },
                            )
                            logger.info("Inserted data artifact %s for run %s", artifact_id, run_id)
                    except Exception as exc:
                        logger.warning("Failed to save data block: %s", exc)

                # 8b. 存 echarts 块
                for idx, block in enumerate(echarts_blocks):
                    try:
                        title = f"图表 {idx + 1}"
                        try:
                            import json as _json

                            echarts_json = _json.loads(block.strip())
                            t = echarts_json.get("title", {})
                            if isinstance(t, dict):
                                title = t.get("text", title)
                            elif isinstance(t, str):
                                title = t
                        except Exception:
                            pass

                        content_bytes = block.encode("utf-8")
                        content_hash = hashlib.sha256(content_bytes).hexdigest()
                        artifact_id = new_id()
                        artifact_key = f"analysis_chart_{idx + 1}.json"
                        s3_key = f"research/artifacts/{run_id}/{artifact_id}/{artifact_key}"
                        _s3.put_object(s3_key, content_bytes, "application/json")
                        await session.execute(
                            sa.text(
                                "INSERT INTO research_run_artifact "
                                "(id, run_id, step_id, artifact_type, "
                                "artifact_key, storage_path, "
                                "content_hash, size_bytes, "
                                "is_publishable, created_at) "
                                "VALUES (:id, :run_id, NULL, "
                                "'chart', :key, :path, :hash, "
                                ":size, true, now())"
                            ),
                            {
                                "id": str(artifact_id),
                                "run_id": str(run_id),
                                "key": artifact_key,
                                "path": s3_key,
                                "hash": content_hash,
                                "size": len(content_bytes),
                            },
                        )
                        logger.info("Inserted chart artifact %s for run %s", artifact_id, run_id)
                    except Exception as exc:
                        logger.warning("Failed to save chart block: %s", exc)

            return {"analysis_result": analysis_result, "data_context": full_data_text}

    async def extract_insight(
        self,
        workspace_id: UUID,
        plan_id: UUID,
        snapshot_id: UUID,
    ) -> dict:
        """从已有分析结果提取 Insight 候选（Step 3，独立调用）。

        Args:
            workspace_id: 工作空间 ID。
            plan_id: 计划版本 ID。
            snapshot_id: 证据快照 ID。

        Returns:
            dict: {insight_candidate: dict | None,
                   insight_candidate_id: str | None,
                   run_id: str | None}
        """
        from packages.research.models_trusted import TaskType

        self._require_actor()
        async with self._scoped_session() as session:
            # 1. 获取计划 + 已有分析结果
            plan = await ResearchRepositoryTrusted.get_plan(session, plan_id)
            if plan is None or plan.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="分析计划不存在",
                    retryable=False,
                    fields={"plan_id": str(plan_id)},
                )

            steps = plan.dag_structure.get("steps", []) if plan.dag_structure else []
            analysis_result = steps[0].get("analysis_result", "") if steps else ""
            advice_text = steps[0].get("expected_output", "") if steps else ""

            if not analysis_result:
                raise AppError(
                    code="validation_failed",
                    message="请先执行数据分析再提取 Insight",
                    retryable=False,
                    fields={},
                )

            # 2. 获取研究问题（含子问题）
            question = await ResearchRepository.get_latest_question_version(session, workspace_id)
            research_question = question.question_text if question else ""
            sub_questions = question.sub_questions if question and question.sub_questions else []
            sub_q_section = ""
            if sub_questions:
                sub_q_lines = "\n".join(f"  - {sq}" for sq in sub_questions if sq.strip())
                sub_q_section = f"子问题:\n{sub_q_lines}\n"

            # 3. LLM Insight 提取
            insight_system_prompt = (
                "你是一个研究洞察提取专家。请从以下分析结果中提取结构化的 Insight。\n"
                "返回 JSON 格式，包含以下字段：\n"
                '{"conclusion": "核心结论", "scope": "适用范围", '
                '"evidence_refs": [], "method_refs": [{"step_key": "analysis"}], '
                '"confidence_level": "high/medium/low", "limitations": "局限性说明", '
                '"evidence_source_label": "experimental_data"}\n'
                "只返回 JSON，不要其他文字。"
            )
            insight_context = (
                f"研究问题: {research_question}\n"
                f"{sub_q_section}"
                f"分析建议:\n{advice_text}\n\n"
                f"分析结果:\n{analysis_result}"
            )

            insight_candidate = None
            try:
                response = await self._model_gateway.call(
                    task_type=TaskType.INSIGHT,
                    system_prompt=insight_system_prompt,
                    data_context=analysis_result[:4000],
                    research_context=insight_context,
                )
                answer = response.answer if hasattr(response, "answer") else str(response)
                import json as _json

                clean = answer.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[-1]
                if clean.endswith("```"):
                    clean = clean.rsplit("```", 1)[0]
                clean = clean.strip()
                insight_candidate = _json.loads(clean)
            except Exception as exc:
                logger.warning("Insight extraction failed: %s", exc)

            # 4. 写入候选记录
            insight_candidate_id = None
            insight_run_id = None
            if insight_candidate:
                try:
                    import json as _json

                    from packages.common.ids import new_id

                    insight_candidate_id = new_id()
                    # 复用最新 run（analyze_data 已创建），不新建 run
                    runs = await ResearchRepositoryTrusted.list_runs(session, workspace_id)
                    if runs:
                        run_id = runs[0].id
                    else:
                        run_id = await ResearchRepositoryTrusted.insert_run(
                            session,
                            workspace_id=workspace_id,
                            plan_id=plan_id,
                            status="succeeded",
                        )
                    insight_run_id = str(run_id)
                    await session.execute(
                        sa.text(
                            "INSERT INTO research_insight_candidate "
                            "(id, workspace_id, run_id, step_id, conclusion, scope, "
                            "evidence_refs, method_refs, confidence_level, limitations, "
                            "evidence_source_label, ai_raw_text, status, created_at) "
                            "VALUES (:id, :wid, :run_id, NULL, :conclusion, :scope, "
                            "'[]'::jsonb, :method_refs, :confidence, :limitations, "
                            ":evidence_label, :ai_raw, 'pending', now())"
                        ),
                        {
                            "id": str(insight_candidate_id),
                            "wid": str(workspace_id),
                            "run_id": str(run_id),
                            "conclusion": insight_candidate.get("conclusion", ""),
                            "scope": insight_candidate.get("scope", ""),
                            "method_refs": _json.dumps(
                                insight_candidate.get("method_refs", [{"step_key": "analysis"}]),
                                ensure_ascii=False,
                            ),
                            "confidence": insight_candidate.get("confidence_level", "medium"),
                            "limitations": insight_candidate.get("limitations", ""),
                            "evidence_label": insight_candidate.get(
                                "evidence_source_label", "experimental_data"
                            ),
                            "ai_raw": analysis_result[:8000],
                        },
                    )
                except Exception as exc:
                    logger.warning("Failed to save insight candidate: %s", exc)
                    insight_candidate_id = None
                    insight_run_id = None

            # 4.5. 补建 data/chart 工件（如果不存在）
            # analyze_data 步骤可能因清理等原因丢失工件，extract_insight 时自动补建
            if insight_run_id:
                try:
                    import hashlib
                    import os as _os3
                    import re as _re3

                    from packages.common.ids import new_id as _new_id
                    from packages.common.s3_repository import S3Repository
                    from packages.research.validation import ThreeSegmentValidator

                    # 检查当前 run 已有的 data/chart 工件数量
                    existing_result = await session.execute(
                        sa.text(
                            "SELECT count(*) FROM research_run_artifact "
                            "WHERE run_id = :rid AND artifact_type IN ('data','chart')"
                        ),
                        {"rid": str(insight_run_id)},
                    )
                    existing_count = existing_result.scalar() or 0

                    if existing_count == 0:
                        # 重新解析 analysis_result 中的 ```data 和 ```echarts 块
                        data_blocks = _re3.findall(r"```data\s*\n([\s\S]*?)```", analysis_result)
                        echarts_blocks = _re3.findall(
                            r"```echarts\s*\n([\s\S]*?)```", analysis_result
                        )

                        if data_blocks or echarts_blocks:
                            _endpoint = _os3.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
                            if not _endpoint.startswith("http"):
                                _endpoint = f"http://{_endpoint}"
                            _s3 = S3Repository(
                                endpoint_url=_endpoint,
                                access_key=_os3.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
                                secret_key=_os3.getenv(
                                    "IRIP_MINIO_SECRET_KEY", "irip_dev_password"
                                ),
                                bucket_name=_os3.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
                                region=_os3.getenv("IRIP_MINIO_REGION", "us-east-1"),
                            )

                            for idx, block in enumerate(data_blocks):
                                try:
                                    result = ThreeSegmentValidator.validate(block.encode("utf-8"))
                                    if result.valid and result.data is not None:
                                        content_bytes = block.encode("utf-8")
                                        content_hash = hashlib.sha256(content_bytes).hexdigest()
                                        artifact_id = _new_id()
                                        artifact_key = f"analysis_data_{idx + 1}.json"
                                        s3_key = (
                                            f"research/artifacts/{insight_run_id}"
                                            f"/{artifact_id}/{artifact_key}"
                                        )
                                        _s3.put_object(s3_key, content_bytes, "application/json")
                                        await session.execute(
                                            sa.text(
                                                "INSERT INTO research_run_artifact "
                                                "(id, run_id, step_id, "
                                                "artifact_type, artifact_key, "
                                                "storage_path, content_hash, "
                                                "size_bytes, is_publishable, "
                                                "created_at) "
                                                "VALUES (:id, :run_id, NULL, "
                                                "'data', :key, :path, :hash, "
                                                ":size, true, now())"
                                            ),
                                            {
                                                "id": str(artifact_id),
                                                "run_id": str(insight_run_id),
                                                "key": artifact_key,
                                                "path": s3_key,
                                                "hash": content_hash,
                                                "size": len(content_bytes),
                                            },
                                        )
                                        logger.info(
                                            "Rebuilt data artifact %s for run %s",
                                            artifact_id,
                                            insight_run_id,
                                        )
                                except Exception as exc:
                                    logger.warning("Failed to rebuild data block: %s", exc)

                            for idx, block in enumerate(echarts_blocks):
                                try:
                                    title = f"图表 {idx + 1}"
                                    try:
                                        import json as _json3

                                        echarts_json = _json3.loads(block.strip())
                                        t = echarts_json.get("title", {})
                                        if isinstance(t, dict):
                                            title = t.get("text", title)
                                        elif isinstance(t, str):
                                            title = t
                                    except Exception:
                                        pass

                                    content_bytes = block.encode("utf-8")
                                    content_hash = hashlib.sha256(content_bytes).hexdigest()
                                    artifact_id = _new_id()
                                    artifact_key = f"analysis_chart_{idx + 1}.json"
                                    s3_key = (
                                        f"research/artifacts/{insight_run_id}"
                                        f"/{artifact_id}/{artifact_key}"
                                    )
                                    _s3.put_object(s3_key, content_bytes, "application/json")
                                    await session.execute(
                                        sa.text(
                                            "INSERT INTO research_run_artifact "
                                            "(id, run_id, step_id, "
                                            "artifact_type, artifact_key, "
                                            "storage_path, content_hash, "
                                            "size_bytes, is_publishable, "
                                            "created_at) "
                                            "VALUES (:id, :run_id, NULL, "
                                            "'chart', :key, :path, :hash, "
                                            ":size, true, now())"
                                        ),
                                        {
                                            "id": str(artifact_id),
                                            "run_id": str(insight_run_id),
                                            "key": title,
                                            "path": s3_key,
                                            "hash": content_hash,
                                            "size": len(content_bytes),
                                        },
                                    )
                                    logger.info(
                                        "Rebuilt chart artifact %s for run %s",
                                        artifact_id,
                                        insight_run_id,
                                    )
                                except Exception as exc:
                                    logger.warning("Failed to rebuild chart block: %s", exc)
                except Exception as exc:
                    logger.warning("Failed to rebuild artifacts: %s", exc)

            # 5. 持久化候选信息到 dag_structure
            try:
                import json as _json

                stored_steps = plan.dag_structure.get("steps", []) if plan.dag_structure else []
                if stored_steps:
                    if insight_candidate:
                        stored_steps[0]["insight_candidate"] = insight_candidate
                    if insight_candidate_id:
                        stored_steps[0]["insight_candidate_id"] = str(insight_candidate_id)
                    if insight_run_id:
                        stored_steps[0]["insight_run_id"] = insight_run_id
                    new_dag = dict(plan.dag_structure)
                    new_dag["steps"] = stored_steps
                    await session.execute(
                        sa.text(
                            "UPDATE research_analysis_plan_version "
                            "SET dag_structure = :dag WHERE id = :pid"
                        ),
                        {"dag": _json.dumps(new_dag, ensure_ascii=False), "pid": str(plan.id)},
                    )
            except Exception as exc:
                logger.warning("Failed to persist insight: %s", exc)

            return {
                "insight_candidate": insight_candidate,
                "insight_candidate_id": str(insight_candidate_id) if insight_candidate_id else None,
                "run_id": insight_run_id,
            }

    async def list_plans(self, workspace_id: UUID) -> list[PlanVersionRef]:
        """列出工作空间的全部计划版本。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[PlanVersionRef]: 计划版本引用列表。
        """
        async with self._scoped_session() as session:
            plans = await ResearchRepositoryTrusted.list_plans(session, workspace_id)
            return [
                PlanVersionRef(
                    plan_id=p.id,
                    workspace_id=p.workspace_id,
                    version_number=p.version_number,
                    status=p.status,
                    step_count=len(p.dag_structure.get("steps", [])),
                )
                for p in plans
            ]

    async def get_plan(self, workspace_id: UUID, plan_id: UUID) -> PlanDetail:
        """获取计划详情。

        Args:
            workspace_id: 工作空间 ID。
            plan_id: 计划版本 ID。

        Returns:
            PlanDetail: 计划详情。

        Raises:
            AppError: code="not_found"，当计划不存在时。
        """
        async with self._scoped_session() as session:
            plan = await ResearchRepositoryTrusted.get_plan(session, plan_id)
            if plan is None or plan.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="分析计划不存在",
                    retryable=False,
                    fields={"plan_id": str(plan_id)},
                )
            return PlanDetail(
                plan_id=plan.id,
                workspace_id=plan.workspace_id,
                version_number=plan.version_number,
                status=plan.status,
                dag_structure=plan.dag_structure,
                coverage_declaration=plan.coverage_declaration,
                created_at=plan.created_at,
                confirmed_at=plan.confirmed_at,
            )

    async def _build_data_profile(
        self,
        session: AsyncSession,
        snapshot: object,
    ) -> DataProfile:
        """构建证据快照的数据 Profile。

        通过 CoreFactProvider 逐条获取字段清单，汇总为数据 Profile。

        Args:
            session: 异步会话。
            snapshot: 证据快照 ORM 实体。

        Returns:
            DataProfile: 数据 Profile。
        """
        field_manifest: dict[str, list[str]] = {}
        source_refs = snapshot.source_refs or []
        total_records = 0
        data_summary_parts: list[str] = []

        for _idx, ref in enumerate(source_refs):
            source_id = UUID(str(ref.get("id"))) if ref.get("id") else None
            if source_id is None:
                continue
            # 获取字段清单
            fields = await self._fact_provider.get_fact_fields(source_id)
            field_manifest[str(source_id)] = list(fields)

            # 获取 Fact 名称（来源任务名）
            fact_name = ""
            try:
                from sqlalchemy import text as _sql_text

                async with self._factory() as _ns:
                    _result = await _ns.execute(
                        _sql_text("SELECT task_name FROM fact WHERE id = :fid"),
                        {"fid": str(source_id)},
                    )
                    _row = _result.fetchone()
                    if _row:
                        fact_name = _row[0] or ""
            except Exception:
                pass

            # 获取数据摘要
            get_data = getattr(self._fact_provider, "get_fact_data", None)
            if get_data is not None:
                import json as _json

                fact_data = await get_data(source_id)
                if isinstance(fact_data, dict):
                    points = fact_data.get("points", [])
                    series = fact_data.get("series", [])
                    metadata = fact_data.get("metadata", {})

                    # 统计记录数：points + series 行数
                    record_count = len(points) if isinstance(points, list) else 0
                    if isinstance(series, list):
                        for s in series:
                            if isinstance(s, dict):
                                s.get("columns", [])
                                rows = s.get("rows", [])
                                record_count += len(rows) if isinstance(rows, list) else 0
                    total_records += record_count

                    # 构建数据摘要：包含实际数据内容
                    summary_lines = [
                        f"数据源 {_idx + 1}/{len(source_refs)}:"
                        f" 来源={fact_name},"
                        f" 样品={metadata.get('样品名') or metadata.get('样品', '?')},"
                        f" {len(fields)} 字段, {record_count} 条记录",
                    ]
                    if metadata:
                        summary_lines.append(
                            f"  元数据: {_json.dumps(metadata, ensure_ascii=False)[:500]}"
                        )
                    if isinstance(points, list) and points:
                        summary_lines.append(
                            f"  数据点(前5条): {_json.dumps(points[:5], ensure_ascii=False)[:500]}"
                        )
                    if isinstance(series, list) and series:
                        for s in series[:3]:
                            if isinstance(s, dict):
                                sname = s.get("name", "")
                                scols = s.get("columns", [])
                                srows = s.get("rows", [])
                                summary_lines.append(
                                    f"  数据组[{sname}] 列={scols}"
                                    f" 行数={len(srows) if isinstance(srows, list) else 0}"
                                )
                                if isinstance(srows, list) and srows:
                                    summary_lines.append(
                                        f"    前5行:"
                                        f" {_json.dumps(srows[:5], ensure_ascii=False)[:800]}"
                                    )
                    data_summary_parts.append("\n".join(summary_lines))

        # 估算总 token 数（粗略：每条记录约 500 token）
        total_tokens_estimate = total_records * 500

        return DataProfile(
            snapshot_id=snapshot.id,
            total_records=total_records,
            total_tokens_estimate=total_tokens_estimate,
            field_manifest=field_manifest,
            source_count=len(source_refs),
            data_summary="\n".join(data_summary_parts),
        )

    async def _call_ai_for_plan(
        self,
        data_profile: DataProfile,
        research_question: str,
        sub_questions: list[str] | None = None,
    ) -> dict:
        """调用 AI 生成分析建议（纯文本，不分步 DAG）。

        Args:
            data_profile: 数据 Profile。
            research_question: 研究问题文本。
            sub_questions: 子问题列表。

        Returns:
            dict: 含单步 advice 的 DAG 结构（兼容已有存储格式）。
        """
        from packages.research.models_trusted import TaskType

        # 构建传给 AI 的数据描述：基本信息 + 实际数据内容
        basic_summary = self._context_router.build_data_profile_summary(data_profile)
        if data_profile.data_summary:
            data_text = f"{basic_summary}\n\n各数据源详情:\n{data_profile.data_summary}"
        else:
            data_text = basic_summary
        system_prompt = (
            "你是一个研究分析规划专家。请根据用户提供的数据集和研究问题，"
            "给出初步的分析建议。包括：\n"
            "1. 数据概况（数据类型、规模、质量评估）\n"
            "2. 建议的分析方法和路径\n"
            "3. 建议的可视化方案（如柱状图、对比表格、散点图等）\n"
            "4. 需要关注的关键点或潜在风险\n"
            "请用 Markdown 格式输出，给出具体、可操作的建议。"
        )
        if sub_questions:
            sub_q_text = "\n".join(f"  - {sq}" for sq in sub_questions if sq.strip())
            research_context = (
                f"研究问题: {research_question}\n子问题:\n{sub_q_text}\n数据摘要:\n{data_text}"
            )
        else:
            research_context = f"研究问题: {research_question}\n数据摘要:\n{data_text}"

        try:
            response = await self._model_gateway.call(
                task_type=TaskType.PLANNING,
                system_prompt=system_prompt,
                data_context=data_text,
                research_context=research_context,
            )
            answer = response.answer if hasattr(response, "answer") else str(response)
            if answer and answer.strip():
                # 将 AI 建议文本包装为单步 DAG（兼容已有存储格式）
                return {
                    "steps": [
                        {
                            "step_key": "analysis_advice",
                            "question": research_question or "数据分析建议",
                            "evidence_refs": list(data_profile.field_manifest.keys()),
                            "method": "llm",
                            "strategy": "full",
                            "expected_output": answer.strip(),
                            "risks": [],
                            "dependencies": [],
                            "requires_full": True,
                            "per_record_semantic": False,
                            "cross_record_reasoning": False,
                            "allows_sampling": False,
                            "estimated_tokens": 0,
                            "resource_tier": "standard",
                        }
                    ]
                }
        except Exception as exc:
            logger.warning("AI plan generation failed, using fallback: %s", exc)

        # 回退：生成默认建议
        return self._generate_fallback_plan(data_profile, research_question)

    def _generate_fallback_plan(
        self,
        data_profile: DataProfile,
        research_question: str,
    ) -> dict:
        """生成回退建议（AI 调用失败时使用）。

        Args:
            data_profile: 数据 Profile。
            research_question: 研究问题。

        Returns:
            dict: 含单步建议的 DAG 结构。
        """
        field_count = len(data_profile.field_manifest)
        record_count = data_profile.total_records
        advice = (
            f"## 数据概况\n"
            f"当前数据集包含 {record_count} 条记录、{field_count} 个字段。\n\n"
            f"## 分析建议\n"
            f"1. 首先检查数据的完整性和质量，确认无缺失值或异常值。\n"
            f"2. 根据研究问题「{research_question[:80]}」，建议进行描述性统计和趋势分析。\n"
            f"3. 关注关键指标的变化趋势和异常点。\n\n"
            f"## 注意事项\n"
            f"- 数据量较小时需注意统计显著性\n"
            f"- 建议结合领域知识解读分析结果"
        )
        return {
            "steps": [
                {
                    "step_key": "analysis_advice",
                    "question": research_question or "数据分析建议",
                    "evidence_refs": list(data_profile.field_manifest.keys()),
                    "method": "llm",
                    "strategy": "full",
                    "expected_output": advice,
                    "risks": ["数据量可能不足"],
                    "dependencies": [],
                    "requires_full": True,
                    "per_record_semantic": False,
                    "cross_record_reasoning": False,
                    "allows_sampling": False,
                    "estimated_tokens": 0,
                    "resource_tier": "standard",
                }
            ]
        }

    def _enrich_plan_with_modes(
        self,
        dag_json: dict,
        data_profile: DataProfile,
    ) -> dict:
        """用 ContextRouter 预分析每步，填入 analysis_mode / data_budget / mode_reason。

        Args:
            dag_json: AI 生成的 DAG JSON。
            data_profile: 数据 Profile。

        Returns:
            dict: 增强后的 DAG 结构。
        """
        steps = dag_json.get("steps", [])
        for step_dict in steps:
            try:
                plan_step = PlanStep(
                    step_key=step_dict.get("step_key", ""),
                    question=step_dict.get("question", ""),
                    evidence_refs=step_dict.get("evidence_refs", []),
                    method=step_dict.get("method", "python"),
                    strategy=step_dict.get("strategy", "full"),
                    expected_output=step_dict.get("expected_output", ""),
                    risks=step_dict.get("risks", []),
                    dependencies=step_dict.get("dependencies", []),
                    requires_full=step_dict.get("requires_full", True),
                    per_record_semantic=step_dict.get("per_record_semantic", False),
                    cross_record_reasoning=step_dict.get("cross_record_reasoning", False),
                    allows_sampling=step_dict.get("allows_sampling", False),
                    estimated_tokens=step_dict.get("estimated_tokens", 0),
                    resource_tier=step_dict.get("resource_tier", "standard"),
                )
                mode, reason = self._context_router.analyze_step(plan_step, data_profile)
                step_dict["analysis_mode"] = mode
                step_dict["mode_reason"] = reason
                step_dict["data_budget_tokens"] = self._context_router.calculate_budget(
                    research_context_tokens=plan_step.estimated_tokens,
                )
            except Exception as exc:
                logger.warning("Failed to enrich step %s: %s", step_dict.get("step_key"), exc)
                step_dict.setdefault("analysis_mode", "mixed")
                step_dict.setdefault("mode_reason", "模式选择回退")
                step_dict.setdefault("data_budget_tokens", 50000)
        return dag_json

    def _estimate_coverage(self, dag_structure: dict, data_profile: DataProfile) -> dict:
        """计算预估覆盖声明。

        Args:
            dag_structure: DAG 步骤结构。
            data_profile: 数据 Profile。

        Returns:
            dict: 覆盖声明 JSONB 字典。
        """
        steps = dag_structure.get("steps", [])
        has_python = any(s.get("method") in ("python", "mixed") for s in steps)
        has_llm = any(s.get("method") in ("llm", "mixed") for s in steps)

        if has_python and has_llm:
            mode = "mixed"
            data_rate = 1.0
            llm_rate = 0.75
        elif has_python:
            mode = "full_compute"
            data_rate = 1.0
            llm_rate = 0.0
        elif has_llm:
            mode = "chunked_full_scan"
            data_rate = 1.0
            llm_rate = 1.0
        else:
            mode = "mixed"
            data_rate = 1.0
            llm_rate = 0.5

        return CoverageDeclaration(
            analysis_mode=mode,
            data_coverage_rate=data_rate,
            llm_read_rate=llm_rate,
            is_sampled=False,
            mode_reason="预估覆盖声明（基于计划步骤方法分布）",
        ).to_dict()

    def build_scope_boundary(
        self,
        snapshot_id: UUID,
        question_version: int,
        dag_structure: dict,
    ) -> ScopeBoundary:
        """从计划构建范围边界。

        Args:
            snapshot_id: 快照 ID。
            question_version: 研究问题版本号。
            dag_structure: DAG 步骤结构。

        Returns:
            ScopeBoundary: 范围边界。
        """
        steps = dag_structure.get("steps", [])
        methods = {s.get("method", "python") for s in steps}
        # 确认时不包含 knowledge（首次使用需重新确认）
        methods_allowed = methods - {"knowledge"}
        has_heavy = any(s.get("resource_tier") == "heavy" for s in steps)

        return ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=question_version,
            methods_allowed=methods_allowed,
            resource_tier="heavy" if has_heavy else "standard",
            knowledge_base_used=False,
        )
