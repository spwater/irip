"""模型服务依赖覆盖 provider（F-20）。

注册：
- ModelService（模型服务，需当前用户上下文 + 工件服务 + 事实服务）。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.models import get_model_service


def register(ctx: CompositionContext) -> None:
    """注册模型服务依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.dependencies.dept_scope import get_rls_dept_id
    from packages.common.artifacts import ArtifactService
    from packages.facts.service import FactService
    from packages.models.service import ModelService

    async def _get_model_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ModelService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        art_svc = ArtifactService(
            s3_repo=ctx.s3_repo,  # type: ignore[arg-type]
            session_factory=ctx.session_factory,
            department_id=dept_id,
            uploaded_by=current_user.user_id,
        )
        if rls_dept_id is not None:
            art_svc._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        # 注入 FactService，使模型预测结果写入溯源事实链
        fact_svc = FactService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        if rls_dept_id is not None:
            fact_svc._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        model_svc = ModelService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            artifact_service=art_svc,
            fact_service=fact_svc,
        )
        if rls_dept_id is not None:
            model_svc._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return model_svc

    ctx.app.dependency_overrides[get_model_service] = _get_model_service_dep
