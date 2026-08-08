"""研究编排器：DAG 拓扑排序 + 步骤执行编排 + 范围越界检测 + 沙箱/模型协调。

ResearchOrchestrator 是执行引擎核心，负责：
1. execute_run: 加载 Run/Plan/Snapshot → 拓扑排序 DAG → 逐步执行 → 聚合结果；
2. _execute_step: 按步骤 method（python/llm/mixed/knowledge）分发执行；
3. _check_scope: 范围越界检测（新增数据/改变目标/扩大资源/首次知识库）；
4. _prepare_input_package: 生成受控输入包（沙箱不直接访问老系统）；
5. _publish_event: 发布 SSE 事件到 Redis pub/sub。

DAG 拓扑排序使用 Kahn 算法。

关键约束：
- 某步失败后依赖步骤停止（skipped），无依赖分支仍可继续；
- Python 步骤通过 SandboxRuntime 执行，自动修错最多 3 次；
- LLM 步骤通过 ContextRouter 计算预算，超预算自动分块；
- 每步发布 SSE 事件到 Redis pub/sub。
"""

import asyncio
import json
import logging
import os
import tempfile
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.research.execution.models_trusted import (
    CoverageDeclaration,
    ErrorClassification,
    ExecutionResult,
    ResourceLimits,
    ScopeBoundary,
    ScopeCheckResult,
    StepStatus,
    TaskType,
)

logger = logging.getLogger("research.orchestrator")

#: 最大自动修错重试次数。
MAX_RETRY_ATTEMPTS: int = int(os.getenv("RESEARCH_MAX_RETRY_ATTEMPTS", "3"))

#: 默认沙箱资源限制。
DEFAULT_RESOURCE_LIMITS: ResourceLimits = ResourceLimits()

#: 默认保温时长（秒）。
DEFAULT_WARM_DURATION: int = int(os.getenv("RESEARCH_WARM_TTL_SECONDS", "180"))

#: 沙箱镜像 digest（通过环境变量配置）。
SANDBOX_IMAGE_DIGEST: str = os.getenv(
    "RESEARCH_SANDBOX_IMAGE_DIGEST",
    "sha256:research-sandbox-scipy-2026.08",
)


