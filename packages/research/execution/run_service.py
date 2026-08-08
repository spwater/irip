"""分析运行服务：Run 生命周期管理。

AnalysisRunService 负责：
1. submit_run: 提交 Run（校验计划已确认 + 无活跃 Run + 调度槽位 + Celery send_task）；
2. cancel_run: 取消 Run（标记 cancelled + 释放槽位 + 工件标记不可发布）；
3. get_run_status / get_run_progress: 状态与进度查询；
4. list_runs: 列出 Run；
5. get_queue_position: 排队位置查询；
6. check_publish_eligibility: 发布资格校验（依赖闭包完整性）。

Run 状态机：
queued → planning → running → succeeded / partially_succeeded / failed
queued / running → cancelled

参照 packages/research/snapshots.py 的 ScopedSessionMixin 模式。
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.execution.models_trusted import (
    CoverageDeclaration,
    EligibilityResult,
    QueuePosition,
    RunProgress,
    RunRef,
    StepProgress,
)
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted

logger = logging.getLogger("research.run_service")

DEFAULT_IMAGE_DIGEST: str = os.getenv(
    "RESEARCH_SANDBOX_IMAGE_DIGEST",
    "sha256:research-sandbox-scipy-2026.08",
)


class AnalysisRunService(ScopedSessionMixin):
    """分析运行生命周期管理服务。

    依赖注入 session_factory / department_id / actor_id / scheduler。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _scheduler: 研究调度器。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        scheduler: Any,
    ) -> None:
        """初始化运行服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            scheduler: ResearchScheduler 实例。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._scheduler = scheduler
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

    async def submit_run(
        self,
        workspace_id: UUID,
        plan_version_id: UUID,
        snapshot_id: UUID,
    ) -> "RunRef":
        """提交分析 Run。

        流程：
        1. 校验计划已确认（status='confirmed'）；
        2. 校验无活跃 Run（get_active_run_for_workspace 返回 None）；
        3. 获取 run_number（递增）；
        4. insert_run(status='queued')；
        5. scheduler.acquire_slot(user_id, run_id)：
           - 有槽位 → update_run_status('running') + send_task("research.run.execute")
           - 无槽位 → 保持 queued + update_run_queue_position
        6. 审计。

        Args:
            workspace_id: 工作空间 ID。
            plan_version_id: 计划版本 ID。
            snapshot_id: 证据快照 ID。

        Returns:
            RunRef: Run 引用（status=running 或 queued）。

        Raises:
            AppError: code="not_found"，当计划不存在或未确认时。
            AppError: code="validation_failed"，当已有活跃 Run 时。
        """

        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 校验计划已确认
            plan = await ResearchRepositoryTrusted.get_plan(session, plan_version_id)
            if plan is None or plan.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="分析计划不存在",
                    retryable=False,
                    fields={"plan_id": str(plan_version_id)},
                )
            if plan.status != "confirmed":
                raise AppError(
                    code="validation_failed",
                    message=f"计划状态为 '{plan.status}'，仅 confirmed 计划可提交 Run",
                    retryable=False,
                    fields={"plan_id": str(plan_version_id), "status": plan.status},
                )

            # 2. 校验无活跃 Run
            active_run = await ResearchRepositoryTrusted.get_active_run_for_workspace(
                session, workspace_id
            )
            if active_run is not None:
                raise AppError(
                    code="validation_failed",
                    message="工作空间已有活跃 Run，请等待完成或取消后再提交",
                    retryable=False,
                    fields={
                        "workspace_id": str(workspace_id),
                        "active_run_id": str(active_run.id),
                    },
                )

            # 3. 获取 run_number
            run_number = await ResearchRepositoryTrusted.get_next_run_number(session, workspace_id)

            # 4. 插入 Run
            run = await ResearchRepositoryTrusted.insert_run(
                session,
                workspace_id=workspace_id,
                plan_version_id=plan_version_id,
                snapshot_id=snapshot_id,
                run_number=run_number,
                image_digest=DEFAULT_IMAGE_DIGEST,
                created_by=actor_id,
            )

            # 5. 调度
            acquired, position = await self._scheduler.acquire_slot(str(actor_id), str(run.id))

            if acquired:
                # 有槽位：立即开始执行
                await ResearchRepositoryTrusted.update_run_status(
                    session,
                    run.id,
                    "running",
                    started_at=datetime.now(UTC),
                )
                # 发送 Celery 任务（在事务提交后发送）
                # 注意：send_task 在事务外调用，避免事务回滚后任务已发出
            else:
                # 无槽位：排队等待
                await ResearchRepositoryTrusted.update_run_queue_position(session, run.id, position)

            # 6. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.run.submit",
                    actor_user_id=actor_id,
                    resource_type="research_analysis_run",
                    resource_id=run.id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "run_number": run_number,
                        "plan_version_id": str(plan_version_id),
                        "status": "running" if acquired else "queued",
                        "queue_position": position if not acquired else None,
                    },
                ),
            )

            # 发送 Celery 任务（事务提交后由 session_scope 自动提交）
            if acquired:
                try:
                    from apps.worker.celery_app import celery_app

                    celery_app.send_task(
                        "research.run.execute",
                        kwargs={"run_id": str(run.id)},
                    )
                except Exception as exc:
                    logger.error("Failed to send Celery task: %s", exc)

            return RunRef(
                run_id=run.id,
                workspace_id=workspace_id,
                run_number=run_number,
                status="running" if acquired else "queued",
                queue_position=position if not acquired else None,
            )

    async def cancel_run(self, run_id: UUID) -> None:
        """取消 Run。

        流程：
        1. 获取 Run，校验为活跃状态（queued/running/planning）；
        2. update_run_status('cancelled')；
        3. scheduler.release_slot；
        4. 工件标记不可发布；
        5. 审计。

        Args:
            run_id: Run ID。

        Raises:
            AppError: code="not_found"，当 Run 不存在时。
            AppError: code="validation_failed"，当 Run 状态不可取消时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            run = await ResearchRepositoryTrusted.get_run(session, run_id)
            if run is None:
                raise AppError(
                    code="not_found",
                    message="分析 Run 不存在",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            if run.status not in ("queued", "planning", "running"):
                raise AppError(
                    code="validation_failed",
                    message=f"Run 状态为 '{run.status}'，仅活跃状态可取消",
                    retryable=False,
                    fields={"run_id": str(run_id), "status": run.status},
                )

            now = datetime.now(UTC)
            await ResearchRepositoryTrusted.update_run_status(
                session,
                run_id,
                "cancelled",
                cancelled_at=now,
                cancelled_by=actor_id,
            )

            # 取消当前步骤
            steps = await ResearchRepositoryTrusted.list_steps_by_run(session, run_id)
            for step in steps:
                if step.status in ("pending", "running"):
                    await ResearchRepositoryTrusted.update_step_status(
                        session,
                        step.id,
                        "cancelled" if step.status == "running" else "skipped",
                    )

            # 释放调度槽位
            await self._scheduler.release_slot(str(run.created_by), str(run_id))

            # 工件标记不可发布（在独立事务中由 ArtifactService 处理，此处仅标记）
            artifacts = await ResearchRepositoryTrusted.list_artifacts_by_run(session, run_id)
            for a in artifacts:
                if a.is_publishable:
                    await ResearchRepositoryTrusted.update_artifact_publishable(
                        session, a.id, False
                    )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.run.cancel",
                    actor_user_id=actor_id,
                    resource_type="research_analysis_run",
                    resource_id=run_id,
                    payload={"run_number": run.run_number},
                ),
            )

    async def get_run_status(self, run_id: UUID) -> "RunRef":
        """获取 Run 状态。

        Args:
            run_id: Run ID。

        Returns:
            RunRef: Run 引用。

        Raises:
            AppError: code="not_found"，当 Run 不存在时。
        """
        from packages.research.execution.models_trusted import RunRef

        async with self._scoped_session() as session:
            run = await ResearchRepositoryTrusted.get_run(session, run_id)
            if run is None:
                raise AppError(
                    code="not_found",
                    message="分析 Run 不存在",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )
            return RunRef(
                run_id=run.id,
                workspace_id=run.workspace_id,
                run_number=run.run_number,
                status=run.status,
                queue_position=run.queue_position,
            )

    async def get_run_progress(self, run_id: UUID) -> RunProgress:
        """获取 Run 进度（含步骤状态列表 + 覆盖声明）。

        Args:
            run_id: Run ID。

        Returns:
            RunProgress: Run 进度。

        Raises:
            AppError: code="not_found"，当 Run 不存在时。
        """
        async with self._scoped_session() as session:
            run = await ResearchRepositoryTrusted.get_run(session, run_id)
            if run is None:
                raise AppError(
                    code="not_found",
                    message="分析 Run 不存在",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            steps = await ResearchRepositoryTrusted.list_steps_by_run(session, run_id)
            step_progress_list = [
                StepProgress(
                    step_id=s.id,
                    step_key=s.step_key,
                    step_index=s.step_index,
                    status=s.status,
                    method=s.method,
                    analysis_mode=s.analysis_mode,
                    coverage_rate=s.coverage_rate,
                    llm_read_rate=s.llm_read_rate,
                    is_sampled=s.is_sampled,
                    attempt_count=s.attempt_count,
                    error_message=s.error_message,
                )
                for s in steps
            ]

            total = len(steps)
            completed = sum(
                1 for s in steps if s.status in ("succeeded", "failed", "skipped", "cancelled")
            )

            # 从 Run 的 coverage_summary 构建 CoverageDeclaration
            coverage: CoverageDeclaration | None = None
            if run.coverage_summary:
                cs = run.coverage_summary
                coverage = CoverageDeclaration(
                    analysis_mode=cs.get("analysis_mode", "mixed"),
                    data_coverage_rate=cs.get("data_coverage_rate", 0.0),
                    llm_read_rate=cs.get("llm_read_rate", 0.0),
                    is_sampled=cs.get("is_sampled", False),
                    batch_count=cs.get("batch_count"),
                    batch_progress=cs.get("batch_progress"),
                    mode_reason=cs.get("mode_reason", ""),
                )

            return RunProgress(
                run_id=run.id,
                status=run.status,
                total_steps=total,
                completed_steps=completed,
                steps=step_progress_list,
                coverage_declaration=coverage,
                started_at=run.started_at,
                completed_at=run.completed_at,
            )

    async def list_runs(self, workspace_id: UUID) -> list["RunRef"]:
        """列出工作空间的全部 Run。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[RunRef]: Run 引用列表。
        """
        from packages.research.execution.models_trusted import RunRef

        async with self._scoped_session() as session:
            runs = await ResearchRepositoryTrusted.list_runs(session, workspace_id)
            return [
                RunRef(
                    run_id=r.id,
                    workspace_id=r.workspace_id,
                    run_number=r.run_number,
                    status=r.status,
                    queue_position=r.queue_position,
                )
                for r in runs
            ]

    async def get_queue_position(self, run_id: UUID) -> QueuePosition:
        """获取排队位置。

        Args:
            run_id: Run ID。

        Returns:
            QueuePosition: 排队位置信息。

        Raises:
            AppError: code="not_found"，当 Run 不存在时。
        """
        async with self._scoped_session() as session:
            run = await ResearchRepositoryTrusted.get_run(session, run_id)
            if run is None:
                raise AppError(
                    code="not_found",
                    message="分析 Run 不存在",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            if run.status != "queued":
                return QueuePosition(position=0, ahead_count=0, estimated_wait_seconds=0)

            result = await self._scheduler.get_queue_position(str(run_id))
            return result

    async def check_publish_eligibility(
        self,
        run_id: UUID,
        step_keys: list[str] | None = None,
    ) -> EligibilityResult:
        """校验发布资格。

        只有依赖闭包全部成功的步骤输出才具备发布资格。
        从部分失败 Run 发布独立成功结果时，必须标明源 Run 为部分成功。

        Args:
            run_id: Run ID。
            step_keys: 指定步骤 key 列表（可选，None 表示全部成功步骤）。

        Returns:
            EligibilityResult: 发布资格校验结果。

        Raises:
            AppError: code="not_found"，当 Run 不存在时。
        """
        async with self._scoped_session() as session:
            run = await ResearchRepositoryTrusted.get_run(session, run_id)
            if run is None:
                raise AppError(
                    code="not_found",
                    message="分析 Run 不存在",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            # 取消的 Run 不可发布
            if run.status == "cancelled":
                return EligibilityResult(
                    is_eligible=False,
                    failed_step_keys=[],
                    source_run_partial=False,
                    message="已取消的 Run 不允许发布",
                )

            steps = await ResearchRepositoryTrusted.list_steps_by_run(session, run_id)

            # 构建步骤状态映射
            step_status_map: dict[str, str] = {s.step_key: s.status for s in steps}

            # 确定要校验的步骤集合
            target_keys = (
                set(step_keys)
                if step_keys
                else {k for k, v in step_status_map.items() if v == "succeeded"}
            )

            # 检查每个目标步骤的依赖闭包
            failed_deps: list[str] = []
            step_dep_map: dict[str, list[str]] = {}
            for s in steps:
                step_dep_map[s.step_key] = list(s.depends_on or [])

            for key in target_keys:
                deps = self._get_dependency_closure(key, step_dep_map)
                for dep_key in deps:
                    dep_status = step_status_map.get(dep_key, "pending")
                    if dep_status != "succeeded":
                        failed_deps.append(dep_key)

            is_partial = run.status == "partially_succeeded"
            is_eligible = len(failed_deps) == 0 and run.status in (
                "succeeded",
                "partially_succeeded",
            )

            if not is_eligible:
                msg = (
                    "依赖闭包不完整，存在失败步骤"
                    if failed_deps
                    else f"Run 状态为 {run.status}，不可发布"
                )
            elif is_partial:
                msg = "可发布（源 Run 部分成功，仅发布成功步骤输出）"
            else:
                msg = "可发布"

            return EligibilityResult(
                is_eligible=is_eligible,
                failed_step_keys=list(set(failed_deps)),
                source_run_partial=is_partial,
                message=msg,
            )

    def _get_dependency_closure(self, step_key: str, dep_map: dict[str, list[str]]) -> set[str]:
        """获取步骤的全部依赖闭包（递归）。

        Args:
            step_key: 步骤键。
            dep_map: 步骤依赖映射。

        Returns:
            set[str]: 依赖闭包（不含自身）。
        """
        visited: set[str] = set()
        stack: list[str] = list(dep_map.get(step_key, []))
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            stack.extend(dep_map.get(current, []))
        return visited
