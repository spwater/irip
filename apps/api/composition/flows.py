"""流程与组件相关依赖覆盖 provider（F-20）。

注册：
- ComponentRegistryService（组件注册表服务）；
- FlowRuntimeService（流程运行时服务，含组件注册表 + 执行器 + 作业服务）。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.components import get_component_registry_service
from apps.api.routers.flows import get_flow_service

# 模块级组件执行器单例（避免每次请求重复注册 29 个内置组件）。
_flow_runner: object | None = None


def register(ctx: CompositionContext) -> None:
    """注册流程与组件相关依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.dependencies.dept_scope import get_rls_dept_id
    from apps.api.routers.ai_config import get_active_ai_config
    from packages.common.artifacts import ArtifactService
    from packages.components.builtin import register_builtin_components
    from packages.components.flow.flow_runtime import FlowRuntimeService
    from packages.components.registry import ComponentRegistryService
    from packages.components.runner import PythonComponentRunner
    from packages.jobs.service import JobService

    async def _get_component_registry_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ComponentRegistryService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = ComponentRegistryService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service.set_rls_override(rls_dept_id)
        return service

    ctx.app.dependency_overrides[get_component_registry_service] = (
        _get_component_registry_service_dep
    )

    async def _get_flow_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> FlowRuntimeService:
        global _flow_runner
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        registry = ComponentRegistryService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            registry.set_rls_override(rls_dept_id)
        if _flow_runner is None:
            _flow_runner = PythonComponentRunner()
            register_builtin_components(_flow_runner)
        job_svc = JobService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            created_by=current_user.user_id,
        )
        if rls_dept_id is not None:
            job_svc.set_rls_override(rls_dept_id)
        art_svc = ArtifactService(
            s3_repo=ctx.s3_repo,  # type: ignore[arg-type]
            session_factory=ctx.session_factory,
            department_id=dept_id,
            uploaded_by=current_user.user_id,
        )
        if rls_dept_id is not None:
            art_svc.set_rls_override(rls_dept_id)
        flow_svc = FlowRuntimeService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            registry=registry,
            runner=_flow_runner,  # type: ignore[arg-type]
            job_service=job_svc,
            artifact_service=art_svc,
            ai_config_provider=get_active_ai_config,
        )
        if rls_dept_id is not None:
            flow_svc.set_rls_override(rls_dept_id)
        return flow_svc

    ctx.app.dependency_overrides[get_flow_service] = _get_flow_service_dep
