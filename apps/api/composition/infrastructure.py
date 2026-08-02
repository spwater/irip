"""基础设施依赖覆盖 provider（F-20，标准层空表清理后精简版）。

注册：
- 健康检查依赖（DB 会话工厂、Redis URL、S3 客户端）；
- 工件服务（ArtifactService）；
- 治理/审计/备份路由用的 DB 会话工厂；
- 数据源预览服务（IngestionService）；
- 参数服务。

映射评分服务（MappingService）与映射配置生命周期服务
（MappingProfileService）依赖的 variable / mapping_profile 表已在
migration 0057 中 DROP，对应 DI 注册一并删除。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.assistant import get_ai_service  # noqa: F401 (re-export guard)
from apps.api.routers.audit import get_audit_session_factory
from apps.api.routers.backups import get_backups_session_factory
from apps.api.routers.governance import get_governance_session_factory
from apps.api.routers.health import (
    get_health_session_factory,
    get_redis_url,
    get_s3_repo,
)
from apps.api.routers.ingestions import get_ingestion_service
from apps.api.routers.parameters import get_parameter_service
from apps.api.routers.uploads import get_artifact_service


def register(ctx: CompositionContext) -> None:
    """注册基础设施依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.common.artifacts import ArtifactService
    from packages.connectors.mapping import IngestionService
    from packages.parameters.service import ParameterService

    # 健康检查依赖
    ctx.app.dependency_overrides[get_health_session_factory] = lambda: ctx.session_factory
    ctx.app.dependency_overrides[get_redis_url] = lambda: ctx.redis_url
    ctx.app.dependency_overrides[get_s3_repo] = lambda: ctx.s3_repo

    # 工件服务（需当前用户上下文，按请求构造）
    async def _get_artifact_service(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ArtifactService:
        """按请求构造工件服务，从 DB 查询当前用户的 department_id。"""
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        return ArtifactService(
            s3_repo=ctx.s3_repo,
            session_factory=ctx.session_factory,
            department_id=dept_id,  # 
            uploaded_by=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_artifact_service] = _get_artifact_service

    # 治理路由用的 DB 会话工厂
    ctx.app.dependency_overrides[get_governance_session_factory] = lambda: ctx.session_factory

    # 审计路由用的 DB 会话工厂
    ctx.app.dependency_overrides[get_audit_session_factory] = lambda: ctx.session_factory

    # 备份/恢复路由用的 DB 会话工厂
    ctx.app.dependency_overrides[get_backups_session_factory] = lambda: ctx.session_factory

    # 数据源预览服务
    async def _get_ingestion_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> IngestionService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        return IngestionService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
        )

    ctx.app.dependency_overrides[get_ingestion_service] = _get_ingestion_service_dep

    # 参数服务
    async def _get_parameter_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ParameterService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        return ParameterService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )

    ctx.app.dependency_overrides[get_parameter_service] = _get_parameter_service_dep
