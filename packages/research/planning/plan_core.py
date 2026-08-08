"""PlanService 装配模块：组合各功能域 Mixin 为最终 PlanService。

拆分自 plan_service.py（IRIP 拆分任务）。``plan_core`` 将
``PlanGeneratorMixin`` / ``PlanConfirmerMixin`` / ``PlanReviserMixin`` /
``PlanAnalyzerMixin`` 装配为 ``PlanService``，并承载计划查询方法
（list_plans / get_plan）。

向后兼容：``plan_service.py`` 与 ``packages/research.planning.__init__``
均 re-export 本模块的 ``PlanService``，使旧式导入路径仍可工作。
"""

from uuid import UUID

from packages.common.errors import AppError
from packages.research.execution.models_trusted import PlanDetail, PlanVersionRef
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.planning.plan_analyzer import PlanAnalyzerMixin
from packages.research.planning.plan_base import PlanServiceBase
from packages.research.planning.plan_confirmer import PlanConfirmerMixin
from packages.research.planning.plan_generator import PlanGeneratorMixin
from packages.research.planning.plan_reviser import PlanReviserMixin


class PlanService(
    PlanGeneratorMixin,
    PlanConfirmerMixin,
    PlanReviserMixin,
    PlanAnalyzerMixin,
    PlanServiceBase,
):
    """分析计划生成与确认服务。

    由各功能域 Mixin 装配而成：
    - PlanGeneratorMixin: generate_plan 及辅助方法；
    - PlanConfirmerMixin: confirm_plan；
    - PlanReviserMixin: revise_plan；
    - PlanAnalyzerMixin: analyze_data / extract_insight。

    本模块直接承载计划查询方法 list_plans / get_plan。
    """

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


__all__ = ["PlanService"]
