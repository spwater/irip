"""集成测试 fixtures。

优先使用 ``IRIP_TEST_DATABASE_URL`` 环境变量（指向已启动的测试数据库容器）；
若未设置，回退到 testcontainers 启动临时 PostgreSQL（pgvector/pgvector:pg16），
并在容器内自动执行 ``alembic upgrade head``。

典型用法（compose 方式，验收主路径）：
    export IRIP_DATABASE_URL=postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test
    export IRIP_TEST_DATABASE_URL=postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test
    docker compose -f deployments/compose/test.compose.yaml up -d postgres-test
    .venv/bin/python -m alembic upgrade head
    .venv/bin/python -m pytest tests/integration -v

T04 新增 fixtures：
- async_session_factory: 异步会话工厂（NullPool，适配 TestClient 事件循环）；
- token_secret: JWT 测试密钥；
- auth_service: AuthService 实例（LocalAuthBackend + AuthRepository）；
- api_client: FastAPI TestClient（挂载 auth_router + me_router + health + AppError 处理器）；
- seeded_user: 活跃测试用户（Correct-Horse-2026!）；
- seeded_disabled_user: 禁用测试用户。

T09 新增 fixtures：
- run_bootstrap: 幂等执行 bootstrap_platform（组织 → 角色 → 管理员 → bucket）；
- auth_repository: AuthRepository 实例（含 count_by_email，用于幂等性验证）；
- health_s3_repo: 连接 minio-test 的 S3 客户端（健康检查用）。
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from packages.auth.service import AuthService


def _run_alembic_upgrade(url: str) -> None:
    """以编程方式执行 alembic upgrade head（用于 testcontainers 路径）。"""
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def sync_engine() -> Iterator[Engine]:
    """提供同步 SQLAlchemy 引擎连接到测试数据库。

    路径 1（主）：``IRIP_TEST_DATABASE_URL`` 已设置 —— 迁移预期已由外部执行。
    路径 2（回退）：testcontainers 启动 pgvector 容器并自动执行迁移。
    两者均不可用时 skip。
    """
    url = os.getenv("IRIP_TEST_DATABASE_URL")
    if url:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            yield engine
        finally:
            engine.dispose()
        return

    # 回退：testcontainers
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed; set IRIP_TEST_DATABASE_URL")
        return  # 不可达，满足 mypy

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        container_url = pg.get_connection_url()
        # testcontainers 默认返回 psycopg2 驱动，切换为 psycopg 3
        container_url = container_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        _run_alembic_upgrade(container_url)
        engine = create_engine(container_url, pool_pre_ping=True)
        try:
            yield engine
        finally:
            engine.dispose()


# ---- T04: 异步会话工厂 ----


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


@pytest.fixture(scope="session")
def async_session_factory(sync_engine: Engine) -> async_sessionmaker[AsyncSession]:
    """提供异步会话工厂（NullPool，适配 TestClient 跨事件循环场景）。"""
    async_url = _to_async_url(sync_engine.url.render_as_string(hide_password=False))
    engine = create_async_engine(async_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---- T04: JWT 测试密钥 ----


@pytest.fixture
def token_secret() -> str:
    """JWT 签名密钥（测试固定值）。"""
    return "irip-test-jwt-secret-2026"


# ---- T04: AuthService 实例 ----


@pytest.fixture
def auth_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    token_secret: str,
) -> "AuthService":
    """构建 AuthService 实例（LocalAuthBackend + AuthRepository）。"""
    from packages.auth.backends import LocalAuthBackend
    from packages.auth.repository import AuthRepository
    from packages.auth.service import AuthService
    from packages.common.clock import SystemClock

    repository = AuthRepository()
    backend = LocalAuthBackend(repository)
    return AuthService(
        backend=backend,
        repository=repository,
        session_factory=async_session_factory,
        token_secret=token_secret,
        clock=SystemClock(),
    )


# ---- T04: API 测试客户端 ----


@pytest.fixture
def api_client(
    auth_service: "AuthService",
    token_secret: str,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> Iterator["TestClient"]:
    """提供 FastAPI TestClient（挂载认证路由 + 健康检查 + AppError 处理器）。

    依赖覆盖：
    - get_auth_service → auth_service fixture
    - get_token_secret → token_secret fixture
    - get_health_session_factory → async_session_factory fixture
    - get_redis_url → 从环境变量读取
    - get_s3_repo → health_s3_repo fixture
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from apps.api.dependencies.auth import get_token_secret
    from apps.api.routers.auth import (
        auth_router,
        get_auth_service,
        get_me_session_factory,
        me_router,
    )
    from apps.api.routers.health import (
        get_health_session_factory,
        get_redis_url,
        get_s3_repo,
        health_router,
    )
    from packages.common.errors import AppError

    app = FastAPI(title="IRIP Test")
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(health_router)

    # 覆盖认证依赖
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_token_secret] = lambda: token_secret
    app.dependency_overrides[get_me_session_factory] = lambda: async_session_factory

    # 覆盖健康检查依赖
    app.dependency_overrides[get_health_session_factory] = lambda: async_session_factory
    app.dependency_overrides[get_redis_url] = lambda: os.getenv(
        "IRIP_REDIS_URL", "redis://localhost:56379/0"
    )
    # S3 repo 需连接 minio-test；延迟创建避免无 MinIO 时全部测试跳过
    try:
        s3_repo = _build_health_s3_repo()
        app.dependency_overrides[get_s3_repo] = lambda: s3_repo
    except Exception:
        pass

    # AppError → JSON 统一错误响应
    _STATUS_MAP: dict[str, int] = {
        "invalid_credentials": 401,
        "token_expired": 401,
        "refresh_replayed": 401,
        "forbidden": 403,
        "not_found": 404,
        "conflict": 409,
        "validation_failed": 422,
        "internal_error": 500,
    }

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    client = TestClient(app)
    yield client


