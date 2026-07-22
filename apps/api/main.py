"""FastAPI 应用工厂：创建并配置 IRIP API 应用。

职责（实施计划 Task 9）：
  - 创建 FastAPI app，挂载全部路由（auth, uploads, jobs, health）；
  - CORS 中间件（允许前端 origin）；
  - AppError 异常处理器（返回统一 JSON 错误格式）；
  - lifespan：启动时初始化 DB session factory、S3 client、Redis client，
    并设置依赖覆盖。

启动命令：
  uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser, get_current_user, get_token_secret
from apps.api.dependencies.departments import (
    get_department_service,
    get_user_department_service,
)
from apps.api.routers.auth import auth_router, get_auth_service, get_me_session_factory, me_router
from apps.api.routers.audit import audit_router, get_audit_session_factory
from apps.api.routers.backups import backups_router, get_backups_session_factory
from apps.api.routers.assistant import assistant_router, get_ai_service
from apps.api.routers.ai_config import ai_config_router, set_session_factory as set_ai_config_session_factory, get_active_ai_config
from apps.api.routers.components import (
    components_router,
    get_component_registry_service,
)
from apps.api.routers.departments import departments_router
from apps.api.routers.equipment import equipment_router, get_equipment_service
from apps.api.routers.fact_templates import (
    get_method_service,
    get_package_service,
    get_template_service,
    methods_router,
    packages_router,
    templates_router,
)
from apps.api.routers.facts import facts_router, get_fact_service
from apps.api.routers.flows import flows_router, get_flow_service
from apps.api.routers.governance import governance_router, get_governance_session_factory
from apps.api.routers.health import (
    get_health_session_factory,
    get_redis_url,
    get_s3_repo,
    health_router,
)
from apps.api.routers.ingestions import (
    get_ingestion_service,
    get_mapping_profile_service,
    get_mapping_service,
    ingestions_router,
)
from apps.api.routers.jobs import get_job_service, jobs_router
from apps.api.routers.models import get_model_service, models_router
from apps.api.routers.objects import get_object_graph_service, objects_router
from apps.api.routers.parameters import (
    get_parameter_service,
    parameters_router,
)
from apps.api.routers.provenance import (
    get_derivation_service,
    get_evidence_service,
    get_provenance_graph_service,
    get_recipe_service,
    provenance_router,
)
from apps.api.routers.standards import get_standard_service, standards_router
from apps.api.routers.uploads import (
    artifacts_router,
    get_artifact_service,
    uploads_router,
)
from apps.api.routers.user_departments import user_departments_router
from packages.common.artifacts import ArtifactService
from packages.common.database import build_session_factory
from packages.common.errors import AppError
from packages.common.s3_repository import S3Repository
from packages.connectors.mapping import (
    IngestionService,
    MappingProfileService,
    MappingService,
)
from packages.departments.service import DepartmentService
from packages.departments.user_departments import UserDepartmentService
from packages.equipment.service import EquipmentService
from packages.facts.service import FactService
from packages.jobs.service import JobService
from packages.parameters.service import ParameterService
from packages.provenance.derivations import DerivationService
from packages.provenance.evidence import EvidenceService
from packages.provenance.graph import ProvenanceGraphService
from packages.provenance.recipes import RecipeService
from packages.standards.methods import MethodService
from packages.standards.object_graph import ObjectGraphService
from packages.standards.packages import PackageService
from packages.standards.service import StandardService
from packages.standards.templates import TemplateService

# V2+V3 新增服务导入
from packages.components.registry import ComponentRegistryService
from packages.components.flow_runtime import FlowRuntimeService
from packages.components.runner import PythonComponentRunner
from packages.components.builtin import register_builtin_components
from packages.models.service import ModelService
from packages.ai.service import AIService
from packages.ai.offline_provider import OfflineProvider
from packages.ai.openai_compatible import OpenAICompatibleProvider
from packages.ai.tools import ToolRegistry

#: AppError code → HTTP 状态码映射（docs/arch-v0.md §7.2）。
_STATUS_MAP: dict[str, int] = {
    "invalid_credentials": 401,
    "token_expired": 401,
    "refresh_replayed": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "invalid_transition": 409,
    "published_version_immutable": 409,
    "unsupported_media_type": 415,
    "hash_mismatch": 422,
    "size_mismatch": 422,
    "validation_failed": 422,
    "incompatible_dimensions": 422,
    "unknown_unit": 422,
    "invalid_cursor": 422,
    "self_relation": 422,
    "object_cycle": 409,
    "reference_not_published": 422,
    "missing_observation": 422,
    "duplicate_observation": 422,
    "missing_unit": 422,
    "invalid_observation": 422,
    "secret_not_found": 404,
    "connector_error": 502,
    "internal_error": 500,
    "normalized_without_raw": 422,
    "template_not_published": 422,
    "method_not_published": 422,
    "quality_blocked": 422,
    "ingestion_error": 500,
    "component_unavailable": 422,
    "evidence_not_frozen": 422,
    "recipe_not_published": 422,
    "self_approval_forbidden": 403,
    "derivation_not_succeeded": 422,
    "candidate_not_pending": 409,
}


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    return url


def _build_s3_repo() -> S3Repository:
    """从环境变量构建 S3 客户端。"""
    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"
    return S3Repository(
        endpoint_url=endpoint,
        access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        bucket_name=os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
        region=os.getenv("IRIP_MINIO_REGION", "us-east-1"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化资源，退出时清理。

    初始化内容：
      1. 数据库会话工厂（从 IRIP_DATABASE_URL）；
      2. S3 / MinIO 客户端（ensure_bucket 幂等创建）；
      3. Redis URL；
      4. JWT 密钥；
      5. 设置全部依赖覆盖。
    """
    # ---- 1. 数据库会话工厂 ----
    db_url = os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL environment variable is required")
    async_url = _to_async_url(db_url)
    session_factory = build_session_factory(async_url)

    # ---- 2. S3 / MinIO ----
    s3_repo = _build_s3_repo()
    s3_repo.ensure_bucket()

    # ---- 3. Redis URL ----
    redis_url = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")

    # ---- 4. JWT 密钥 ----
    token_secret = os.getenv("IRIP_JWT_SECRET", "irip-dev-secret-2026")

    # ---- 5. 依赖覆盖 ----

    # 认证服务
    from packages.auth.backends import LocalAuthBackend
    from packages.auth.repository import AuthRepository
    from packages.auth.service import AuthService
    from packages.common.clock import SystemClock

    auth_repository = AuthRepository()
    auth_backend = LocalAuthBackend(auth_repository)
    auth_service = AuthService(
        backend=auth_backend,
        repository=auth_repository,
        session_factory=session_factory,
        token_secret=token_secret,
        clock=SystemClock(),
    )

    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_token_secret] = lambda: token_secret

    # 健康检查依赖
    app.dependency_overrides[get_health_session_factory] = lambda: session_factory
    app.dependency_overrides[get_redis_url] = lambda: redis_url
    app.dependency_overrides[get_s3_repo] = lambda: s3_repo

    # 工件服务（需当前用户上下文，按请求构造）
    async def _get_artifact_service(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ArtifactService:
        """按请求构造工件服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return ArtifactService(
            s3_repo=s3_repo,
            session_factory=session_factory,
            organization_id=org_id,
            uploaded_by=current_user.user_id,
        )

    app.dependency_overrides[get_artifact_service] = _get_artifact_service

    # 作业服务（需当前用户上下文，按请求构造）
    async def _get_job_service(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> JobService:
        """按请求构造作业服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return JobService(
            session_factory=session_factory,
            organization_id=org_id,
            created_by=current_user.user_id,
        )

    app.dependency_overrides[get_job_service] = _get_job_service

    # 实验室服务（需当前用户上下文，按请求构造）
    async def _get_department_service(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> DepartmentService:
        """按请求构造实验室服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return DepartmentService(
            session_factory=session_factory,
            organization_id=org_id,
        )

    app.dependency_overrides[get_department_service] = _get_department_service

    # 设备仪器服务（需当前用户上下文，按请求构造）
    async def _get_equipment_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> EquipmentService:
        """按请求构造设备仪器服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return EquipmentService(
            session_factory=session_factory,
            organization_id=org_id,
        )

    app.dependency_overrides[get_equipment_service] = _get_equipment_service_dep

    # 用户-实验室关联服务（需当前用户上下文，按请求构造）
    async def _get_user_department_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> UserDepartmentService:
        """按请求构造用户-实验室关联服务。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return UserDepartmentService(
            session_factory=session_factory,
            organization_id=org_id,
        )

    app.dependency_overrides[get_user_department_service] = (
        _get_user_department_service_dep
    )

    # 标准变量服务（需当前用户上下文，按请求构造）
    async def _get_standard_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> StandardService:
        """按请求构造标准变量服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return StandardService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_standard_service] = _get_standard_service_dep

    # 工业对象图服务（需当前用户上下文，按请求构造）
    async def _get_object_graph_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ObjectGraphService:
        """按请求构造工业对象图服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return ObjectGraphService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_object_graph_service] = (
        _get_object_graph_service_dep
    )

    # 事实模板服务（需当前用户上下文，按请求构造）
    async def _get_template_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> TemplateService:
        """按请求构造事实模板服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return TemplateService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_template_service] = _get_template_service_dep

    # 方法服务（需当前用户上下文，按请求构造）
    async def _get_method_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> MethodService:
        """按请求构造方法服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return MethodService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_method_service] = _get_method_service_dep

    # 标准包服务（需当前用户上下文，按请求构造）
    async def _get_package_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> PackageService:
        """按请求构造标准包服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return PackageService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_package_service] = _get_package_service_dep

    # 映射评分服务（需当前用户上下文，按请求构造）
    async def _get_mapping_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> "MappingService":
        """按请求构造映射评分服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return MappingService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_mapping_service] = _get_mapping_service_dep

    # 映射配置生命周期服务（需当前用户上下文，按请求构造）
    async def _get_mapping_profile_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> "MappingProfileService":
        """按请求构造映射配置服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return MappingProfileService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_mapping_profile_service] = (
        _get_mapping_profile_service_dep
    )

    # 数据源预览服务（需当前用户上下文，按请求构造）
    async def _get_ingestion_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> "IngestionService":
        """按请求构造预览服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return IngestionService(
            session_factory=session_factory,
            organization_id=org_id,
        )

    app.dependency_overrides[get_ingestion_service] = _get_ingestion_service_dep

    # 事实服务（需当前用户上下文，按请求构造）
    async def _get_fact_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> FactService:
        """按请求构造事实服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return FactService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_fact_service] = _get_fact_service_dep

    # 证据集服务（需当前用户上下文，按请求构造）
    async def _get_evidence_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> EvidenceService:
        """按请求构造证据集服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return EvidenceService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_evidence_service] = _get_evidence_service_dep

    # 推导配方服务（需当前用户上下文，按请求构造）
    async def _get_recipe_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> RecipeService:
        """按请求构造推导配方服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return RecipeService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_recipe_service] = _get_recipe_service_dep

    # 推导运行服务（需当前用户上下文，按请求构造）
    async def _get_derivation_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> DerivationService:
        """按请求构造推导运行服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return DerivationService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_derivation_service] = (
        _get_derivation_service_dep
    )

    # 溯源图服务（需当前用户上下文，按请求构造）
    async def _get_provenance_graph_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ProvenanceGraphService:
        """按请求构造溯源图服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return ProvenanceGraphService(
            session_factory=session_factory,
            organization_id=org_id,
        )

    app.dependency_overrides[get_provenance_graph_service] = (
        _get_provenance_graph_service_dep
    )

    # 参数服务（需当前用户上下文，按请求构造）
    async def _get_parameter_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ParameterService:
        """按请求构造参数服务，从 DB 查询当前用户的 organization_id。"""
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return ParameterService(
            session_factory=session_factory,
            organization_id=org_id,
            actor_id=current_user.user_id,
        )

    app.dependency_overrides[get_parameter_service] = (
        _get_parameter_service_dep
    )

    # /me 端点用的 DB 会话工厂
    app.dependency_overrides[get_me_session_factory] = lambda: session_factory

    # 治理路由用的 DB 会话工厂
    app.dependency_overrides[get_governance_session_factory] = lambda: session_factory

    # 审计路由用的 DB 会话工厂
    app.dependency_overrides[get_audit_session_factory] = lambda: session_factory

    # 备份/恢复路由用的 DB 会话工厂
    app.dependency_overrides[get_backups_session_factory] = lambda: session_factory

    # 组件注册表服务（需当前用户上下文）
    async def _get_component_registry_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ComponentRegistryService:
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        return ComponentRegistryService(
            session_factory=session_factory,
            organization_id=org_id,
        )

    app.dependency_overrides[get_component_registry_service] = (
        _get_component_registry_service_dep
    )

    # 流程运行时服务（需当前用户上下文 + 组件注册表 + 执行器 + 作业服务）
    # PythonComponentRunner 是无状态的，使用模块级单例避免每次请求重复注册 29 个组件
    _flow_runner: PythonComponentRunner | None = None

    async def _get_flow_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> FlowRuntimeService:
        nonlocal _flow_runner
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        registry = ComponentRegistryService(
            session_factory=session_factory,
            organization_id=org_id,
        )
        if _flow_runner is None:
            _flow_runner = PythonComponentRunner()
            register_builtin_components(_flow_runner)
        job_svc = JobService(
            session_factory=session_factory,
            organization_id=org_id,
            created_by=current_user.user_id,
        )
        return FlowRuntimeService(
            session_factory=session_factory,
            organization_id=org_id,
            registry=registry,
            runner=_flow_runner,
            job_service=job_svc,
        )

    app.dependency_overrides[get_flow_service] = _get_flow_service_dep

    # 模型服务（需当前用户上下文 + 工件服务）
    async def _get_model_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ModelService:
        org_id = await _lookup_org_id(session_factory, current_user.user_id)
        art_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=session_factory,
            organization_id=org_id,
            uploaded_by=current_user.user_id,
        )
        return ModelService(
            session_factory=session_factory,
            organization_id=org_id,
            artifact_service=art_svc,
        )

    app.dependency_overrides[get_model_service] = _get_model_service_dep

    # AI 助手服务（优先从配置读取真实模型，未配置时用离线模式）
    set_ai_config_session_factory(session_factory)

    async def _get_ai_service_dep() -> AIService:
        config = await get_active_ai_config()
        if config and config.get("base_url") and config.get("api_key"):
            provider = OpenAICompatibleProvider(
                api_key=config["api_key"],
                base_url=config["base_url"],
                model=config["model_name"],
            )
        else:
            provider = OfflineProvider()
        tool_registry = ToolRegistry()
        return AIService(
            provider=provider,
            tool_registry=tool_registry,
            session_factory=session_factory,
        )

    app.dependency_overrides[get_ai_service] = _get_ai_service_dep

    yield

    # 清理
    app.dependency_overrides.clear()


