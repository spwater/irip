"""标准域依赖覆盖 provider（F-20，标准层空表清理后精简版）。

注册：
- ObjectGraphService（工业对象图服务）；
- DepartmentService（实验室服务）；
- EquipmentService（设备仪器服务）；
- UserDepartmentService（用户-实验室关联服务）。

原 StandardService / TemplateService / PackageService 依赖的表
（variable / fact_template / standard_package）已在 migration 0057 中
DROP，对应服务与 DI 注册一并删除。

阶段2 多租户升级：lookup_dept_id, department_id。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.dependencies.departments import (
    get_department_service,
    get_user_department_service,
)
from apps.api.routers.equipment import get_equipment_service
from apps.api.routers.objects import get_object_graph_service


def register(ctx: CompositionContext) -> None:
    """注册标准域依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.dependencies.dept_scope import get_rls_dept_id
    from packages.departments.service import DepartmentService
    from packages.departments.user_departments import UserDepartmentService
    from packages.equipment.service import EquipmentService
    from packages.standards.object_graph import ObjectGraphService

    # 工业对象图服务
    async def _get_object_graph_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ObjectGraphService:
        service = ObjectGraphService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_object_graph_service] = _get_object_graph_service_dep

    # 实验室服务
    async def _get_department_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> DepartmentService:
        # 阶段2: 直接从 CurrentUser 拿 department_id（get_current_user 已查 DB）
        service = DepartmentService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_department_service] = _get_department_service_dep

    # 设备仪器服务
    async def _get_equipment_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> EquipmentService:
        service = EquipmentService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_equipment_service] = _get_equipment_service_dep

    # 用户-实验室关联服务
    async def _get_user_department_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> UserDepartmentService:
        service = UserDepartmentService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_user_department_service] = _get_user_department_service_dep

    # 实验项目服务
    from apps.api.routers.experiment_projects import get_experiment_project_service
    from packages.experiment_project.service import ExperimentProjectService

    async def _get_experiment_project_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ExperimentProjectService:
        service = ExperimentProjectService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_experiment_project_service] = (
        _get_experiment_project_service_dep
    )
