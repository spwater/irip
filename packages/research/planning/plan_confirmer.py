"""计划确认 Mixin：confirm_plan 逻辑。

拆分自 plan_service.py（IRIP 拆分任务）。``PlanConfirmerMixin`` 承载
用户确认计划（draft → confirmed）的流程。
"""

from datetime import UTC, datetime
from uuid import UUID

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.execution.models_trusted import PlanVersionRef
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.planning.plan_base import PlanServiceBase


class PlanConfirmerMixin(PlanServiceBase):
    """计划确认功能域：confirm_plan。"""

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
