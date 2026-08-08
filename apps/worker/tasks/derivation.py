"""Celery 推导任务（IRIP Task 17）。

包装 DerivationService 为 Celery 任务，异步处理推导作业。
任务通过 asyncio.run() 在同步 Celery 上下文中执行异步推导。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

from apps.worker.celery_app import celery_app


async def _process_derivation_async(
    job_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """异步处理推导作业。

    从 payload 中提取参数，构建 DerivationService 并执行推导。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 作业载荷，包含：
            - evidence_set_version_id: 证据集版本 ID
            - recipe_version_id: 配方版本 ID
            - department_id: 组织 ID
            - actor_id: 操作人 ID（可选）

    Returns:
        dict: 推导结果摘要。
    """
    import sqlalchemy as sa

    from packages.common.database import build_session_factory, session_scope
    from packages.common.tenant_guc import set_dept_guc, set_user_guc
    from packages.jobs.entities import Job, JobStatus
    from packages.provenance.derivations import DerivationService

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)

    # 从 payload 提取参数
    department_id = UUID(str(payload["department_id"]))
    actor_id_str = payload.get("actor_id")
    actor_id = UUID(str(actor_id_str)) if actor_id_str else None
    evidence_set_version_id = UUID(str(payload["evidence_set_version_id"]))
    recipe_version_id = UUID(str(payload["recipe_version_id"]))

    # 构建服务
    derivation_service = DerivationService(
        session_factory=factory,
        department_id=department_id,
        actor_id=actor_id,
    )

    # 更新作业状态为 RUNNING
    async with session_scope(factory) as session:
        # RLS 通电：job 表有 B 类 RLS，需设 GUC
        await set_dept_guc(session, department_id)
        await set_user_guc(session, actor_id)
        await session.execute(
            sa.update(Job)
            .values(
                status=JobStatus.RUNNING.value,
                updated_at=sa.func.now(),
                lock_version=Job.lock_version + 1,
            )
            .where(Job.id == UUID(job_id))
        )

    # 执行推导
    try:
        ref = await derivation_service.create_run(
            evidence_set_version_id=evidence_set_version_id,
            recipe_version_id=recipe_version_id,
        )

        summary = {
            "run_id": str(ref.id),
            "status": ref.status,
            "output_digest": ref.output_digest,
            "outputs": [
                {
                    "variable_code": o.variable_code,
                    "value": str(o.value),
                    "unit": o.unit,
                    "confidence": o.confidence,
                    "exclusion_reasons": list(o.exclusion_reasons),
                }
                for o in ref.outputs
            ],
        }

        # 更新作业状态为 COMPLETED
        async with session_scope(factory) as session:
            # RLS 通电：job 表有 B 类 RLS，需设 GUC
            await set_dept_guc(session, department_id)
            await set_user_guc(session, actor_id)
            await session.execute(
                sa.update(Job)
                .values(
                    status=JobStatus.COMPLETED.value,  # type: ignore[attr-defined]
                    result=summary,
                    updated_at=sa.func.now(),
                    lock_version=Job.lock_version + 1,
                )
                .where(Job.id == UUID(job_id))
            )

        return summary

    except Exception as exc:
        # 更新作业状态为 FAILED
        from apps.worker.tasks import get_system_guc

        sys_dept, sys_user = get_system_guc()
        async with session_scope(factory) as session:
            # RLS 通电：job 表有 B 类 RLS，需设 GUC
            await set_dept_guc(session, sys_dept)
            await set_user_guc(session, sys_user)
            await session.execute(
                sa.update(Job)
                .values(
                    status=JobStatus.FAILED.value,
                    last_error={"error": str(exc)},
                    updated_at=sa.func.now(),
                    lock_version=Job.lock_version + 1,
                )
                .where(Job.id == UUID(job_id))
            )
        raise


@celery_app.task(name="irip.derivation.process", bind=True, soft_time_limit=3000, time_limit=3600)
def process_derivation_job(self: Any, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Celery 任务：处理推导作业。

    1. 从 payload 提取参数；
    2. 构建 DerivationService；
    3. 执行 create_run；
    4. 更新作业状态（RUNNING → COMPLETED/FAILED）；
    5. 返回结果摘要。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 作业载荷字典。

    Returns:
        dict: 推导结果摘要。
    """
    try:
        return asyncio.run(_process_derivation_async(job_id, payload))
    except Exception as exc:
        # P2-C17: 可重试异常用 self.retry，否则 raise 让 Celery 记录失败
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            raise self.retry(exc=exc) from None
        raise
