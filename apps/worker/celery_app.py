"""Celery 应用配置（broker=redis, backend=redis）。

Phase V0 T07: 配置 Celery 异步任务队列，broker 和 backend 均使用 Redis。

任务命名约定（docs/arch-v0.md §7.6）：
  <domain>.<verb>（如 ``jobs.execute``）

Worker 心跳间隔 10s，租约 TTL 30s，到期后由 reaper 重新入队。

F-19 可观测性增强：
  - worker.heartbeat 任务记录 Prometheus 心跳时间戳指标；
  - 提供 ``run_worker_healthcheck_server()`` 轻量 HTTP 健康检查端点，
    供 Kubernetes/Docker healthcheck 探测 Worker 进程存活状态。
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from celery import Celery

#: Redis URL（从环境变量读取，默认本地测试 Redis）。
REDIS_URL: str = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")

#: Worker 健康检查 HTTP 端口（可通过环境变量覆盖）。
WORKER_HEALTHCHECK_PORT: int = int(os.getenv("IRIP_WORKER_HEALTHCHECK_PORT", "9100"))

#: Celery 应用实例。
celery_app: Celery = Celery(
    "irip",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["apps.worker.tasks"],
)

#: Celery 配置。
celery_app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    timezone="UTC",
    enable_utc=True,
    # 可靠性
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="irip-jobs",
    # 预取：一次只取一个任务（确保长任务不阻塞短任务）
    worker_prefetch_multiplier=1,
    # 重试配置
    task_default_max_retries=3,
    # 结果过期时间（7 天）
    result_expires=7 * 24 * 3600,
    # Celery Beat 调度配置（F-04：Outbox 闭环）
    beat_schedule={
        # Outbox 事件投递调度：每 5 秒拉取未投递事件发送到 Celery
        "dispatch-outbox": {
            "task": "outbox.dispatch",
            "schedule": 5.0,
        },
        # Worker 心跳：每 10 秒发送心跳
        "worker-heartbeat": {
            "task": "worker.heartbeat",
            "schedule": 10.0,
        },
        # 过期租约回收：每 30 秒回收过期租约
        "reap-expired-leases": {
            "task": "worker.reap_expired_leases",
            "schedule": 30.0,
        },
        # 重试等待作业重新入队：每 15 秒扫描 retry_wait 状态作业
        "retry-wait-jobs": {
            "task": "worker.retry_wait_jobs",
            "schedule": 15.0,
        },
    },
)


@celery_app.task(name="jobs.execute", bind=True)
def execute_job(self: object, job_id: str) -> str:
    """Celery 任务入口：执行作业。

    此任务由 OutboxDispatcher 通过 celery.send_task() 触发。
    实际执行逻辑由 JobExecutor 负责（在 worker 进程中通过 DI 注入）。

    Args:
        self: Celery 任务实例（bind=True）。
        job_id: 作业 UUID 字符串。

    Returns:
        str: 作业 UUID（用于结果追踪）。
    """
    # 实际执行逻辑在 apps/worker/tasks.py 中组装
    from apps.worker.tasks import _do_execute_job

    return _do_execute_job(job_id)


@celery_app.task(name="outbox.dispatch")
def dispatch_outbox() -> int:
    """Celery Beat 调度任务：拉取 Outbox 未投递事件并发送到 Celery。

    技术设计文档 F-04：由 Beat 每 5 秒触发，使用 FOR UPDATE SKIP LOCKED
    拉取 pending 事件，通过 send_task 发送到 irip-jobs 队列。

    Phase 3 架构收敛（T3-3）：作为 ``packages`` 层的组装/注入点，在此将
    本模块的 ``celery_app`` 作为 ``task_sender`` 注入 ``run_dispatch``，
    使 ``packages.jobs`` 不再直接依赖 ``apps.worker.celery_app``。

    Returns:
        int: 已投递事件数。
    """
    from packages.jobs.dispatcher import run_dispatch

    return run_dispatch(task_sender=celery_app)


@celery_app.task(name="worker.heartbeat")
def worker_heartbeat() -> str:
    """Celery Beat 调度任务：Worker 心跳。

    F-19：每次心跳执行时，将心跳时间戳写入 Redis（共享存储），
    供 API readiness 探针检查 Worker 是否在最近 N 秒内有过心跳。

    Returns:
        str: 心跳确认消息。
    """
    try:
        import time

        import redis

        redis_url = os.getenv("IRIP_REDIS_URL", "redis://redis:6379/0")
        r = redis.from_url(redis_url)
        r.set("irip:worker:heartbeat", str(time.time()), ex=120)
    except Exception:
        # 心跳记录失败不应影响心跳任务本身
        pass
    return "heartbeat-ok"


@celery_app.task(name="worker.reap_expired_leases")
def reap_expired_leases() -> int:
    """Celery Beat 调度任务：回收过期租约。

    将 running 状态且租约过期的作业重新入队（status→queued）。

    Returns:
        int: 被回收的作业数。
    """
    import asyncio
    import os

    from packages.common.database import build_session_factory
    from packages.jobs.worker import WorkerLeaseManager

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    else:
        async_url = db_url

    factory = build_session_factory(async_url)
    lease_manager = WorkerLeaseManager(factory)

    async def _reap() -> list:
        return await lease_manager.reap_expired()

    result = asyncio.run(_reap())
    return len(result)


@celery_app.task(name="worker.retry_wait_jobs")
def retry_wait_jobs() -> int:
    """Celery Beat 调度任务：重新入队 retry_wait 状态且已到 run_after 的作业。

    Returns:
        int: 重新入队的作业数。
    """
    import asyncio
    import os

    import sqlalchemy as sa
    from packages.common.database import build_session_factory, session_scope
    from packages.jobs.entities import Job, JobStatus

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    else:
        async_url = db_url

    factory = build_session_factory(async_url)

    async def _retry() -> int:
        from packages.common.clock import SystemClock

        clock = SystemClock()
        count = 0
        async with session_scope(factory) as session:
            result = await session.execute(
                sa.select(Job).where(
                    Job.status == JobStatus.RETRY_WAIT.value,
                    Job.run_after <= clock.now(),
                )
            )
            jobs = list(result.scalars().all())
            for job in jobs:
                await session.execute(
                    sa.update(Job)
                    .values(
                        status=JobStatus.QUEUED.value,
                        updated_at=sa.func.now(),
                        lock_version=Job.lock_version + 1,
                    )
                    .where(Job.id == job.id)
                )
                count += 1
        return count

    return asyncio.run(_retry())


# ---- F-19: Worker 健康检查 HTTP 端点 ----


class _HealthcheckHandler(BaseHTTPRequestHandler):
    """Worker 健康检查 HTTP 请求处理器。

    响应 ``GET /health`` 返回 200 ``{"status": "ok"}``。
    只要 Worker 进程能响应即认为存活（不检查 broker 连接，
    broker 连接状态由 readiness 探针通过心跳时间戳检查）。
    """

    def do_GET(self) -> None:
        """处理 GET 请求：返回健康状态 JSON。"""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """禁用默认日志输出（避免干扰 Celery 日志）。"""
        pass


def run_worker_healthcheck_server(
    port: int | None = None,
    block: bool = False,
) -> HTTPServer | None:
    """启动 Worker 健康检查 HTTP 服务器。

    在 Worker 进程启动时以守护线程方式运行，供 Kubernetes liveness probe
    或 Docker healthcheck 探测 Worker 进程是否存活。

    Args:
        port: 健康检查 HTTP 端口（默认从环境变量 IRIP_WORKER_HEALTHCHECK_PORT 读取）。
        block: True 时阻塞当前线程（用于独立运行模式），False 时以守护线程运行。

    Returns:
        HTTPServer | None: 非阻塞模式返回 HTTPServer 实例，阻塞模式返回 None。
    """
    listen_port: int = port if port is not None else WORKER_HEALTHCHECK_PORT
    server: HTTPServer = HTTPServer(
        ("0.0.0.0", listen_port), _HealthcheckHandler
    )

    if block:
        server.serve_forever()
        return None

    # 守护线程模式：随主线程退出自动终止
    thread: threading.Thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="worker-healthcheck",
    )
    thread.start()
    return server
