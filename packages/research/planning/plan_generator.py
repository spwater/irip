"""计划生成 Mixin：generate_plan 及其辅助逻辑。

拆分自 plan_service.py（IRIP 拆分任务）。``PlanGeneratorMixin`` 承载
AI 检查数据 → 生成 DAG 计划 → 保存不可变版本的完整流程，
以及数据 Profile 构建、AI 调用、模式增强、覆盖声明预估等辅助方法。

计划级授权：
- 确认后的计划记录 ScopeBoundary（snapshot_id / question_version / methods / resource_tier）；
- 越界行为（新增数据/改变目标/首次知识库/扩大资源/发布）触发重新确认。
"""

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.execution.models_trusted import (
    CoverageDeclaration,
    DataProfile,
    PlanStep,
    PlanVersionRef,
    ScopeBoundary,
)
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.planning.plan_base import PlanServiceBase
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.plan_service")


class PlanGeneratorMixin(PlanServiceBase):
    """计划生成功能域：generate_plan 及辅助方法。"""

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

            # 4. 获取研究问题 — Timeline refactoring: 从 Turn 读取，不再从 question version 读取
            # 如果 plan 有 turn_id 关联（新 Timeline 模式），从 Turn.question_text_snapshot 读
            # 否则回退到旧模式（兼容尚未迁移的 workspace）
            research_question = ""
            sub_questions: list[str] = []

            # 检查是否有 Turn 关联（新 Timeline 模式）
            from packages.research.timeline.entities import ResearchTurn

            turn_result = await session.execute(
                sa.select(ResearchTurn)
                .where(ResearchTurn.evidence_snapshot_id == snapshot_id)
                .order_by(ResearchTurn.turn_number.desc())
                .limit(1)
            )
            turn = turn_result.scalar_one_or_none()
            if turn is not None:
                research_question = turn.question_text_snapshot
                # 从 TurnContext 加载选中的历史结论作为子问题参考
                from packages.research.timeline.context_builder import (
                    TurnContextBuilder,
                )

                conclusions = await TurnContextBuilder.build_conclusion_inputs(session, turn.id)
                # 把历史结论作为上下文注入（不作为子问题）
                # 但确保 AI 能看到这些结论
                if conclusions:
                    sub_questions = [c.statement for c in conclusions]
            else:
                # 旧模式回退：从 question version 读（兼容）
                # 注意：get_latest_question_version 已在 Task 1 中移除
                # 如果 workspace 没有关联 Turn，使用空问题
                research_question = ""

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

    async def _build_data_profile(
        self,
        session: AsyncSession,
        snapshot: Any,
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
                logger.warning("unexpected error", exc_info=True)

            # 获取数据摘要
            get_data = getattr(self._fact_provider, "get_fact_data", None)
            if get_data is not None:
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

                    # 构建数据摘要：仅包含结构和概要，不含具体数据行
                    summary_lines = [
                        f"数据源 {_idx + 1}/{len(source_refs)}:"
                        f" 来源={fact_name},"
                        f" 样品={metadata.get('样品名') or metadata.get('样品', '?')},"
                        f" {len(fields)} 字段, {record_count} 条记录",
                    ]
                    if metadata:
                        # 元数据只传 key 列表，不传具体值
                        meta_keys = list(metadata.keys()) if isinstance(metadata, dict) else []
                        summary_lines.append(f"  元数据字段: {meta_keys}")
                    if isinstance(points, list) and points:
                        # 只传 point 的 name 列表，不传具体值
                        point_names = [
                            p.get("name", "?") for p in points[:10] if isinstance(p, dict)
                        ]
                        summary_lines.append(f"  数据点指标: {point_names}")
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
    ) -> dict[str, Any]:
        """调用 AI 生成分析建议（纯文本，不分步 DAG）。

        Args:
            data_profile: 数据 Profile。
            research_question: 研究问题文本。
            sub_questions: 子问题列表。

        Returns:
            dict: 含单步 advice 的 DAG 结构（兼容已有存储格式）。
        """
        from packages.research.execution.models_trusted import TaskType

        # 构建传给 AI 的数据描述：基本信息 + 实际数据内容
        basic_summary = self._context_router.build_data_profile_summary(data_profile)
        if data_profile.data_summary:
            data_text = f"{basic_summary}\n\n各数据源详情:\n{data_profile.data_summary}"
        else:
            data_text = basic_summary
        from packages.ai.prompt_store import get_prompt

        system_prompt = get_prompt("plan_generation.system_prompt")
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
    ) -> dict[str, Any]:
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
            f"## 分析策略\n"
            f"1. 第一步：数据质量检查——确认字段完整性，检查缺失值和异常值\n"
            f"2. 第二步：描述性统计——对研究问题「{research_question[:80]}」相关的指标做统计汇总\n"
            f"3. 第三步：对比分析——跨样品/跨条件对比关键指标差异\n\n"
            f"## 可视化建议\n"
            f"- 柱状图：适合成分对比\n"
            f"- 折线图：适合趋势/累积分布\n"
            f"- Markdown 表格：适合精确数值对比\n\n"
            f"## 关注要点\n"
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
        dag_json: dict[str, Any],
        data_profile: DataProfile,
    ) -> dict[str, Any]:
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

    def _estimate_coverage(
        self, dag_structure: dict[str, Any], data_profile: DataProfile
    ) -> dict[str, Any]:
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
        dag_structure: dict[str, Any],
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
