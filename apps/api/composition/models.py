"""模型服务依赖覆盖 provider（F-20）。

注册：
- ModelService（模型服务，需当前用户上下文 + 工件服务 + 事实服务）。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_org_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.models import get_model_service


def register(ctx: CompositionContext) -> None:
    """注册模型服务依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.common.artifacts import ArtifactService
    from packages.facts.service import FactService
    from packages.models.service import ModelService

    async def _get_model_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ModelService:
        org_id = await lookup_org_id(ctx.session_factory, current_user.user_id)
        art_svc = ArtifactService(
            s3_repo=ctx.s3_repo,
            session_factory=ctx.session_factory,
            organization_id=org_id,
            uploaded_by=current_user.user_id,
        )
        # 注入 FactService，使模型预测结果写入溯源事实链
        fact_svc = FactService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )
        return ModelService(
            session_factory=ctx.session_factory,
            organization_id=org_id,
            artifact_service=art_svc,
            fact_service=fact_svc,
        )

    ctx.app.dependency_overrides[get_model_service] = _get_model_service_dep
