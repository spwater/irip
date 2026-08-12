"""计划分析 Mixin：analyze_data / extract_insight 逻辑。

拆分自 plan_service.py（IRIP 拆分任务）。``PlanAnalyzerMixin`` 承载
基于分析建议执行数据分析（Step 2）以及从分析结果提取 Insight 候选（Step 3）
两条独立调用链。
"""

import json
import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from packages.common.errors import AppError
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.planning.plan_base import PlanServiceBase
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.plan_service")


class PlanAnalyzerMixin(PlanServiceBase):
    """计划分析功能域：analyze_data + extract_insight。"""

    async def analyze_data(
        self,
        workspace_id: UUID,
        plan_id: UUID,
        snapshot_id: UUID,
        edited_advice: str | None = None,
    ) -> dict[str, Any]:
        """基于分析建议执行数据分析（Step 2，不含 Insight 提取）。

        Args:
            workspace_id: 工作空间 ID。
            plan_id: 计划版本 ID。
            snapshot_id: 证据快照 ID。
            edited_advice: 用户编辑后的分析建议文本（可选，为空则用原始建议）。

        Returns:
            dict: {analysis_result: str}
        """
        from packages.research.execution.models_trusted import TaskType

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
                except SQLAlchemyError:
                    logger.warning(
                        "Failed to fetch fact name for source %s",
                        source_id,
                        exc_info=True,
                    )
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

            # 4. Timeline refactoring: question from Turn, not question version
            research_question = ""
            sub_questions: list[str] = []

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
                "7. **重要：涉及精确统计量计算时（均值、标准差、方差、中位数、分位数、"
                "偏度、峰度等），必须调用 describe_series 工具来计算，不要自己估算。**\n"
                "   - 将需要计算的数据序列通过 inline 方式传入工具\n"
                "   - 工具返回的结果是精确计算的，可以直接引用\n"
                "   - 仅对定性判断和简单算术（如百分比差异）可以自行计算\n"
                "8. 请根据问题内容，判断合适的结构化输出数据。对每个问题和子问题都执行一次：\n"
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
                # 构建数值工具 schema（供 LLM tool calling）
                numeric_tool_schemas: list[dict[str, Any]] = []
                if self._numeric_tools is not None:
                    from packages.ai.numeric import DESCRIBE_SERIES_SCHEMA

                    numeric_tool_schemas = [
                        {
                            "type": "function",
                            "function": {
                                "name": "describe_series",
                                "description": (
                                    "计算序列的描述统计量：count、sum、mean、"
                                    "总体/样本方差、标准差、min、max、median、分位数、"
                                    "偏度和峰度。涉及精确统计量时必须调用此工具。"
                                ),
                                "parameters": DESCRIBE_SERIES_SCHEMA,
                            },
                        },
                    ]

                logger.info(
                    "analyze_data: numeric_tools=%s, tool_schemas_count=%d",
                    self._numeric_tools is not None,
                    len(numeric_tool_schemas),
                )
                response = await self._model_gateway.call(
                    task_type=TaskType.LONG_CONTEXT,
                    system_prompt=analysis_system_prompt,
                    data_context=full_data_text[:256000],
                    research_context=analysis_context,
                    tools=numeric_tool_schemas or None,
                )
                logger.info(
                    "analyze_data: has_tool_calls=%s, tool_calls=%s",
                    bool(getattr(response, "tool_calls", None)),
                    getattr(response, "tool_calls", None),
                )
                analysis_result = response.answer if hasattr(response, "answer") else str(response)

                # 处理 tool_calls：执行数值工具 → 第二轮 LLM 调用
                if (
                    self._numeric_tools is not None
                    and hasattr(response, "tool_calls")
                    and response.tool_calls
                ):
                    from packages.ai.numeric import NumericPrincipal

                    principal = NumericPrincipal(
                        user_id=self._actor_id or UUID("00000000-0000-0000-0000-000000000000"),
                        department_id=self._dept_id,
                        roles=(),
                    )

                    # 执行每个 tool call
                    tool_messages: list[dict[str, Any]] = []
                    assistant_tool_calls: list[dict[str, Any]] = []
                    for tc in response.tool_calls:
                        tool_name = tc.get("tool", "") if isinstance(tc, dict) else ""
                        tool_args = tc.get("args", {}) if isinstance(tc, dict) else {}
                        tc_id = (
                            tc.get("id", f"call_{len(tool_messages)}")
                            if isinstance(tc, dict)
                            else f"call_{len(tool_messages)}"
                        )

                        if tool_name == "describe_series":
                            try:
                                result = await self._numeric_tools.describe_series(
                                    tool_args, principal
                                )
                                tool_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tc_id,
                                        "content": _json.dumps(
                                            result.llm_data, ensure_ascii=False, default=str
                                        ),
                                    }
                                )
                                # 拼入摘要供日志参考
                                logger.info("describe_series result: %s", result.summary)
                            except Exception as tool_exc:
                                logger.warning("describe_series failed: %s", tool_exc)
                                tool_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tc_id,
                                        "content": _json.dumps(
                                            {"error": str(tool_exc)}, ensure_ascii=False
                                        ),
                                    }
                                )
                        else:
                            tool_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "content": _json.dumps(
                                        {"error": f"unknown tool: {tool_name}"}, ensure_ascii=False
                                    ),
                                }
                            )

                        assistant_tool_calls.append(
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": _json.dumps(
                                        tool_args, ensure_ascii=False, default=str
                                    ),
                                },
                            }
                        )

                    # 第二轮 LLM 调用：带工具结果生成最终报告
                    if tool_messages:
                        second_response = await self._model_gateway.call(
                            task_type=TaskType.LONG_CONTEXT,
                            system_prompt=analysis_system_prompt,
                            data_context=full_data_text[:256000],
                            research_context=(
                                analysis_context
                                + "\n\n--- 工具调用结果 ---\n"
                                + "\n".join(
                                    f"工具 {m.get('tool_call_id', '')}: {m.get('content', '')}"
                                    for m in tool_messages
                                )
                                + "\n请基于以上工具返回的精确统计结果，生成最终分析报告。"
                            ),
                            tools=None,
                        )
                        analysis_result = (
                            second_response.answer
                            if hasattr(second_response, "answer")
                            else str(second_response)
                        )

                # 清洗 echarts
                import re as _re

                def _clean_echarts_block(match: Any) -> None:
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
                    return "```echarts\n" + block + "\n```"  # type: ignore[return-value]

                analysis_result = _re.sub(
                    r"```echarts\n([\s\S]*?)```",
                    _clean_echarts_block,  # type: ignore[arg-type]
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
                    # P2-C14: 截断到 256K 防止 JSONB 膨胀，完整数据通过 artifact 存储
                    stored_steps[0]["data_context"] = full_data_text[:256000]
                    new_dag = dict(plan.dag_structure)
                    new_dag["steps"] = stored_steps
                    await session.execute(
                        sa.text(
                            "UPDATE research_analysis_plan_version "
                            "SET dag_structure = :dag WHERE id = :pid"
                        ),
                        {"dag": _json.dumps(new_dag, ensure_ascii=False), "pid": str(plan.id)},
                    )
            except (SQLAlchemyError, TypeError, KeyError) as exc:
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
                from packages.research.execution.validation import ThreeSegmentValidator

                # 查找 run
                runs = await ResearchRepositoryTrusted.list_runs(session, workspace_id)
                run_id = runs[0].id if runs else None
                if run_id is None:
                    # 纯 LLM 分析不走沙箱执行，直接创建简化 run 记录
                    from packages.common.ids import new_id as _new_id2

                    run_id = _new_id2()
                    await session.execute(
                        sa.text(
                            "INSERT INTO research_analysis_run "
                            "(id, workspace_id, plan_version_id, snapshot_id, "
                            "run_number, status, submitted_at, image_digest, created_by) "
                            "VALUES (:id, :wid, :pid, :sid, :num, "
                            "'succeeded', now(), 'llm-only', :uid)"
                        ),
                        {
                            "id": str(run_id),
                            "wid": str(workspace_id),
                            "pid": str(plan_id),
                            "sid": str(snapshot_id),
                            "num": 1,
                            "uid": str(self._actor_id or new_id()),
                        },
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
                        except (json.JSONDecodeError, KeyError, AttributeError):
                            logger.warning("Failed to parse echarts block title", exc_info=True)

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
    ) -> dict[str, Any]:
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
        from packages.research.execution.models_trusted import TaskType

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

            # 2. Timeline refactoring: question from Turn, not question version
            research_question = ""
            sub_questions: list[str] = []
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
            except (json.JSONDecodeError, AttributeError, IndexError) as exc:
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
                        # 纯 LLM 分析不走沙箱执行，直接创建简化 run 记录
                        from packages.common.ids import new_id as _new_id3

                        run_id = _new_id3()
                        await session.execute(
                            sa.text(
                                "INSERT INTO research_analysis_run "
                                "(id, workspace_id, plan_version_id, snapshot_id, "
                                "run_number, status, submitted_at, image_digest, "
                                "created_by) "
                                "VALUES (:id, :wid, :pid, :sid, :num, "
                                "'succeeded', now(), 'llm-only', :uid)"
                            ),
                            {
                                "id": str(run_id),
                                "wid": str(workspace_id),
                                "pid": str(plan_id),
                                "sid": str(snapshot_id),
                                "num": 1,
                                "uid": str(self._actor_id or new_id()),
                            },
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
                except (SQLAlchemyError, TypeError, KeyError) as exc:
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
                    from packages.research.execution.validation import ThreeSegmentValidator

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
                                    except (json.JSONDecodeError, KeyError, AttributeError):
                                        logger.warning(
                                            "Failed to parse echarts title in extract_insight",
                                            exc_info=True,
                                        )

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
            except (SQLAlchemyError, TypeError, KeyError) as exc:
                logger.warning("Failed to persist insight: %s", exc)

            return {
                "insight_candidate": insight_candidate,
                "insight_candidate_id": str(insight_candidate_id) if insight_candidate_id else None,
                "run_id": insight_run_id,
            }
