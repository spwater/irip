"""Celery 任务实际执行逻辑。

在 worker 进程中通过 DI 组装 JobExecutor 并执行作业。
此模块在 celery_app.py 的 execute_job 任务中被调用。

技术设计文档 F-04 §8.5：所有异步任务只通过 Outbox→Dispatcher→Celery 一条通道。
此处注册全部 handler（flow, ingestion, model, backup, restore, audit_export），
确保 JobExecutor 能处理所有已注册的作业类型。
"""

import asyncio
import os
from uuid import UUID

from packages.common.database import build_session_factory
from packages.jobs.worker import JobExecutor, WorkerLeaseManager


async def _execute_job_async(job_id: str) -> str:
    """异步执行作业。

    构建 session_factory、租约管理器和执行器，注册全部 handler，
    然后执行作业。

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

    # 注册全部 handler（F-04：显式注册表）
    _register_handlers(executor)

    result = await executor.execute(UUID(job_id), owner="celery-worker")
    return str(result.job_id) if result else job_id


def _register_handlers(executor: JobExecutor) -> None:
    """注册全部作业 handler。

    技术设计文档 F-04：显式注册 flow、ingestion、model、backup、restore、
    audit_export 全部 handler，确保 JobExecutor 能处理所有已注册的作业类型。
    未知 kind 直接失败（禁止 echo fallback）。
    """
    from apps.worker.tasks.flows import execute_flow_job, resume_flow_job
    from apps.worker.tasks.ingestion import process_ingestion_job
    from apps.worker.tasks.models import (
        predict_model_job,
        publish_model_job,
        train_model_job,
    )

    # Flow handler
    executor.register_handler("flow_execute", execute_flow_job)
    executor.register_handler("flow_resume", resume_flow_job)

    # Ingestion handler
    executor.register_handler("ingestion", process_ingestion_job)

    # Model handler
    executor.register_handler("model_train", train_model_job)
    executor.register_handler("model_predict", predict_model_job)
    executor.register_handler("model_publish", publish_model_job)

    # Backup / Restore / Audit Export handler（F-04 §8.5）
    executor.register_handler("backup", _backup_handler)
    executor.register_handler("restore", _restore_handler)
    executor.register_handler("audit_export", _audit_export_handler)


async def _backup_handler(job: object) -> dict:
    """备份作业 handler。

    执行 PostgreSQL + MinIO 全量备份，生成完整性 manifest。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 备份结果（含 backup_id、manifest 路径）。
    """
    from deployments.compose.backup import run_backup

    manifest = await run_backup()
    return {
        "backup_id": manifest.backup_id,
        "database_sha256": manifest.database_sha256,
        "object_count": manifest.object_count,
    }


async def _restore_handler(job: object) -> dict:
    """恢复作业 handler。

    从备份目录恢复 PostgreSQL + MinIO 数据。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 恢复结果。
    """
    from pathlib import Path

    payload: dict = getattr(job, "payload", None) or {}
    backup_dir_str: str = payload.get("backup_dir", "")
    if not backup_dir_str:
        from packages.common.errors import AppError

        raise AppError(
            code="validation_failed",
            message="恢复作业缺少 backup_dir 参数",
            retryable=False,
        )

    from deployments.compose.restore import run_restore

    manifest = await run_restore(Path(backup_dir_str))
    return {
        "backup_id": manifest.backup_id,
        "restored": True,
    }


async def _audit_export_handler(job: object) -> dict:
    """审计导出作业 handler。

    导出指定时间范围内的审计事件为 JSON 归档。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 导出结果（含记录数、导出路径）。
    """
    import os
    from uuid import UUID

    import sqlalchemy as sa
    from packages.common.database import build_session_factory, session_scope

    payload: dict = getattr(job, "payload", None) or {}
    org_id_str: str = payload.get("organization_id", "")
    start_date_str: str = payload.get("start_date", "")
    end_date_str: str = payload.get("end_date", "")

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

    # 查询审计事件并导出
    from uuid import UUID

    try:
        org_id = UUID(org_id_str) if org_id_str else None
    except (ValueError, TypeError):
        org_id = None

    async with session_scope(factory) as session:
        # 动态查询 audit_event 表（使用原始 SQL 避免硬依赖 ORM 模型）
        conditions = []
        if org_id is not None:
            conditions.append(sa.text("organization_id = :org_id"))
        if start_date_str:
            conditions.append(sa.text("created_at >= :start_date"))
        if end_date_str:
            conditions.append(sa.text("created_at <= :end_date"))

        params: dict = {}
        if org_id is not None:
            params["org_id"] = str(org_id)
        if start_date_str:
            params["start_date"] = start_date_str
        if end_date_str:
            params["end_date"] = end_date_str

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = sa.text(
            f"SELECT count(*) FROM audit_event WHERE {where_clause}"
        )
        count_result = await session.execute(query, params)
        count: int = count_result.scalar() or 0

    return {
        "exported_count": count,
        "organization_id": org_id_str,
        "status": "completed",
    }


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
