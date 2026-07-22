"""Celery 摄入任务（IRIP Task 16）。

包装 IngestionPipeline 为 Celery 任务，支持单文件和批量摄入。
任务通过 asyncio.run() 在同步 Celery 上下文中执行异步管线。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

from apps.worker.celery_app import celery_app


async def _process_ingestion_async(
    job_id: str,
    payload: dict,
) -> dict:
    """异步处理摄入作业。

    从 payload 中提取参数，构建 IngestionPipeline 并执行摄入。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 作业载荷，包含：
            - file_paths: 文件路径列表（批量）或 file_path（单文件）
            - mapping_profile_version_id: 映射配置版本 ID
            - template_version_id: 模板版本 ID
            - object_id: 工业对象 ID
            - organization_id: 组织 ID
            - actor_id: 操作人 ID（可选）
            - method_version_id: 方法版本 ID（可选）

    Returns:
        dict: 摄入结果摘要。
    """
    from packages.common.database import build_session_factory
    from packages.connectors.ingestion_service import IngestionPipeline
    from packages.facts.quality import QualityEngine
    from packages.facts.service import FactService
    from packages.jobs.entities import Job, JobStatus
    from packages.jobs.repository import JobRepository
    from packages.common.database import session_scope

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

    # 从 payload 提取参数
    organization_id = UUID(str(payload["organization_id"]))
    actor_id_str = payload.get("actor_id")
    actor_id = UUID(str(actor_id_str)) if actor_id_str else None
    mapping_profile_version_id = UUID(
        str(payload["mapping_profile_version_id"])
    )
    template_version_id = UUID(str(payload["template_version_id"]))
    object_id = UUID(str(payload["object_id"]))
    method_version_id_str = payload.get("method_version_id")
    method_version_id = (
        UUID(str(method_version_id_str)) if method_version_id_str else None
    )

    # 构建服务
    fact_service = FactService(
        session_factory=factory,
        organization_id=organization_id,
        actor_id=actor_id,
    )
    quality_engine = QualityEngine()
    pipeline = IngestionPipeline(
        session_factory=factory,
        fact_service=fact_service,
        quality_engine=quality_engine,
        organization_id=organization_id,
        actor_id=actor_id,
    )

    # 更新作业状态为 RUNNING
    import sqlalchemy as sa

    async with session_scope(factory) as session:
        await session.execute(
            sa.update(Job)
            .values(
                status=JobStatus.RUNNING.value,
                updated_at=sa.func.now(),
                lock_version=Job.lock_version + 1,
            )
            .where(Job.id == UUID(job_id))
        )

    # 执行摄入
    file_paths_raw = payload.get("file_paths")
    if file_paths_raw is None:
        file_path_str = payload.get("file_path")
        if file_path_str is None:
            raise ValueError("payload 缺少 file_paths 或 file_path")
        file_paths = (Path(file_path_str),)
    else:
        file_paths = tuple(Path(p) for p in file_paths_raw)

    results = await pipeline.ingest_batch(
        file_paths=file_paths,
        mapping_profile_version_id=mapping_profile_version_id,
        template_version_id=template_version_id,
        object_id=object_id,
        method_version_id=method_version_id,
    )

    # 汇总结果
    total = len(results)
    deduplicated_count = sum(1 for r in results if r.deduplicated)
    blocked_count = sum(1 for r in results if r.blocked)
    warning_count = sum(1 for r in results if r.warnings > 0 and not r.blocked)
    success_count = total - blocked_count - sum(
        1 for r in results if r.error is not None
    )

    summary = {
        "total": total,
        "deduplicated": deduplicated_count,
        "blocked": blocked_count,
        "warnings": warning_count,
        "success": success_count,
        "errors": [r.error for r in results if r.error is not None],
    }

    # 更新作业状态为 COMPLETED
    async with session_scope(factory) as session:
        await session.execute(
            sa.update(Job)
            .values(
                status=JobStatus.COMPLETED.value,
                result=summary,
                updated_at=sa.func.now(),
                lock_version=Job.lock_version + 1,
            )
            .where(Job.id == UUID(job_id))
        )

    return summary


@celery_app.task(name="irip.ingestion.process")
def process_ingestion_job(job_id: str, payload: dict) -> dict:
    """Celery 任务：处理摄入作业。

    1. 从 payload 提取参数；
    2. 构建 IngestionPipeline；
    3. 执行 ingest_batch；
    4. 更新作业状态（RUNNING → COMPLETED/FAILED）；
    5. 返回结果摘要。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 作业载荷字典。

    Returns:
        dict: 摄入结果摘要。
    """
    try:
        return asyncio.run(_process_ingestion_async(job_id, payload))
    except Exception as exc:
        # 更新作业状态为 FAILED
        try:
            asyncio.run(_mark_job_failed(job_id, str(exc)))
        except Exception:
            pass
        return {"error": str(exc), "job_id": job_id}


async def _mark_job_failed(job_id: str, error: str) -> None:
    """标记作业为失败状态。

    Args:
        job_id: 作业 UUID 字符串。
        error: 错误消息。
    """
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
    async with session_scope(factory) as session:
        await session.execute(
            sa.update(Job)
            .values(
                status=JobStatus.FAILED.value,
                last_error={"error": error},
                updated_at=sa.func.now(),
                lock_version=Job.lock_version + 1,
            )
            .where(Job.id == UUID(job_id))
        )
