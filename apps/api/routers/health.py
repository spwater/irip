"""健康检查端点：存活与就绪探针。

端点（docs/arch-v0.md §8.3 V0 验收标准 + 实施计划 Task 9）：
  GET /api/v1/health/live  — 存活检查（always 200，不检查依赖）
  GET /api/v1/health/ready — 就绪检查（DB + Redis + MinIO + Outbox）

就绪检查各项：
  1. DB 迁移 head 匹配（SELECT version FROM alembic_version）
  2. Redis ping
  3. MinIO bucket 可访问
  4. Outbox dispatcher 心跳（无超过 N 秒未投递的事件）

全部通过返回 200；任一失败返回 503 + 详细状态。
"""

import os
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.s3_repository import S3Repository

#: 路由实例。
health_router = APIRouter(prefix="/api/v1/health", tags=["health"])

#: 期望的 Alembic 迁移 head 版本（0006 = 机构/实验室管理迁移）。
EXPECTED_MIGRATION_HEAD: str = "0006"

#: Outbox 心跳阈值：超过此秒数仍未投递的事件视为 dispatcher 不健康。
OUTBOX_HEARTBEAT_MAX_AGE_SECONDS: int = 120


# ---- 依赖占位（由应用启动或测试覆盖）----


def get_health_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_health_session_factory must be overridden via dependency_overrides"
    )


def get_redis_url() -> str:
    """获取 Redis 连接 URL（从环境变量读取）。"""
    return os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")


def get_s3_repo() -> S3Repository:
    """获取 S3 客户端（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_s3_repo must be overridden via dependency_overrides"
    )


#: 依赖类型别名。
HealthSessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_health_session_factory)
]
RedisUrlDep = Annotated[str, Depends(get_redis_url)]
S3RepoDep = Annotated[S3Repository, Depends(get_s3_repo)]


# ---- 端点 ----


@health_router.get("/live")
async def liveness() -> dict[str, str]:
    """存活探针：始终返回 200，不检查任何依赖。

    用于 Kubernetes liveness probe / Docker healthcheck，
    只要进程能响应即认为存活。
    """
    return {"status": "ok"}


@health_router.get("/ready")
async def readiness(
    session_factory: HealthSessionFactoryDep,
    redis_url: RedisUrlDep,
    s3_repo: S3RepoDep,
) -> JSONResponse:
    """就绪探针：检查 DB、Redis、MinIO、Outbox 全部依赖。

    检查项：
      1. DB 迁移版本匹配 EXPECTED_MIGRATION_HEAD；
      2. Redis ping 成功；
      3. MinIO bucket 可访问；
      4. Outbox 无超过 OUTBOX_HEARTBEAT_MAX_AGE_SECONDS 秒的未投递事件。

    Returns:
        JSONResponse: 200（全部通过）或 503（任一失败），
        body 含各检查项详细状态。
    """
    checks: dict[str, object] = {}
    all_ok: bool = True

    # ---- 1. DB 迁移 head ----
    try:
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
            version: str | None = result.scalar()
            if version != EXPECTED_MIGRATION_HEAD:
                checks["database"] = {
                    "status": "error",
                    "expected": EXPECTED_MIGRATION_HEAD,
                    "actual": version,
                }
                all_ok = False
            else:
                checks["database"] = {"status": "ok", "version": version}
    except Exception as exc:
        checks["database"] = {"status": "error", "error": str(exc)}
        all_ok = False

    # ---- 2. Redis ping ----
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url)  # type: ignore[no-untyped-call]
        await client.ping()
        await client.aclose()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        checks["redis"] = {"status": "error", "error": str(exc)}
        all_ok = False

    # ---- 3. MinIO bucket ----
    try:
        s3_repo.ensure_bucket()
        checks["minio"] = {"status": "ok", "bucket": s3_repo.bucket}
    except Exception as exc:
        checks["minio"] = {"status": "error", "error": str(exc)}
        all_ok = False

    # ---- 4. Outbox dispatcher 心跳 ----
    try:
        async with session_factory() as session:
            result = await session.execute(
                sa.text(
                    "SELECT count(*) FROM outbox_event "
                    "WHERE delivered_at IS NULL "
                    f"AND occurred_at < now() - "
                    f"interval '{OUTBOX_HEARTBEAT_MAX_AGE_SECONDS} seconds'"
                )
            )
            stale_count: int = result.scalar() or 0
            if stale_count > 0:
                checks["outbox"] = {
                    "status": "error",
                    "stale_undelivered": stale_count,
                    "threshold_seconds": OUTBOX_HEARTBEAT_MAX_AGE_SECONDS,
                }
                all_ok = False
            else:
                checks["outbox"] = {"status": "ok"}
    except Exception as exc:
        checks["outbox"] = {"status": "error", "error": str(exc)}
        all_ok = False

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if all_ok else "error",
            "checks": checks,
        },
    )