# ---- T04: 种子用户 ----


@dataclass(frozen=True)
class SeededUser:
    """种子用户信息（测试用）。"""

    user_id: UUID
    email: str
    password: str
    display_name: str = ""

    @property
    def id(self) -> UUID:
        """``id`` 别名，兼容测试中 ``seeded_user.id`` 写法。"""
        return self.user_id


def _insert_user(
    engine: Engine,
    email: str,
    display_name: str,
    password: str,
    status: str,
) -> UUID:
    """向数据库插入测试用户，返回用户 ID。"""
    from packages.auth.passwords import hash_password
    from packages.common.ids import new_id

    user_id = new_id()
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, email, display_name, password_hash, status, lock_version) "
                "VALUES (:id, :email, :name, :hash, :status, 0)"
            ),
            {
                "id": user_id,
                "email": email,
                "name": display_name,
                "hash": hash_password(password),
                "status": status,
            },
        )
        conn.commit()
    return user_id


def _cleanup_user(engine: Engine, user_id: UUID) -> None:
    """清理测试用户及其刷新会话。"""
    with engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM refresh_session WHERE user_id = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        conn.commit()


@pytest.fixture
def seeded_user(sync_engine: Engine) -> Iterator[SeededUser]:
    """活跃测试用户（密码 Correct-Horse-2026!）。"""
    email = "seeded@irip.local"
    password = "Correct-Horse-2026!"
    user_id = _insert_user(sync_engine, email, "Seeded User", password, "active")
    yield SeededUser(user_id=user_id, email=email, password=password, display_name="Seeded User")
    _cleanup_user(sync_engine, user_id)


@pytest.fixture
def seeded_disabled_user(sync_engine: Engine) -> Iterator[SeededUser]:
    """禁用测试用户（密码 Correct-Horse-2026!，status=disabled）。"""
    email = "disabled@irip.local"
    password = "Correct-Horse-2026!"
    user_id = _insert_user(sync_engine, email, "Disabled User", password, "disabled")
    yield SeededUser(user_id=user_id, email=email, password=password, display_name="Disabled User")
    _cleanup_user(sync_engine, user_id)


# ---- T09: 引导 fixtures ----


def _build_health_s3_repo():
    """构建连接 minio-test 的 S3 客户端（健康检查用）。"""
    from packages.common.s3_repository import S3Repository

    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "localhost:59000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"
    return S3Repository(
        endpoint_url=endpoint,
        access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        bucket_name=os.getenv("IRIP_MINIO_BUCKET", "irip-test"),
    )


@pytest.fixture
def health_s3_repo():
    """连接 minio-test 的 S3 客户端（健康检查用）。"""
    return _build_health_s3_repo()


@pytest.fixture
def auth_repository(
    async_session_factory: async_sessionmaker[AsyncSession],
):
    """AuthRepository 包装器（含 count_by_email，自动管理 session）。

    返回一个对象，其 count_by_email(email) 方法自动创建 session
    并调用 AuthRepository.count_by_email。
    """

    from packages.auth.repository import AuthRepository

    repo = AuthRepository()

    class _AuthRepositoryWrapper:
        """AuthRepository 测试包装器：自动管理 session。"""

        _repo = repo
        _factory = async_session_factory

        async def count_by_email(self, email: str) -> int:
            """按邮箱统计用户数。"""
            async with self._factory() as session:
                return await self._repo.count_by_email(session, email)

    return _AuthRepositoryWrapper()


@pytest.fixture
def run_bootstrap(
    async_session_factory: async_sessionmaker[AsyncSession],
):
    """幂等执行 bootstrap_platform 的可调用 fixture。

    返回一个 async 零参数协程函数，await 调用时执行引导：
      组织 → 角色 → 管理员 → bucket

    幂等：可安全多次调用。
    """
    from deployments.compose.bootstrap import ApplicationContainer, bootstrap_platform

    s3_repo = _build_health_s3_repo()
    container = ApplicationContainer(
        session_factory=async_session_factory,
        s3_repo=s3_repo,
    )

    async def _run() -> None:
        """执行幂等引导。"""
        await bootstrap_platform(container)

    return _run
