"""健康检查端点：存活与就绪探针。

端点（docs/arch-v0.md §8.3 V0 验收标准 + 实施计划 Task 9）：
  GET /api/v1/health/live  — 存活检查（always 200，不检查依赖）
  GET /api/v1/health/ready — 就绪检查（DB + Redis + MinIO + Outbox + Worker heartbeat）

就绪检查各项：
  1. DB 迁移 head 匹配（SELECT version FROM alembic_version）
  2. Redis ping
  3. MinIO bucket 可访问
  4. Outbox dispatcher 心跳（无超过 N 秒未投递的事件）
  5. Worker heartbeat（最近 N 秒内有心跳记录）

全部通过返回 200；任一失败返回 503 + 详细状态。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# health 路由使用 text() 执行原生 SQL 探针查询（检查迁移版本 + outbox 心跳），
# 这属于基础设施健康检查，不是业务 ORM 查询，保留在路由层。
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.schemas.common import StatusResponse
from packages.common.redis_url import get_redis_url as _read_redis_url
from packages.common.s3_repository import S3Repository

#: 路由实例。
health_router = APIRouter(prefix="/api/v1/health", tags=["health"])

#: Outbox 心跳阈值：超过此秒数仍未投递的事件视为 dispatcher 不健康。
OUTBOX_HEARTBEAT_MAX_AGE_SECONDS: int = 120

#: Worker 心跳阈值：超过此秒数无心跳记录视为 Worker 不健康（F-19）。
WORKER_HEARTBEAT_MAX_AGE_SECONDS: int = 60


def _get_expected_heads() -> set[str]:
    """从 Alembic ScriptDirectory 动态读取代码期望的迁移 head 集合。

    替代原先硬编码的 ``EXPECTED_MIGRATION_HEAD = "0024"``，避免迁移新增后
    readiness 检查误报 head 不一致。

    Returns:
        set[str]: 期望的迁移 head revision 集合（通常只有 1 个）。
    """
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    # 以本文件所在位置推断项目根目录下的 migrations 目录
    migrations_path = Path(__file__).resolve().parents[3] / "migrations"
    config.set_main_option("script_location", str(migrations_path))
    script_dir = ScriptDirectory.from_config(config)
    return {rev.revision for rev in script_dir.get_revisions("heads")}


# ---- 依赖占位（由应用启动或测试覆盖）----


def get_health_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_health_session_factory must be overridden via dependency_overrides"
    )


def get_redis_url() -> str:
    """获取 Redis 连接 URL（file-backed secret 优先，env 回退）。"""
    return _read_redis_url()


def get_s3_repo() -> S3Repository:
    """获取 S3 客户端（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_s3_repo must be overridden via dependency_overrides")


#: 依赖类型别名。
HealthSessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_health_session_factory)
]
RedisUrlDep = Annotated[str, Depends(get_redis_url)]
S3RepoDep = Annotated[S3Repository, Depends(get_s3_repo)]


# ---- 端点 ----


@health_router.get("/live", response_model=StatusResponse)
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
      1. DB 迁移版本匹配从 Alembic ScriptDirectory 动态读取的 head 集合；
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
        expected_heads = _get_expected_heads()
        async with session_factory() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            version: str | None = result.scalar()
            if version not in expected_heads:
                checks["database"] = {
                    "status": "error",
                    "expected": sorted(expected_heads),
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

        client = aioredis.from_url(redis_url)
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
                text(
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

    # ---- 5. Worker heartbeat（F-19，通过 Redis 共享） ----
    try:
        import time

        import redis

        r = redis.from_url(redis_url)
        raw = r.get("irip:worker:heartbeat")

        if raw is not None:
            latest_heartbeat: float = float(raw)
            age_seconds: float = time.time() - latest_heartbeat
            if age_seconds > WORKER_HEARTBEAT_MAX_AGE_SECONDS:
                checks["worker"] = {
                    "status": "error",
                    "last_heartbeat_age_seconds": round(age_seconds, 1),
                    "threshold_seconds": WORKER_HEARTBEAT_MAX_AGE_SECONDS,
                }
                all_ok = False
            else:
                checks["worker"] = {
                    "status": "ok",
                    "last_heartbeat_age_seconds": round(age_seconds, 1),
                }
        else:
            # 无心跳记录：Worker 可能尚未启动或 Beat 未调度
            checks["worker"] = {
                "status": "error",
                "error": "no worker heartbeat recorded",
            }
            all_ok = False
    except Exception as exc:
        checks["worker"] = {"status": "error", "error": str(exc)}
        all_ok = False

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if all_ok else "error",
            "checks": checks,
        },
    )
