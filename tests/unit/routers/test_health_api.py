"""health_router API 测试：存活探针 + 就绪探针。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_health_session_factory、get_redis_url、get_s3_repo
- /live 始终返回 200，不检查依赖
- /ready 检查 DB + Redis + MinIO + Outbox + Worker，全部通过 200，任一失败 503
- mock redis.asyncio.from_url 和 redis.from_url 避免真实连接
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.routers.health import (
    get_health_session_factory,
    get_redis_url,
    get_s3_repo,
    health_router,
)
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_mock_s3_repo() -> MagicMock:
    """构造 mock S3Repository，ensure_bucket 为 MagicMock（同步调用）。"""
    repo = MagicMock()
    repo.ensure_bucket = MagicMock(return_value=None)
    repo.bucket = "irip-test"
    return repo


def _make_mock_session_factory(
    version: str = "0024",
    stale_count: int = 0,
) -> MagicMock:
    """构造 mock session_factory，模拟 DB 查询结果。

    Args:
        version: alembic_version 表返回的迁移版本号。
        stale_count: outbox 未投递事件数（0 = 健康）。
    """
    mock_session = AsyncMock()
    # 第一次 execute 返回 alembic_version 查询，第二次返回 outbox count 查询
    result1 = MagicMock()
    result1.scalar = MagicMock(return_value=version)
    result2 = MagicMock()
    result2.scalar = MagicMock(return_value=stale_count)
    mock_session.execute = AsyncMock(side_effect=[result1, result2])

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_app(
    session_factory: MagicMock | None = None,
    redis_url: str = "redis://localhost:6379/0",
    s3_repo: MagicMock | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(health_router)

    app.dependency_overrides[get_health_session_factory] = lambda: (
        session_factory or _make_mock_session_factory()
    )
    app.dependency_overrides[get_redis_url] = lambda: redis_url
    app.dependency_overrides[get_s3_repo] = lambda: s3_repo or _make_mock_s3_repo()

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    return app


def _patch_redis(heartbeat_val: bytes | None = b"0"):
    """patch redis.asyncio.from_url 和 redis.from_url。

    Args:
        heartbeat_val: worker heartbeat 的 Redis 返回值（None = 无心跳）。
    """
    async_client = MagicMock()
    async_client.ping = AsyncMock(return_value=True)
    async_client.aclose = AsyncMock(return_value=None)

    sync_client = MagicMock()
    sync_client.get = MagicMock(return_value=heartbeat_val)

    return (
        patch("redis.asyncio.from_url", return_value=async_client),
        patch("redis.from_url", return_value=sync_client),
        async_client,
        sync_client,
    )


# ===========================================================================
# 1. GET /api/v1/health/live — 存活探针
# ===========================================================================


class TestLiveness:
    """GET /api/v1/health/live — 存活探针（始终 200）。"""

    def test_live_always_200(self):
        """存活探针始终返回 200，不检查任何依赖。"""
        app = _make_app()
        client = TestClient(app)

        response = client.get("/api/v1/health/live")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ===========================================================================
# 2. GET /api/v1/health/ready — 就绪探针
# ===========================================================================


class TestReadiness:
    """GET /api/v1/health/ready — 就绪探针（DB + Redis + MinIO + Outbox + Worker）。"""

    def test_ready_all_healthy_200(self):
        """全部依赖健康 → 200"""
        app = _make_app()
        client = TestClient(app)

        # 用最近的时间戳模拟 worker 心跳健康
        heartbeat = str(time.time()).encode()
        p1, p2, _, _ = _patch_redis(heartbeat_val=heartbeat)
        with (
            p1,
            p2,
            patch(
                "apps.api.routers.health._get_expected_heads",
                return_value={"0024"},
            ),
        ):
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["checks"]["database"]["status"] == "ok"
        assert data["checks"]["redis"]["status"] == "ok"
        assert data["checks"]["minio"]["status"] == "ok"
        assert data["checks"]["outbox"]["status"] == "ok"
        assert data["checks"]["worker"]["status"] == "ok"

    def test_ready_db_failure_503(self):
        """DB 连接失败 → 503"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB connection refused"))
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        factory = MagicMock(return_value=mock_ctx)

        app = _make_app(session_factory=factory)
        client = TestClient(app)

        heartbeat = str(time.time()).encode()
        p1, p2, _, _ = _patch_redis(heartbeat_val=heartbeat)
        with p1, p2:
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "error"
        assert data["checks"]["database"]["status"] == "error"

    def test_ready_redis_failure_503(self):
        """Redis ping 失败 → 503"""
        app = _make_app()
        client = TestClient(app)

        # Redis 抛异常
        async_client = MagicMock()
        async_client.ping = AsyncMock(side_effect=Exception("Redis refused"))
        async_client.aclose = AsyncMock(return_value=None)
        sync_client = MagicMock()
        sync_client.get = MagicMock(return_value=str(time.time()).encode())

        with (
            patch("redis.asyncio.from_url", return_value=async_client),
            patch("redis.from_url", return_value=sync_client),
            patch(
                "apps.api.routers.health._get_expected_heads",
                return_value={"0024"},
            ),
        ):
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["redis"]["status"] == "error"

    def test_ready_minio_failure_503(self):
        """MinIO bucket 不可访问 → 503"""
        s3_repo = _make_mock_s3_repo()
        s3_repo.ensure_bucket = MagicMock(side_effect=Exception("MinIO down"))

        app = _make_app(s3_repo=s3_repo)
        client = TestClient(app)

        heartbeat = str(time.time()).encode()
        p1, p2, _, _ = _patch_redis(heartbeat_val=heartbeat)
        with (
            p1,
            p2,
            patch(
                "apps.api.routers.health._get_expected_heads",
                return_value={"0024"},
            ),
        ):
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["minio"]["status"] == "error"

    def test_ready_outbox_stale_events_503(self):
        """Outbox 有超时未投递事件 → 503"""
        app = _make_app(session_factory=_make_mock_session_factory(stale_count=5))
        client = TestClient(app)

        heartbeat = str(time.time()).encode()
        p1, p2, _, _ = _patch_redis(heartbeat_val=heartbeat)
        with (
            p1,
            p2,
            patch(
                "apps.api.routers.health._get_expected_heads",
                return_value={"0024"},
            ),
        ):
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["outbox"]["status"] == "error"
        assert data["checks"]["outbox"]["stale_undelivered"] == 5

    def test_ready_worker_no_heartbeat_503(self):
        """Worker 无心跳记录 → 503"""
        app = _make_app()
        client = TestClient(app)

        # heartbeat 返回 None = 无心跳
        p1, p2, _, _ = _patch_redis(heartbeat_val=None)
        with (
            p1,
            p2,
            patch(
                "apps.api.routers.health._get_expected_heads",
                return_value={"0024"},
            ),
        ):
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["worker"]["status"] == "error"

    def test_ready_worker_stale_heartbeat_503(self):
        """Worker 心跳超时 → 503"""
        app = _make_app()
        client = TestClient(app)

        # 心跳时间戳为 2 小时前（超过 60 秒阈值）
        stale_heartbeat = str(time.time() - 7200).encode()
        p1, p2, _, _ = _patch_redis(heartbeat_val=stale_heartbeat)
        with (
            p1,
            p2,
            patch(
                "apps.api.routers.health._get_expected_heads",
                return_value={"0024"},
            ),
        ):
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["worker"]["status"] == "error"
        assert data["checks"]["worker"]["last_heartbeat_age_seconds"] > 60
