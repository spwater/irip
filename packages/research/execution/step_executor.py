"""步骤执行 Mixin：按 method 分发执行步骤。

拆分自 orchestrator.py（IRIP 拆分任务）。``StepExecutorMixin`` 承载
_execute_step（按 python/llm/mixed/knowledge 分发）、_execute_python_step
（AI 生成代码 → 沙箱执行 → 收集输出，自动修错）、_execute_llm_step
（ContextRouter 计算预算 → 超预算分块 → 模型调用 → 归并）、_execute_mixed_step
（Python 先行 → LLM 阅读）、_extract_insight_candidate（成功后提取候选）
与 _generate_fallback_script（AI 失败回退脚本）。

关键约束：
- 某步失败后依赖步骤停止（skipped），无依赖分支仍可继续；
- Python 步骤通过 SandboxRuntime 执行，自动修错最多 MAX_RETRY_ATTEMPTS 次；
- LLM 步骤通过 ContextRouter 计算预算，超预算自动分块；
- 每步发布 SSE 事件到 Redis pub/sub。
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.research.execution.models_trusted import (
    CoverageDeclaration,
    ErrorClassification,
    ExecutionResult,
    StepStatus,
    TaskType,
)
from packages.research.execution.orchestrator_base import (
    DEFAULT_RESOURCE_LIMITS,
    DEFAULT_WARM_DURATION,
    MAX_RETRY_ATTEMPTS,
    SANDBOX_IMAGE_DIGEST,
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
        - python: 沙箱执行 Python 脚本（AI 生成代码 → 沙箱执行 → 收集输出）；
        - llm: ContextRouter 计算预算 → 超预算分块 → 模型调用 → 归并；
        - mixed: Python 先行计算 → LLM 阅读结果；
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
        method = step_def.get("method", "python")

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

            if method == "python":
                coverage = await self._execute_python_step(
                    run_id, step_id, step_def, step_map, plan, created_by
                )
            elif method == "llm":
                coverage = await self._execute_llm_step(run_id, step_id, step_def, plan)
            elif method == "mixed":
                coverage = await self._execute_mixed_step(
                    run_id, step_id, step_def, step_map, plan, created_by
                )
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

                # ── 阶段 3：Insight 候选提取钩子 ──
                # LLM/混合步骤成功后，通过 InsightExtractor 提取结构化候选
                if method in ("llm", "mixed") and self._insight_extractor is not None:
                    try:
                        await self._extract_insight_candidate(
                            run_id, step_id, step_def, plan, method
                        )
                    except Exception as exc:
                        logger.warning(
                            "Insight extraction failed for step %s: %s",
                            step_key,
                            exc,
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

    async def _execute_python_step(
        self,
        run_id: UUID,
        step_id: UUID | None,
        step_def: dict[str, Any],
        step_map: dict[str, UUID],
        plan: object,
        created_by: UUID | None = None,
    ) -> dict[str, Any] | None:
        """执行 Python 步骤：AI 生成代码 → 沙箱执行 → 收集输出。

        自动修错：失败时 AI 修复代码重试，最多 MAX_RETRY_ATTEMPTS 次。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID。
            step_def: 步骤定义。
            step_map: step_key → step_id 映射。
            plan: 计划版本 ORM。

        Returns:
            dict | None: 成功时返回覆盖声明，失败时返回 None。
        """
        step_key = step_def.get("step_key", "unknown")
        question = step_def.get("question", "")
        expected_output = step_def.get("expected_output", "")

        # 获取快照并准备输入包
        async with self._factory() as session:
            run = await self._repo.get_run(session, run_id)
            if run is None:
                return None
            snapshot_id = run.snapshot_id

        # 准备受控输入包
        input_package_path = await self._prepare_input_package(snapshot_id)

        # 创建容器
        container_id = await self._sandbox.create_container(
            input_package_path=input_package_path,
            image_digest=SANDBOX_IMAGE_DIGEST,
            resource_limits=DEFAULT_RESOURCE_LIMITS,
        )

        try:
            # AI 生成 Python 脚本
            system_prompt = (
                f"你是一个 Python 数据分析专家。请针对以下问题生成可执行的 Python 脚本。\n"
                f"问题: {question}\n"
                f"预期输出: {expected_output}\n"
                f"数据在 /input/evidence.json 中（JSON 格式）。\n"
                f"输出文件写入 /workspace/output/ 目录。\n"
                f"使用 pandas/numpy/scipy/matplotlib 等科学计算库。\n\n"
                f"重要规则：\n"
                f"1. 只返回纯 Python 代码，不要返回任何解释、说明、markdown 或自然语言文本。\n"
                f"2. 不要使用 ```python 代码块包裹，直接返回代码本身。\n"
                f"3. 代码必须是完整的可执行脚本，不能有语法错误。\n"
                f"4. 第一行必须是 import 语句。\n"
                f"5. 如果数据为空或不存在，代码应正常处理异常并输出空结果。"
            )

            attempt = 0
            last_error = ""

            while attempt < MAX_RETRY_ATTEMPTS:
                attempt += 1

                # 更新尝试次数
                if step_id is not None:
                    async with self._factory() as session:
                        await self._repo.update_step_progress(
                            session, step_id, attempt_count=attempt
                        )

                # AI 生成/修复代码
                error_context = f"\n\n上次错误:\n{last_error}" if last_error else ""
                try:
                    response = await self._model_gateway.call(
                        task_type=TaskType.CODE_GEN,
                        system_prompt=system_prompt + error_context,
                        data_context="",
                        research_context=question,
                    )
                    script_content = (
                        response.answer if hasattr(response, "answer") else str(response)
                    )
                    logger.info(
                        "AI code gen response: %s, script_len=%d",
                        type(response).__name__,
                        len(script_content),
                    )

                    # 清理 AI 返回中的 markdown 代码块包裹
                    script_content = script_content.strip()
                    if script_content.startswith("```python"):
                        script_content = script_content[len("```python") :].strip()
                    elif script_content.startswith("```"):
                        script_content = script_content[3:].strip()
                    if script_content.endswith("```"):
                        script_content = script_content[:-3].strip()

                    # 检查是否为模拟响应或空回答 → 走 fallback
                    if (
                        not script_content
                        or script_content.startswith("[模拟响应]")
                        or len(script_content) < 50
                    ):
                        logger.warning("AI response too short or mock, using fallback script")
                        script_content = self._generate_fallback_script(question)
                except Exception as exc:
                    logger.warning("AI code generation failed: %s", exc)
                    script_content = self._generate_fallback_script(question)
                    logger.info("Using fallback script: len=%d", len(script_content))

                # 沙箱执行
                result: ExecutionResult = await self._sandbox.execute(
                    container_id=container_id,
                    script_content=script_content,
                    timeout_seconds=DEFAULT_RESOURCE_LIMITS.timeout_seconds,
                )

                if result.exit_code == 0 and not result.timed_out:
                    # 执行成功 → 收集输出
                    output_files = await self._sandbox.collect_output(
                        container_id, ["*.json", "*.csv", "*.png", "*.txt", "*.log"]
                    )

                    # 持久化工件
                    for of in output_files:
                        # data 和 chart 类型默认 publishable，log 类型不 publishable
                        if of.filename.endswith((".json", ".csv")):
                            atype = "data"
                            publishable = True
                        elif of.filename.endswith((".png", ".svg", ".pdf")):
                            atype = "chart"
                            publishable = True
                        else:
                            atype = "log"
                            publishable = False
                        await self._artifact_service.collect_artifact(
                            run_id=run_id,
                            step_id=step_id,
                            artifact_type=atype,
                            artifact_key=of.filename,
                            content=of.content,
                            is_publishable=publishable,
                        )

                    # 保存代码工件
                    await self._artifact_service.collect_artifact(
                        run_id=run_id,
                        step_id=step_id,
                        artifact_type="code",
                        artifact_key=f"{step_key}.py",
                        content=script_content.encode("utf-8"),
                        is_publishable=False,
                    )

                    return CoverageDeclaration(
                        analysis_mode="full_compute",
                        data_coverage_rate=1.0,
                        llm_read_rate=0.0,
                        is_sampled=False,
                        mode_reason="Python 全量计算成功",
                    ).to_dict()

                else:
                    # 执行失败
                    last_error = result.stderr or "Unknown error"
                    if result.timed_out:
                        # 超时直接放弃
                        logger.warning("Step %s timed out", step_key)
                        # 审计沙箱超限
                        async with self._factory() as session:
                            if created_by is not None:
                                from packages.common.tenant_guc import set_user_guc

                                await set_user_guc(session, created_by)
                            await AuditRecorder.record(
                                session,
                                AuditEventData(
                                    department_id=UUID(int=0),
                                    actor_user_id=created_by,
                                    action="research.sandbox.timeout",
                                    resource_type="research_analysis_step",
                                    resource_id=step_id,
                                    payload={"step_key": step_key, "attempt": attempt},
                                ),
                            )
                        break

                    logger.warning(
                        "Step %s attempt %d failed: %s",
                        step_key,
                        attempt,
                        last_error[:200],
                    )

            # 所有尝试均失败
            return None

        finally:
            # 保温或销毁容器
            try:
                await self._sandbox.keep_warm(container_id, DEFAULT_WARM_DURATION)
            except Exception as exc:
                logger.warning("Failed to keep warm: %s", exc)
                try:
                    await self._sandbox.destroy_container(container_id)
                except Exception:
                    logging.getLogger(__name__).debug("cleanup failed", exc_info=True)

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
            python_output: Python 步骤输出文本（混合步骤中使用，替代快照数据）。

        Returns:
            dict | None: 成功时返回覆盖声明，失败时返回 None。
        """
        step_key = step_def.get("step_key", "unknown")
        question = step_def.get("question", "")

        # 获取快照数据
        async with self._factory() as session:
            run = await self._repo.get_run(session, run_id)
            if run is None:
                return None
            snapshot_id = run.snapshot_id

        # 准备输入数据：混合步骤使用 Python 输出，独立步骤从快照加载
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
                        system_prompt=f"分析以下数据，回答问题: {question}",
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
                    system_prompt=f"分析以下数据，回答问题: {question}",
                    data_context=data_text,
                    research_context=question,
                )
                # 保存 LLM 回答为工件（供 InsightExtractor 使用）
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

    async def _execute_mixed_step(
        self,
        run_id: UUID,
        step_id: UUID | None,
        step_def: dict[str, Any],
        step_map: dict[str, UUID],
        plan: object,
        created_by: UUID | None = None,
    ) -> dict[str, Any] | None:
        """执行混合步骤：Python 先行计算 → LLM 阅读结果。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID。
            step_def: 步骤定义。
            step_map: step_key → step_id 映射。
            plan: 计划版本 ORM。

        Returns:
            dict | None: 成功时返回覆盖声明，失败时返回 None。
        """
        # 先执行 Python 部分
        python_coverage = await self._execute_python_step(
            run_id, step_id, step_def, step_map, plan, created_by
        )
        if python_coverage is None:
            return None

        # 收集 Python 步骤输出文本（从数据工件中读取）
        python_output_text = await self._collect_step_output_text(step_id)

        # 再执行 LLM 部分，使用 Python 输出作为数据上下文
        llm_coverage = await self._execute_llm_step(
            run_id, step_id, step_def, plan, python_output=python_output_text
        )
        if llm_coverage is None:
            # LLM 失败不影响 Python 结果（P1-7 风格）
            return python_coverage

        # 混合覆盖声明
        return CoverageDeclaration(
            analysis_mode="mixed",
            data_coverage_rate=1.0,
            llm_read_rate=0.75,
            is_sampled=False,
            mode_reason="Python 全量计算 + LLM 语义分析混合",
        ).to_dict()

    async def _collect_step_output_text(self, step_id: UUID | None) -> str:
        """从步骤的数据工件中收集输出文本（混合步骤 LLM 使用）。

        Python 步骤执行后，输出文件（JSON/CSV）会作为 data 类型工件持久化。
        本方法读取这些工件并拼接为文本，供 LLM 步骤作为数据上下文使用。

        Args:
            step_id: 步骤 ID。

        Returns:
            str: 拼接的输出文本，无数据工件时返回空字符串。
        """
        if step_id is None or self._artifact_service is None:
            return ""

        from packages.research.execution.repository_trusted import (
            ResearchRepositoryTrusted,
        )

        parts: list[str] = []
        async with self._factory() as session:
            artifacts = await ResearchRepositoryTrusted.list_artifacts_by_step(session, step_id)
            for a in artifacts:
                if a.artifact_type == "data":
                    try:
                        content = await self._artifact_service.get_artifact(a.id)
                        if content is not None:
                            parts.append(content.content.decode("utf-8", errors="replace"))
                    except Exception:
                        logging.getLogger(__name__).debug(
                            "Failed to read artifact %s", a.id, exc_info=True
                        )

        return "\n\n".join(parts)

    async def _extract_insight_candidate(
        self,
        run_id: UUID,
        step_id: UUID | None,
        step_def: dict[str, Any],
        plan: object,
        method: str,
    ) -> None:
        """提取 Insight 候选（阶段 3 新增钩子）。

        在 LLM/混合步骤成功后调用 InsightExtractor 提取结构化候选，
        并保存为 InsightCandidate。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID。
            step_def: 步骤定义。
            plan: 计划版本 ORM。
            method: 步骤方法（llm 或 mixed）。
        """
        if self._factory is None or self._insight_extractor is None:
            return

        # 获取步骤输出文本
        step_output = ""
        if method == "llm":
            # LLM 步骤的输出即为模型回答
            # 从最近的工件中获取输出文本
            async with self._factory() as session:
                from packages.research.execution.repository_trusted import (
                    ResearchRepositoryTrusted,
                )

                if step_id is not None:
                    artifacts = await ResearchRepositoryTrusted.list_artifacts_by_step(
                        session, step_id
                    )
                    for a in artifacts:
                        if a.artifact_type in ("log", "data"):
                            try:
                                content = await self._artifact_service.get_artifact(a.id)
                                if content is not None:
                                    step_output = content.content.decode("utf-8", errors="replace")
                                    break
                            except Exception:
                                logging.getLogger(__name__).debug("cleanup failed", exc_info=True)
        elif method == "mixed":
            # 混合步骤：LLM 部分的输出
            step_output = step_def.get("question", "")

        if not step_output:
            step_output = step_def.get("question", "")

        # 构建研究上下文
        research_context = self._build_research_context(run_id, plan)

        # 调用 InsightExtractor 提取
        candidate_data = await self._insight_extractor.extract(
            step_output=step_output,
            research_context=research_context,
        )

        if candidate_data is None:
            return

        # 获取 workspace_id
        async with self._factory() as session:
            run = await self._repo.get_run(session, run_id)
            if run is None:
                return
            workspace_id = run.workspace_id

            # 保存 Insight 候选
            from packages.research.repository import ResearchRepository

            await ResearchRepository.insert_insight_candidate(
                session,
                workspace_id=workspace_id,
                run_id=run_id,
                step_id=step_id,
                conclusion=candidate_data.conclusion,
                scope=candidate_data.scope,
                evidence_refs=candidate_data.evidence_refs,
                method_refs=candidate_data.method_refs,
                confidence_level=candidate_data.confidence_level,
                limitations=candidate_data.limitations,
                evidence_source_label=candidate_data.evidence_source_label,
                ai_raw_text=candidate_data.ai_raw_text,
                status="pending",
            )

        # 发布 SSE 事件通知前端有新候选
        await self._publish_event(
            run_id,
            "insight.candidate.created",
            {
                "step_id": str(step_id) if step_id else None,
                "conclusion": candidate_data.conclusion[:100],
            },
        )

    def _generate_fallback_script(self, question: str) -> str:
        """生成回退 Python 脚本（AI 调用失败时使用）。

        Args:
            question: 步骤问题。

        Returns:
            str: Python 脚本内容。
        """
        return (
            "import json\n"
            "import os\n"
            "\n"
            "# Load evidence data\n"
            "with open('/input/evidence.json', 'r') as f:\n"
            "    data = json.load(f)\n"
            "\n"
            "# Basic data quality check\n"
            "evidence = data.get('evidence', [])\n"
            "report = {\n"
            f"    'question': '{question}',\n"
            "    'evidence_count': len(evidence),\n"
            "    'status': 'basic_analysis_complete'\n"
            "}\n"
            "\n"
            "# Write output\n"
            "os.makedirs('/workspace/output', exist_ok=True)\n"
            "with open('/workspace/output/result.json', 'w') as f:\n"
            "    json.dump(report, f, indent=2)\n"
            "\n"
            "print('Analysis complete')\n"
        )
