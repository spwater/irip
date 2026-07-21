"""V0 引导幂等性测试（实施计划 Task 9 Step 1）。

验证 bootstrap_platform 两次运行的幂等性：
  1. run_bootstrap() 两次 → health/ready 返回 200；
  2. admin@irip.local 用户仅存在一个。

前置依赖：
  - 测试数据库已启动并已执行 alembic upgrade head；
  - Redis 已启动（redis-test）；
  - MinIO 已启动（minio-test）。
"""

import pytest


@pytest.mark.integration
async def test_bootstrap_is_idempotent(
    api_client,
    run_bootstrap,
    auth_repository,
) -> None:
    """引导两次：health/ready 200 + admin 用户唯一。"""
    await run_bootstrap()
    await run_bootstrap()

    health = api_client.get("/api/v1/health/ready")
    assert health.status_code == 200

    count = await auth_repository.count_by_email("admin@irip.local")
    assert count == 1


@pytest.mark.integration
async def test_health_live_always_ok(api_client) -> None:
    """存活探针始终返回 200。"""
    response = api_client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.integration
async def test_health_ready_after_bootstrap(
    api_client,
    run_bootstrap,
) -> None:
    """引导后就绪探针返回 200 且各检查项全部 ok。"""
    await run_bootstrap()

    response = api_client.get("/api/v1/health/ready")
    assert response.status_code == 200
    data = response.json()
    checks = data["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["redis"]["status"] == "ok"
    assert checks["minio"]["status"] == "ok"
    assert checks["outbox"]["status"] == "ok"
