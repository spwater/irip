"""Celery 应用配置（broker=redis, backend=redis）。

Phase V0 T07: 配置 Celery 异步任务队列，broker 和 backend 均使用 Redis。

任务命名约定（docs/arch-v0.md §7.6）：
  <domain>.<verb>（如 ``jobs.execute``）

Worker 心跳间隔 10s，租约 TTL 30s，到期后由 reaper 重新入队。
"""

import os

from celery import Celery

#: Redis URL（从环境变量读取，默认本地测试 Redis）。
REDIS_URL: str = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")

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
    # 这里仅提供 Celery 任务注册点
    from apps.worker.tasks import _do_execute_job

    return _do_execute_job(job_id)
