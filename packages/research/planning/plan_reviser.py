"""计划修订 Mixin：revise_plan 逻辑。

拆分自 plan_service.py（IRIP 拆分任务）。``PlanReviserMixin`` 承载
基于已有计划创建修订版本（用户调整步骤后保存）的流程。
"""

from typing import Any
from uuid import UUID

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.execution.models_trusted import PlanVersionRef
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.planning.plan_base import PlanServiceBase


class PlanReviserMixin(PlanServiceBase):
    """计划修订功能域：revise_plan。"""

    async def revise_plan(
        self,
        workspace_id: UUID,
        plan_id: UUID,
        revised_steps: list[dict[str, Any]],
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
