"""标准域依赖覆盖 provider（F-20）。

注册：
- StandardService（标准变量服务）；
- ObjectGraphService（工业对象图服务）；
- TemplateService（事实模板服务）；
- MethodService（方法服务）；
- PackageService（标准包服务）；
- DepartmentService（实验室服务）；
- EquipmentService（设备仪器服务）；
- UserDepartmentService（用户-实验室关联服务）。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_org_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.dependencies.departments import (
    get_department_service,
    get_user_department_service,
)
from apps.api.routers.equipment import get_equipment_service
from apps.api.routers.fact_templates import (
    get_method_service,
    get_package_service,
    get_template_service,
)
from apps.api.routers.objects import get_object_graph_service
from apps.api.routers.standards import get_standard_service


def register(ctx: CompositionContext) -> None:
    """注册标准域依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.departments.service import DepartmentService
    from packages.departments.user_departments import UserDepartmentService
    from packages.equipment.service import EquipmentService
    from packages.standards.methods import MethodService
    from packages.standards.object_graph import ObjectGraphService
    from packages.standards.packages import PackageService
    from packages.standards.service import StandardService
    from packages.standards.templates import TemplateService

    # 标准变量服务
    async def _get_standard_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> StandardService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return StandardService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_standard_service] = _get_standard_service_dep

    # 工业对象图服务
    async def _get_object_graph_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ObjectGraphService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return ObjectGraphService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_object_graph_service] = _get_object_graph_service_dep

    # 事实模板服务
    async def _get_template_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> TemplateService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return TemplateService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_template_service] = _get_template_service_dep

    # 方法服务
    async def _get_method_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> MethodService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return MethodService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_method_service] = _get_method_service_dep

    # 标准包服务
    async def _get_package_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> PackageService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return PackageService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_package_service] = _get_package_service_dep

    # 实验室服务
    async def _get_department_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> DepartmentService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return DepartmentService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
        )

    ctx.app.dependency_overrides[get_department_service] = _get_department_service_dep

    # 设备仪器服务
    async def _get_equipment_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> EquipmentService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return EquipmentService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
        )

    ctx.app.dependency_overrides[get_equipment_service] = _get_equipment_service_dep

    # 用户-实验室关联服务
    async def _get_user_department_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> UserDepartmentService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        return UserDepartmentService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
        )

    ctx.app.dependency_overrides[get_user_department_service] = _get_user_department_service_dep