async def _lookup_org_id(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    """从数据库查询用户的 organization_id。

    若用户无 organization_id（V0 早期数据），回退到 IRIP-DEMO 组织。
    """
    import sqlalchemy as sa

    from packages.auth.entities import AppUser

    async with session_factory() as session:
        user: AppUser | None = await session.scalar(
            sa.select(AppUser).where(AppUser.id == user_id)
        )
        if user is not None and user.organization_id is not None:
            return user.organization_id

    # 回退：查询 IRIP-DEMO 组织
    from packages.common.ids import new_id

    try:
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT id FROM organization WHERE code = 'IRIP-DEMO'")
            )
            row = result.scalar()
            if row is not None:
                return UUID(str(row))
    except Exception:
        pass

    # 最终回退：生成临时 UUID（不应发生，仅防止启动崩溃）
    return new_id()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    Returns:
        FastAPI: 已配置全部路由、中间件、异常处理器的应用实例。
    """
    app = FastAPI(
        title="IRIP",
        description="Industrial Research Intelligence Platform — API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---- CORS ----
    cors_origins = os.getenv("IRIP_API_CORS_ORIGINS", "http://localhost:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 路由 ----
    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """根路径重定向到 API 文档。"""
        return RedirectResponse(url="/docs")

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(uploads_router)
    app.include_router(artifacts_router)
    app.include_router(jobs_router)
    app.include_router(departments_router)
    app.include_router(equipment_router)
    app.include_router(user_departments_router)
    app.include_router(standards_router)
    app.include_router(objects_router)
    app.include_router(templates_router)
    app.include_router(methods_router)
    app.include_router(packages_router)
    app.include_router(ingestions_router)
    app.include_router(facts_router)
    app.include_router(provenance_router)
    app.include_router(parameters_router)
    app.include_router(components_router)
    app.include_router(flows_router)
    app.include_router(models_router)
    app.include_router(health_router)
    app.include_router(governance_router)
    app.include_router(audit_router)
    app.include_router(backups_router)
    app.include_router(assistant_router)
    app.include_router(ai_config_router)

    # ---- AppError 异常处理器 ----
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """将 AppError 映射为统一 JSON 错误响应。"""
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    return app


#: 模块级应用实例（供 uvicorn 直接引用）。
app: FastAPI = create_app()
