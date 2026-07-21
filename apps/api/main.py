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
from apps.api.routers.departments import departments_router
from apps.api.routers.health import (
    get_health_session_factory,
    get_redis_url,
    get_s3_repo,
    health_router,
)
from apps.api.routers.jobs import get_job_service, jobs_router
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
from packages.departments.service import DepartmentService
from packages.departments.user_departments import UserDepartmentService
from packages.jobs.service import JobService

#: AppError code → HTTP 状态码映射（docs/arch-v0.md §7.2）。
_STATUS_MAP: dict[str, int] = {
    "invalid_credentials": 401,
    "token_expired": 401,
    "refresh_replayed": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "unsupported_media_type": 415,
    "hash_mismatch": 422,
    "size_mismatch": 422,
    "validation_failed": 422,
    "internal_error": 500,
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

    # /me 端点用的 DB 会话工厂
    app.dependency_overrides[get_me_session_factory] = lambda: session_factory

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
    app.include_router(user_departments_router)
    app.include_router(health_router)

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
