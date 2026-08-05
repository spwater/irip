"""FastAPI 应用工厂：创建并配置 IRIP API 应用。

职责（实施计划 Task 9）：
  - 创建 FastAPI app，挂载全部路由（auth, uploads, jobs, health）；
  - CORS 中间件（允许前端 origin）；
  - AppError 异常处理器（返回统一 JSON 错误格式）；
  - lifespan：启动时初始化 DB session factory、S3 client、Redis client，
    并通过 composition provider 模块设置依赖覆盖。

启动命令：
  uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from apps.api.routers.account import account_router
from apps.api.routers.ai_config import ai_config_router
from apps.api.routers.ai_tools import ai_tools_router
from apps.api.routers.assistant import assistant_router
from apps.api.routers.audit import audit_router
from apps.api.routers.auth import auth_router, me_router
from apps.api.routers.backups import backups_router
from apps.api.routers.collaboration import collaboration_router
from apps.api.routers.component_preview import component_preview_router
from apps.api.routers.components import components_router
from apps.api.routers.departments import departments_router
from apps.api.routers.equipment import equipment_router
from apps.api.routers.experiment_projects import experiment_projects_router
from apps.api.routers.facts import facts_router
from apps.api.routers.files import files_router
from apps.api.routers.flows import flows_router
from apps.api.routers.governance import governance_router
from apps.api.routers.health import health_router
from apps.api.routers.ingestions import ingestions_router
from apps.api.routers.jobs import jobs_router
from apps.api.routers.models import models_router
from apps.api.routers.object_types import object_types_router
from apps.api.routers.objects import objects_router
from apps.api.routers.parameters import parameters_router
from apps.api.routers.provenance import provenance_router
from apps.api.routers.showcase import showcase_router
from apps.api.routers.uploads import artifacts_router, uploads_router
from apps.api.routers.user_departments import user_departments_router
from packages.common.database import build_session_factory
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.common.s3_repository import S3Repository

#: AppError code → HTTP 状态码映射，由 ErrorCode 封闭枚举自动生成（F-14/F-24）。
#: 新增错误码只需在 ErrorCode 枚举中注册，无需维护手工映射。
#: 未知 code 默认映射为 500（见 handle_app_error 异常处理器中的兜底逻辑）。
_STATUS_MAP: dict[str, int] = ErrorCode.to_status_map()


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


def _build_s3_repo() -> S3Repository:
    """从环境变量构建 S3 客户端。"""
    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"
    external_endpoint = os.getenv("IRIP_MINIO_EXTERNAL_ENDPOINT")
    if external_endpoint and not external_endpoint.startswith("http"):
        external_endpoint = f"http://{external_endpoint}"
    return S3Repository(
        endpoint_url=endpoint,
        access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        bucket_name=os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
        region=os.getenv("IRIP_MINIO_REGION", "us-east-1"),
        external_endpoint_url=external_endpoint,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化资源，退出时清理。

    初始化内容：
      1. 数据库会话工厂（从 IRIP_DATABASE_URL）；
      2. S3 / MinIO 客户端（ensure_bucket 幂等创建）；
      3. Redis URL；
      4. JWT 密钥；
      5. 通过 composition provider 模块设置全部依赖覆盖。
    """
    from apps.api.composition import CompositionContext, lookup_root_dept_id, register_all

    # ---- 1. 数据库会话工厂 ----
    db_url = os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL environment variable is required")
    async_url = _to_async_url(db_url)
    session_factory = build_session_factory(async_url)

    # ---- 1b. 安全断言：拒绝 superuser/bypassrls 运行时连接 ----
    # RLS 是唯一隔离层，运行时连接角色不能是 superuser 或 bypassrls，
    # 否则 RLS 将被绕过，纵深归零。
    from sqlalchemy import text as _sa_text

    async with session_factory() as _assert_session:
        _result = await _assert_session.execute(
            _sa_text(
                "SELECT rolsuper, rolbypassrls, current_user "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
        _row = _result.fetchone()
        if _row and (_row[0] or _row[1]):
            raise RuntimeError(
                f"安全断言失败：运行时连接角色 {_row[2]} 是 superuser 或 bypassrls，"
                "RLS 将被绕过。请检查 IRIP_DATABASE_APP_USER 配置。"
            )

    # ---- 2. S3 / MinIO ----
    s3_repo = _build_s3_repo()
    s3_repo.ensure_bucket()

    # ---- 3. Redis URL ----
    redis_url = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")

    # ---- 4. JWT 密钥 ----
    token_secret = os.getenv("IRIP_JWT_SECRET", "irip-dev-secret-2026")

    # ---- 5. 依赖覆盖（按领域 provider 模块注册） ----
    # 查询 root 部门 ID（用于平台管理员/平台监督员 RLS 绕过）
    root_dept_id = await lookup_root_dept_id(session_factory)
    ctx = CompositionContext(
        app=app,
        session_factory=session_factory,
        s3_repo=s3_repo,
        redis_url=redis_url,
        token_secret=token_secret,
        root_dept_id=root_dept_id,
    )
    register_all(ctx)

    # ---- 6. AI 工具种子数据（表空时写入 12 条内置工具，幂等） ----
    from packages.ai.tool_seeding import seed_tools_if_empty
    from packages.common.database import session_scope

    async with session_scope(session_factory) as session:
        await seed_tools_if_empty(session)

    yield

    # 清理
    app.dependency_overrides.clear()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    Returns:
        FastAPI: 已配置全部路由、中间件、异常处理器的应用实例。
    """
    app = FastAPI(
        title="IRIP",
        description="Industrial Research Intelligence Platform — API",
        version="0.8.0",
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

    # ---- F-19: 结构化日志 + Correlation ID 中间件 ----
    from packages.common.logging_setup import (
        CorrelationIdMiddleware,
        configure_logging,
    )
    from packages.common.metrics import metrics_middleware, set_app_info

    configure_logging(
        service_name="api",
        json_output=os.getenv("IRIP_LOG_JSON", "true").lower() == "true",
    )
    app.add_middleware(CorrelationIdMiddleware)
    # 注意：metrics_middleware 需通过 BaseHTTPMiddleware 挂载
    from starlette.middleware.base import BaseHTTPMiddleware

    app.add_middleware(BaseHTTPMiddleware, dispatch=metrics_middleware)
    set_app_info(version="0.8.0", environment=os.getenv("IRIP_ENV", "development"))

    # ---- 路由 ----
    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """根路径重定向到 API 文档。"""
        return RedirectResponse(url="/docs")

    # ---- F-19: Prometheus 指标端点 ----
    from fastapi import Response

    from packages.common.metrics import generate_metrics

    @app.get("/api/v1/metrics", include_in_schema=False, tags=["metrics"])
    async def metrics_endpoint() -> Response:
        """Prometheus 指标暴露端点（F-19）。

        返回 Prometheus exposition 格式文本，供 Prometheus scrape 抓取。
        """
        return Response(
            content=generate_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(uploads_router)
    app.include_router(artifacts_router)
    app.include_router(jobs_router)
    app.include_router(departments_router)
    app.include_router(equipment_router)
    app.include_router(user_departments_router)
    app.include_router(objects_router)
    app.include_router(object_types_router)
    app.include_router(ingestions_router)
    app.include_router(facts_router)
    app.include_router(provenance_router)
    app.include_router(parameters_router)
    app.include_router(components_router)
    app.include_router(flows_router)
    app.include_router(experiment_projects_router)
    app.include_router(models_router)
    app.include_router(health_router)
    app.include_router(governance_router)
    app.include_router(audit_router)
    app.include_router(backups_router)
    app.include_router(assistant_router)
    app.include_router(showcase_router)
    app.include_router(ai_config_router)
    app.include_router(ai_tools_router)
    app.include_router(files_router)
    app.include_router(component_preview_router)
    # irip-ai-collab: 协作 + 账户管理路由
    app.include_router(collaboration_router)
    app.include_router(account_router)

    # ---- AppError 异常处理器 ----
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """将 AppError 映射为统一 JSON 错误响应。"""
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """捕获未预期异常：服务端记录完整 traceback，客户端始终返回脱敏消息。"""
        import logging

        logging.getLogger("api").exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "服务器内部错误，请联系管理员",
                }
            },
        )

    return app


#: 模块级应用实例（供 uvicorn 直接引用）。
app: FastAPI = create_app()