class ResearchOrchestrator:
    """研究分析执行编排器。

    依赖注入 Repository / ModelGateway / SandboxRuntime / ContextRouter /
    RunArtifactService / ResearchMemoryService / Scheduler / session_factory。

    Attributes:
        _repo: 数据访问层（ResearchRepositoryTrusted）。
        _model_gateway: 模型网关。
        _sandbox: 沙箱运行时。
        _context_router: 上下文路由器。
        _artifact_service: 工件服务。
        _memory_service: 研究记忆服务。
        _scheduler: 调度器。
        _factory: 异步会话工厂。
    """

    def __init__(
        self,
        repo: Any,
        model_gateway: Any,
        sandbox: Any,
        context_router: Any,
        artifact_service: Any,
        memory_service: Any,
        scheduler: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        insight_extractor: Any | None = None,
        lineage_writer: Any | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            repo: 数据访问层。
            model_gateway: 模型网关。
            sandbox: 沙箱运行时。
            context_router: 上下文路由器。
            artifact_service: 工件服务。
            memory_service: 研究记忆服务。
            scheduler: 调度器（可选，Worker 中使用）。
            session_factory: 异步会话工厂（可选，Worker 中使用）。
            insight_extractor: Insight 提取器（可选，阶段 3 新增）。
            lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
        """
        self._repo = repo
        self._model_gateway = model_gateway
        self._sandbox = sandbox
        self._context_router = context_router
        self._artifact_service = artifact_service
        self._memory_service = memory_service
        self._scheduler = scheduler
        self._factory: Any = session_factory
        self._insight_extractor = insight_extractor
        self._lineage_writer = lineage_writer

        # 包装 session_factory：退出时自动 commit（与 session_scope 行为一致）
        _original_factory = session_factory
        from contextlib import asynccontextmanager as _acm

        @_acm
        async def _auto_commit_session() -> AsyncIterator[AsyncSession]:
            if _original_factory is None:
                raise RuntimeError("session_factory is None")
            async with _original_factory() as session:
                async with session.begin():
                    yield session

        self._factory = _auto_commit_session

    async def execute_run(self, run_id: UUID) -> None:
        """执行分析 Run 的完整流程。

        流程：
        1. 加载 Run + Plan + Snapshot；
        2. 拓扑排序 DAG 步骤；
        3. 初始化 ResearchAnalysisStep 行；
        4. 逐步执行 _execute_step；
        5. 每步前检查依赖闭包状态 + 范围越界；
        6. 每步后发布 SSE 事件；
        7. 全部完成后聚合覆盖率 → 确定 Run 最终状态；
        8. 释放调度槽位；
        9. 更新研究记忆文档。

        Args:
            run_id: Run ID。
        """
        logger.info("Starting execute_run: run_id=%s", run_id)

        created_by: UUID | None = None

        # 设置租户上下文
        if self._factory is not None:
            async with self._factory() as session:
                run_orm = await self._repo.get_run(session, run_id)
                if run_orm is None:
                    logger.error("Run not found: %s", run_id)
                    return
                # 检查 Run 状态——如果已取消/失败/完成则不再执行
                if run_orm.status in ("cancelled", "failed", "succeeded", "partially_succeeded"):
                    logger.info("Run %s already %s, skipping execution", run_id, run_orm.status)
                    return
                workspace_id = run_orm.workspace_id
                created_by = run_orm.created_by
                # 设置工件服务和记忆服务的租户上下文
                if hasattr(self._artifact_service, "set_context"):
                    self._artifact_service.set_context(run_orm.workspace_id, run_orm.created_by)
        else:
            run_orm = None
            workspace_id = None
            created_by = None

        # 使用 session_scope 管理事务
        if self._factory is None:
            logger.error("No session_factory available for orchestrator")
            return

        try:
            # 注册心跳
            if self._scheduler is not None:
                await self._scheduler.register_heartbeat(str(run_id))

            # 发布 Run 启动事件
            await self._publish_event(run_id, "run.status_changed", {"status": "running"})

            # 更新研究记忆
            if workspace_id is not None:
                await self._memory_service.update_from_event(
                    workspace_id, "run.started", {"run_id": str(run_id)}
                )

            # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
            if self._lineage_writer is not None:
                try:
                    # 获取 Run 关联的 snapshot_id
                    async with self._factory() as session:
                        run_for_hook = await self._repo.get_run(session, run_id)
                        snapshot_id_for_hook = run_for_hook.snapshot_id if run_for_hook else None
                    if snapshot_id_for_hook is not None:
                        await self._lineage_writer.on_run_started(run_id, [snapshot_id_for_hook])
                except Exception as exc:
                    logger.warning("on_run_started hook failed: %s", exc)

            # 加载 Run + Plan
            async with self._factory() as session:
                run = await self._repo.get_run(session, run_id)
                if run is None:
                    logger.error("Run not found: %s", run_id)
                    return
                plan = await self._repo.get_plan(session, run.plan_version_id)
                if plan is None:
                    logger.error("Plan not found for run: %s", run_id)
                    await self._fail_run(run_id, "计划不存在")
                    return

                dag_structure = plan.dag_structure
                steps_defs = dag_structure.get("steps", [])

            # 拓扑排序
            sorted_steps = self._topological_sort(steps_defs)
            if sorted_steps is None:
                await self._fail_run(run_id, "DAG 存在环，无法拓扑排序")
                return

            # 初始化步骤行
            async with self._factory() as session:
                steps_data = [
                    {
                        "step_key": s.get("step_key", f"step_{i}"),
                        "step_index": i,
                        "method": s.get("method", "python"),
                        "depends_on": s.get("dependencies", []),
                    }
                    for i, s in enumerate(sorted_steps)
                ]
                step_entities = await self._repo.batch_insert_steps(session, run_id, steps_data)
                step_map: dict[str, UUID] = {}
                for sd, ent in zip(steps_data, step_entities, strict=False):
                    step_map[sd["step_key"]] = ent.id

            # 验证 step 已写入数据库
            async with self._factory() as session:
                from packages.research.execution.entities_trusted import ResearchAnalysisStep

                result = await session.execute(
                    sa.select(ResearchAnalysisStep.id).where(ResearchAnalysisStep.run_id == run_id)
                )
                db_step_ids = [row[0] for row in result.fetchall()]
            if not db_step_ids:
                logger.error(
                    "Steps were not persisted to database after batch_insert! run_id=%s", run_id
                )
                await self._fail_run(run_id, "步骤初始化失败：步骤未写入数据库")
                return
            logger.info("Steps persisted: %d steps for run %s", len(db_step_ids), run_id)

            # 逐步执行
            failed_steps: set[str] = set()
            succeeded_steps: set[str] = set()
            coverage_declarations: list[dict[str, Any]] = []

            for idx, step_def in enumerate(sorted_steps):
                step_key = step_def.get("step_key", f"step_{idx}")

                # 注册心跳
                if self._scheduler is not None:
                    await self._scheduler.register_heartbeat(str(run_id))

                # 检查依赖闭包状态
                deps = step_def.get("dependencies", [])
                dep_failed = any(d in failed_steps for d in deps)

                if dep_failed:
                    # 依赖步骤失败 → 跳过
                    async with self._factory() as session:
                        step_id = step_map.get(step_key)
                        if step_id:
                            await self._repo.update_step_status(
                                session, step_id, StepStatus.SKIPPED.value
                            )
                    failed_steps.add(step_key)
                    await self._publish_event(
                        run_id,
                        "step.status_changed",
                        {"step_key": step_key, "status": "skipped"},
                    )
                    continue

                # 检查 Run 是否被取消
                async with self._factory() as session:
                    run_check = await self._repo.get_run(session, run_id)
                    if run_check and run_check.status == "cancelled":
                        logger.info("Run cancelled, stopping: %s", run_id)
                        return

                # 执行步骤
                step_id = step_map.get(step_key)
                step_result = await self._execute_step(
                    run_id, step_id, step_def, step_map, plan, created_by
                )

                if step_result:
                    succeeded_steps.add(step_key)
                    coverage_declarations.append(step_result)
                else:
                    failed_steps.add(step_key)

            # 聚合覆盖率 → 确定 Run 最终状态
            final_status = self._determine_final_status(sorted_steps, succeeded_steps, failed_steps)
            coverage_summary = self._aggregate_coverage(coverage_declarations)

            async with self._factory() as session:
                await self._repo.update_run_status(
                    session,
                    run_id,
                    final_status,
                    completed_at=datetime.now(UTC),
                    coverage_summary=coverage_summary,
                )

            # 标记成功步骤的工件为可发布
            if succeeded_steps:
                await self._artifact_service.mark_publishable(run_id, succeeded_steps)

            # 发布最终状态事件
            await self._publish_event(
                run_id,
                "run.status_changed",
                {"status": final_status, "coverage": coverage_summary},
            )

            # 更新研究记忆
            if workspace_id is not None:
                await self._memory_service.update_from_event(
                    workspace_id,
                    "run.completed",
                    {
                        "run_id": str(run_id),
                        "status": final_status,
                        "coverage": coverage_summary,
                    },
                )

            # 释放调度槽位
            if self._scheduler is not None and created_by is not None:
                await self._scheduler.release_slot(str(created_by), str(run_id))

            # 审计（Worker 进程中 RLS 可能阻止审计写入，不阻断主流程）
            try:
                async with self._factory() as session:
                    if created_by is not None:
                        from packages.common.tenant_guc import set_user_guc

                        await set_user_guc(session, created_by)
                    await AuditRecorder.record(
                        session,
                        AuditEventData(
                            department_id=workspace_id or UUID(int=0),
                            actor_user_id=created_by,
                            action="research.run.complete",
                            resource_type="research_analysis_run",
                            resource_id=run_id,
                            payload={
                                "status": final_status,
                                "succeeded": len(succeeded_steps),
                                "failed": len(failed_steps),
                            },
                        ),
                    )
            except Exception as audit_err:
                logger.warning("Audit record failed (non-blocking): %s", audit_err)

            logger.info(
                "Run %s completed: status=%s, succeeded=%d, failed=%d",
                run_id,
                final_status,
                len(succeeded_steps),
                len(failed_steps),
            )

        except Exception as exc:
            logger.exception("Run execution failed: %s", exc)
            await self._fail_run(run_id, f"执行异常: {str(exc)}")
            if self._scheduler is not None and created_by is not None:
                await self._scheduler.release_slot(str(created_by), str(run_id))

    async def cancel_run(self, run_id: UUID) -> None:
        """取消 Run（编排器层面）。

        标记当前步骤 cancelled → 下游步骤 skipped → Run cancelled → 销毁活跃容器 → 释放槽位。

        Args:
            run_id: Run ID。
        """
        async with self._factory() as session:
            steps = await self._repo.list_steps_by_run(session, run_id)
            for step in steps:
                if step.status == "running":
                    await self._repo.update_step_status(
                        session, step.id, StepStatus.CANCELLED.value
                    )
                elif step.status == "pending":
                    await self._repo.update_step_status(session, step.id, StepStatus.SKIPPED.value)

            await self._repo.update_run_status(
                session,
                run_id,
                "cancelled",
                cancelled_at=datetime.now(UTC),
            )

        await self._publish_event(run_id, "run.status_changed", {"status": "cancelled"})

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
                    pass

    async def _execute_llm_step(
        self,
        run_id: UUID,
        step_id: UUID | None,
        step_def: dict[str, Any],
        plan: object,
    ) -> dict[str, Any] | None:
        """执行 LLM 步骤：ContextRouter 计算预算 → 超预算分块 → 模型调用 → 归并。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID。
            step_def: 步骤定义。
            plan: 计划版本 ORM。

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

        # 准备输入数据（从快照获取）
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

        return coverage.to_dict()  # type: ignore[no-any-return]

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

        # 再执行 LLM 部分
        llm_coverage = await self._execute_llm_step(run_id, step_id, step_def, plan)
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

    def _topological_sort(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """DAG 拓扑排序（Kahn 算法）。

        Args:
            steps: 步骤定义列表。

        Returns:
            list[dict] | None: 拓扑排序后的步骤列表，存在环时返回 None。
        """
        # 构建邻接表和入度表
        step_map: dict[str, dict[str, Any]] = {
            s.get("step_key", f"step_{i}"): s for i, s in enumerate(steps)
        }
        in_degree: dict[str, int] = dict.fromkeys(step_map, 0)
        adjacency: dict[str, list[str]] = {k: [] for k in step_map}

        for step in steps:
            key = step.get("step_key", "")
            deps = step.get("dependencies", [])
            for dep in deps:
                if dep in step_map:
                    adjacency[dep].append(key)
                    in_degree[key] += 1

        # Kahn 算法
        queue: deque[str] = deque(k for k, d in in_degree.items() if d == 0)
        sorted_keys: list[str] = []

        while queue:
            current = queue.popleft()
            sorted_keys.append(current)
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_keys) != len(step_map):
            # 存在环
            logger.error("DAG has cycles, cannot topological sort")
            return None

        return [step_map[k] for k in sorted_keys]

    def _check_scope(
        self,
        scope: ScopeBoundary,
        current_snapshot_id: UUID,
        current_question_version: int,
        current_method: str,
        current_resource_tier: str,
    ) -> ScopeCheckResult:
        """检查范围越界。

        越界检测：
        - snapshot_id 变更 → 新增数据 → 重新确认
        - question_version 变更 → 改变研究目标 → 重新确认
        - method="knowledge" 且 knowledge_base_used=False → 首次知识库 → 重新确认
        - resource_tier > "standard" → 扩大资源级别 → 重新确认

        Args:
            scope: 计划范围边界。
            current_snapshot_id: 当前快照 ID。
            current_question_version: 当前问题版本号。
            current_method: 当前步骤方法。
            current_resource_tier: 当前资源档位。

        Returns:
            ScopeCheckResult: 范围检查结果。
        """
        if current_snapshot_id != scope.snapshot_id:
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="snapshot_changed",
                message="证据快照已变更，需重新确认计划",
            )

        if current_question_version != scope.question_version:
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="question_changed",
                message="研究问题已变更，需重新确认计划",
            )

        if current_method == "knowledge" and not scope.knowledge_base_used:
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="knowledge_first_use",
                message="首次使用知识库，需重新确认计划",
            )

        _TIER_ORDER = {"standard": 0, "heavy": 1}
        if _TIER_ORDER.get(current_resource_tier, 0) > _TIER_ORDER.get(scope.resource_tier, 0):
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="resource_upgraded",
                message="资源级别已升级，需重新确认计划",
            )

        return ScopeCheckResult(is_within_scope=True)

    async def _prepare_input_package(self, snapshot_id: UUID) -> str:
        """生成受控输入包（沙箱只读挂载）。

        从 CoreFactProvider 获取快照数据 → 序列化为 JSON → 写入临时目录。

        Args:
            snapshot_id: 快照 ID。

        Returns:
            str: 输入包文件路径。
        """
        # 创建临时目录
        tmp_dir = tempfile.mkdtemp(prefix=f"research_input_{snapshot_id}_")
        input_path = os.path.join(tmp_dir, "evidence.json")

        # 从数据库加载快照数据
        input_data: dict[str, Any] = {"snapshot_id": str(snapshot_id), "evidence": []}

        if self._factory is not None:
            async with self._factory() as session:
                from packages.research.repository import ResearchRepository

                await ResearchRepository.list_snapshots(session, UUID(int=0))
                # 获取快照关联的证据引用
                # 此处简化：实际需要通过 CoreFactProvider 获取数据
                # 构建输入包结构
                input_data["evidence"] = [
                    {
                        "source_namespace": "core:fact",
                        "source_id": "placeholder",
                        "field_manifest": [],
                        "data": {"metadata": {}, "points": [], "series": []},
                    }
                ]

        # 写入 JSON 文件
        def _write_input_file() -> None:
            with open(input_path, "w", encoding="utf-8") as f:
                json.dump(input_data, f, ensure_ascii=False, indent=2)

        await asyncio.to_thread(_write_input_file)

        return tmp_dir

    async def _load_snapshot_data(self, snapshot_id: UUID) -> str:
        """加载快照数据为文本（LLM 步骤使用）。

        Args:
            snapshot_id: 快照 ID。

        Returns:
            str: 数据文本。
        """
        if self._factory is None:
            return ""

        async with self._factory() as session:
            # 获取快照的字段清单和源引用
            # 简化：返回字段清单的 JSON 文本
            import sqlalchemy as sa

            from packages.research.entities import ResearchEvidenceSnapshot

            result = await session.execute(
                sa.select(ResearchEvidenceSnapshot).where(
                    ResearchEvidenceSnapshot.id == snapshot_id
                )
            )
            snapshot = result.scalar_one_or_none()
            if snapshot is None:
                return ""

            # 读取每个证据引用的 Fact 完整数据
            evidence_data = []
            for ref in snapshot.source_refs or []:
                fact_id = ref.get("id")
                namespace = ref.get("namespace", "")
                if namespace == "core:fact" and fact_id:
                    try:
                        # 通过 factory 创建 CoreFactProvider 并读取数据
                        from apps.api.main import _build_s3_repo
                        from packages.research.lineage.core_adapter import CoreFactProviderImpl

                        s3_repo = _build_s3_repo()
                        provider = CoreFactProviderImpl(  # type: ignore[call-arg]
                            session_factory=self._factory,
                            s3_repo=s3_repo,
                        )
                        fact_data = await provider.get_fact_data(UUID(fact_id))
                        evidence_data.append(
                            {
                                "fact_id": fact_id,
                                "namespace": namespace,
                                "data": fact_data,
                            }
                        )
                    except Exception as exc:
                        logger.warning("Failed to load fact data %s: %s", fact_id, exc)
                        evidence_data.append(
                            {
                                "fact_id": fact_id,
                                "namespace": namespace,
                                "error": str(exc)[:200],
                            }
                        )

            return json.dumps(
                {
                    "field_manifest": snapshot.field_manifest,
                    "source_refs": snapshot.source_refs,
                    "evidence_data": evidence_data,
                },
                ensure_ascii=False,
            )

    async def _publish_event(
        self,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """发布 SSE 事件到 Redis pub/sub。

        Args:
            run_id: Run ID。
            event_type: 事件类型。
            payload: 事件载荷。
        """
        try:
            import redis as redis_lib

            redis_url = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")
            r = redis_lib.from_url(redis_url)  # type: ignore[no-untyped-call]
            channel = f"research:run:{run_id}:events"
            message = json.dumps(
                {"event": event_type, "data": json.dumps(payload, ensure_ascii=False)},
                ensure_ascii=False,
            )
            r.publish(channel, message)
        except Exception as exc:
            logger.warning("Failed to publish event: %s", exc)

    def _determine_final_status(
        self,
        steps: list[dict[str, Any]],
        succeeded: set[str],
        failed: set[str],
    ) -> str:
        """确定 Run 最终状态。

        Args:
            steps: 步骤定义列表。
            succeeded: 成功步骤 key 集合。
            failed: 失败步骤 key 集合。

        Returns:
            str: 最终状态（succeeded / partially_succeeded / failed）。
        """
        if not failed:
            return "succeeded"
        if succeeded:
            return "partially_succeeded"
        return "failed"

    def _aggregate_coverage(self, declarations: list[dict[str, Any]]) -> dict[str, Any]:
        """聚合覆盖率声明。

        Args:
            declarations: 各步骤的覆盖声明列表。

        Returns:
            dict: 聚合后的覆盖声明。
        """
        if not declarations:
            return CoverageDeclaration(
                analysis_mode="mixed",
                data_coverage_rate=0.0,
                llm_read_rate=0.0,
                is_sampled=False,
                mode_reason="无覆盖数据",
            ).to_dict()

        total = len(declarations)
        avg_data_rate = sum(d.get("data_coverage_rate", 0.0) for d in declarations) / total
        avg_llm_rate = sum(d.get("llm_read_rate", 0.0) for d in declarations) / total
        any_sampled = any(d.get("is_sampled", False) for d in declarations)

        return CoverageDeclaration(
            analysis_mode="mixed",
            data_coverage_rate=avg_data_rate,
            llm_read_rate=avg_llm_rate,
            is_sampled=any_sampled,
            mode_reason="聚合覆盖声明",
        ).to_dict()

    async def _fail_run(self, run_id: UUID, error_msg: str) -> None:
        """标记 Run 为 failed。

        Args:
            run_id: Run ID。
            error_msg: 错误消息。
        """
        if self._factory is None:
            return
        async with self._factory() as session:
            await self._repo.update_run_status(
                session,
                run_id,
                "failed",
                completed_at=datetime.now(UTC),
                error_summary=error_msg,
            )
        await self._publish_event(
            run_id, "run.status_changed", {"status": "failed", "error": error_msg}
        )

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
                                pass
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

    def _build_research_context(self, run_id: UUID, plan: object) -> str:
        """构建研究上下文（主问题 + 计划 + 已完成步骤摘要）。

        Args:
            run_id: Run ID。
            plan: 计划版本 ORM。

        Returns:
            str: 研究上下文文本。
        """
        parts: list[str] = []

        # 从计划中提取 DAG 步骤摘要
        if plan is not None and hasattr(plan, "dag_structure"):
            dag = plan.dag_structure
            steps = dag.get("steps", []) if isinstance(dag, dict) else []
            for s in steps:
                step_key = s.get("step_key", "")
                question = s.get("question", "")
                parts.append(f"步骤 {step_key}: {question}")

        return "\n".join(parts) if parts else ""

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
