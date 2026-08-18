"""步骤执行 Mixin：按 method 分发执行步骤。

拆分自 orchestrator.py（IRIP 拆分任务）。``StepExecutorMixin`` 承载
_execute_step（按 llm/knowledge 分发）和 _execute_llm_step
（ContextRouter 计算预算 → 超预算分块 → 模型调用 → 归并）。

沙箱执行（python/mixed method）已删除，当前仅支持 LLM 分析。

Timeline refactoring (Task 8): _extract_insight_candidate 已删除。
候选提取改为整轮 Run 完成后由独立 Celery 任务执行
（CandidateExtractionService），不再在步骤内部逐步骤提取。

关键约束：
- 某步失败后依赖步骤停止（skipped），无依赖分支仍可继续；
- LLM 步骤通过 ContextRouter 计算预算，超预算自动分块；
- 每步发布 SSE 事件到 Redis pub/sub。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from packages.research.execution.models_trusted import (
    CoverageDeclaration,
    ErrorClassification,
    StepStatus,
    TaskType,
)
from packages.research.execution.orchestrator_base import (
    ResearchOrchestratorBase,
    logger,
)


class StepExecutorMixin(ResearchOrchestratorBase):
    """步骤执行功能域：_execute_step 及各 method 子执行器。"""

    async def _execute_step(
        self,
        run_id: UUID,
        step_id: UUID | None,
        step_def: dict[str, Any],
        step_map: dict[str, UUID],
        plan: object,
        created_by: UUID | None = None,
    ) -> dict[str, Any] | None:
        """执行单个步骤。

        按 method 分发：
        - llm: ContextRouter 计算预算 → 超预算分块 → 模型调用 → 归并；
        - knowledge: 本期跳过（子项目 5 接入）。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID。
            step_def: 步骤定义（DAG 中的 step dict）。
            step_map: step_key → step_id 映射。
            plan: 计划版本 ORM。

        Returns:
            dict | None: 成功时返回覆盖声明 dict，失败时返回 None。
        """
        step_key = step_def.get("step_key", "unknown")
        method = step_def.get("method", "llm")

        logger.info("Executing step: %s (method=%s)", step_key, method)

        # 更新步骤状态为 running
        if step_id is not None:
            async with self._factory() as session:
                await self._repo.update_step_status(
                    session,
                    step_id,
                    StepStatus.RUNNING.value,
                    started_at=datetime.now(UTC),
                )

        await self._publish_event(
            run_id,
            "step.status_changed",
            {"step_key": step_key, "status": "running"},
        )

        try:
            coverage: dict[str, Any] | None = None

            if method == "llm":
                coverage = await self._execute_llm_step(run_id, step_id, step_def, plan)
            elif method == "knowledge":
                # 本期跳过知识库步骤（子项目 5 接入）
                logger.warning("Knowledge step skipped (not implemented): %s", step_key)
                if step_id is not None:
                    async with self._factory() as session:
                        await self._repo.update_step_status(
                            session,
                            step_id,
                            StepStatus.FAILED.value,
                            error_message="知识库步骤暂未接入",
                            error_classification=ErrorClassification.UNKNOWN.value,
                        )
                return None
            else:
                logger.warning("Unknown method '%s' for step %s", method, step_key)
                return None

            if coverage is not None:
                # 成功
                if step_id is not None:
                    async with self._factory() as session:
                        await self._repo.update_step_status(
                            session,
                            step_id,
                            StepStatus.SUCCEEDED.value,
                            completed_at=datetime.now(UTC),
                        )
                        await self._repo.update_step_progress(
                            session,
                            step_id,
                            analysis_mode=coverage.get("analysis_mode"),
                            coverage_rate=coverage.get("data_coverage_rate"),
                            llm_read_rate=coverage.get("llm_read_rate"),
                            is_sampled=coverage.get("is_sampled"),
                            mode_reason=coverage.get("mode_reason"),
                        )

                await self._publish_event(
                    run_id,
                    "step.status_changed",
                    {
                        "step_key": step_key,
                        "status": "succeeded",
                        "coverage": coverage,
                    },
                )

                # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
                if self._lineage_writer is not None and step_id is not None:
                    try:
                        await self._lineage_writer.on_step_completed(run_id, step_id)
                    except Exception as exc:
                        logger.warning("on_step_completed hook failed: %s", exc)

                return coverage
            else:
                # 失败
                if step_id is not None:
                    async with self._factory() as session:
                        await self._repo.update_step_status(
                            session,
                            step_id,
                            StepStatus.FAILED.value,
                            completed_at=datetime.now(UTC),
                            error_message="步骤执行失败",
                            error_classification=ErrorClassification.UNKNOWN.value,
                        )
                await self._publish_event(
                    run_id,
                    "step.status_changed",
                    {"step_key": step_key, "status": "failed"},
                )
                return None

        except Exception as exc:
            logger.exception("Step execution failed: %s -> %s", step_key, exc)
            if step_id is not None:
                async with self._factory() as session:
                    await self._repo.update_step_status(
                        session,
                        step_id,
                        StepStatus.FAILED.value,
                        completed_at=datetime.now(UTC),
                        error_message=str(exc),
                        error_classification=ErrorClassification.UNKNOWN.value,
                    )
            await self._publish_event(
                run_id,
                "step.status_changed",
                {"step_key": step_key, "status": "failed", "error": str(exc)},
            )
            return None

    async def _execute_llm_step(
        self,
        run_id: UUID,
        step_id: UUID | None,
        step_def: dict[str, Any],
        plan: object,
        python_output: str | None = None,
    ) -> dict[str, Any] | None:
        """执行 LLM 步骤：ContextRouter 计算预算 → 超预算分块 → 模型调用 → 归并。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID。
            step_def: 步骤定义。
            plan: 计划版本 ORM。
            python_output: Python 步骤输出文本（保留参数兼容，当前不会传入）。

        Returns:
            dict | None: 成功时返回覆盖声明，失败时返回 None。
        """
        step_key = step_def.get("step_key", "unknown")
        question = step_def.get("question", "")

        from packages.ai.prompt_store import get_prompt

        # 获取快照数据
        async with self._factory() as session:
            run = await self._repo.get_run(session, run_id)
            if run is None:
                return None
            snapshot_id = run.snapshot_id

        # 准备输入数据
        if python_output is not None:
            data_text = python_output
        else:
            data_text = await self._load_snapshot_data(snapshot_id)

        # 构建步骤定义
        from packages.research.execution.models_trusted import PlanStep as PlanStepDC

        plan_step = PlanStepDC(
            step_key=step_key,
            question=question,
            method=step_def.get("method", "llm"),
            requires_full=step_def.get("requires_full", True),
            per_record_semantic=step_def.get("per_record_semantic", True),
            cross_record_reasoning=step_def.get("cross_record_reasoning", False),
            allows_sampling=step_def.get("allows_sampling", False),
            estimated_tokens=step_def.get("estimated_tokens", 0),
        )

        from packages.research.execution.models_trusted import DataProfile

        data_profile = DataProfile(snapshot_id=snapshot_id)

        # 分析模式选择
        analysis_mode, mode_reason = self._context_router.analyze_step(plan_step, data_profile)

        # 计算预算
        budget = self._context_router.calculate_budget(
            research_context_tokens=plan_step.estimated_tokens,
        )

        # 分块或直接调用
        data_tokens = len(data_text) // 4  # 粗略估算
        if data_tokens > budget:
            # 超预算 → 分块全量扫描
            from packages.research.execution.models_trusted import ChunkStrategy

            chunks = self._context_router.chunk_data(data_text, budget, ChunkStrategy.TOKEN_BUDGET)
            total_chunks = len(chunks)
            successful_chunks = 0
            chunk_responses: list[str] = []

            for chunk in chunks:
                try:
                    response = await self._model_gateway.call(
                        task_type=TaskType.LONG_CONTEXT,
                        system_prompt=get_prompt("llm_step.system_prompt").format(
                            question=question
                        ),
                        data_context=chunk.content,
                        research_context=question,
                    )
                    chunk_responses.append(response.answer)
                    successful_chunks += 1

                    # 发布批次进度
                    await self._publish_event(
                        run_id,
                        "step.progress",
                        {
                            "step_key": step_key,
                            "batch_progress": chunk.index + 1,
                            "batch_count": total_chunks,
                        },
                    )
                except Exception as exc:
                    logger.warning("Chunk %d failed: %s", chunk.index, exc)

            # 计算覆盖率
            coverage = self._context_router.compute_coverage(
                plan_step,
                chunks,
                data_profile.total_records,
                analysis_mode,
                successful_chunks,
            )
            # 保存分块 LLM 回答为工件
            if chunk_responses and self._artifact_service is not None and step_id is not None:
                try:
                    combined = "\n\n---\n\n".join(chunk_responses)
                    await self._artifact_service.collect_artifact(
                        run_id=run_id,
                        step_id=step_id,
                        artifact_type="log",
                        artifact_key=f"{step_key}_output.txt",
                        content=combined.encode("utf-8"),
                        is_publishable=False,
                    )
                except Exception as exc:
                    logger.warning("Failed to save chunked LLM output: %s", exc)
        else:
            # 在预算内 → 直接全量上下文
            try:
                response = await self._model_gateway.call(
                    task_type=TaskType.LONG_CONTEXT,
                    system_prompt=get_prompt("llm_step.system_prompt").format(question=question),
                    data_context=data_text,
                    research_context=question,
                )
                # 保存 LLM 回答为工件
                if self._artifact_service is not None and step_id is not None:
                    try:
                        await self._artifact_service.collect_artifact(
                            run_id=run_id,
                            step_id=step_id,
                            artifact_type="log",
                            artifact_key=f"{step_key}_output.txt",
                            content=response.answer.encode("utf-8"),
                            is_publishable=False,
                        )
                    except Exception as exc:
                        logger.warning("Failed to save LLM output as artifact: %s", exc)
                coverage = CoverageDeclaration(
                    analysis_mode=analysis_mode,
                    data_coverage_rate=1.0,
                    llm_read_rate=1.0,
                    is_sampled=False,
                    mode_reason=mode_reason,
                )
            except Exception as exc:
                logger.warning("LLM step failed: %s", exc)
                return None

        # 更新步骤进度
        if step_id is not None:
            async with self._factory() as session:
                await self._repo.update_step_progress(
                    session,
                    step_id,
                    analysis_mode=coverage.analysis_mode,
                    data_budget_tokens=budget,
                    coverage_rate=coverage.data_coverage_rate,
                    llm_read_rate=coverage.llm_read_rate,
                    is_sampled=coverage.is_sampled,
                    mode_reason=coverage.mode_reason,
                )

        return coverage.to_dict()
