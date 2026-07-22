"""Celery 任务实际执行逻辑。

在 worker 进程中通过 DI 组装 JobExecutor 并执行作业。
此模块在 celery_app.py 的 execute_job 任务中被调用。
"""

import asyncio
import os
from uuid import UUID

from packages.common.database import build_session_factory
from packages.jobs.worker import JobExecutor, WorkerLeaseManager


async def _execute_job_async(job_id: str) -> str:
    """异步执行作业。

    构建 session_factory、租约管理器和执行器，然后执行作业。

    Args:
        job_id: 作业 UUID 字符串。

    Returns:
        str: 作业 UUID。
    """
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
    executor = JobExecutor(lease_manager, factory)

    result = await executor.execute(UUID(job_id), owner="celery-worker")
    return str(result.job_id) if result else job_id


def _do_execute_job(job_id: str) -> str:
    """同步入口：在事件循环中执行异步作业。

    Args:
        job_id: 作业 UUID 字符串。

    Returns:
        str: 作业 UUID。
    """
    return asyncio.run(_execute_job_async(job_id))


# 导入各领域任务，确保 Celery 能发现它们
from apps.worker.tasks.flows import execute_flow_job, resume_flow_job  # noqa: E402, F401
from apps.worker.tasks.ingestion import process_ingestion_job  # noqa: E402, F401
from apps.worker.tasks.models import (  # noqa: E402, F401
    predict_model_job,
    publish_model_job,
    train_model_job,
)
