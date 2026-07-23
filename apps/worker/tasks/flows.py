"""Celery 流程执行任务（IRIP V2-T03）。

包装 FlowRuntimeService.execute 为 Celery 任务。
任务通过 asyncio.run() 在同步 Celery 上下文中执行异步流程引擎。

模式与 V1 的 worker/tasks/ingestion.py 一致：
- 从环境变量构建数据库会话工厂；
- 构建 ComponentRegistryService、PythonComponentRunner、JobService；
- 调用 FlowRuntimeService.execute(run_id)；
- 更新作业状态（RUNNING → SUCCEEDED/FAILED）。
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from apps.worker.celery_app import celery_app


async def _execute_flow_async(run_id: str, payload: dict) -> dict:
    """异步执行流程。

    从 payload 中提取组织 ID，构建所需服务，调用 FlowRuntimeService.execute。

    Args:
        run_id: 流程执行记录 UUID 字符串。
        payload: 作业载荷，包含：
            - flow_version_id: 流程版本 ID
            - organization_id: 组织 ID

    Returns:
        dict: 执行结果摘要。
    """
    from packages.common.clock import SystemClock
    from packages.common.database import build_session_factory, session_scope
    from packages.components.builtin import register_builtin_components
    from packages.components.flow_runtime import FlowRuntimeService
    from packages.components.registry import ComponentRegistryService
    from packages.components.runner import PythonComponentRunner
    from packages.jobs.service import JobService
    from packages.jobs.entities import Job, JobStatus
    import sqlalchemy as sa

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

    # 设置 AI 配置的 session factory，使 LLM 组件能查询数据库获取 AI 配置
    from apps.api.routers.ai_config import set_session_factory as set_ai_config_session_factory
    set_ai_config_session_factory(factory)

    organization_id = UUID(str(payload["organization_id"]))

    registry = ComponentRegistryService(
        session_factory=factory,
        organization_id=organization_id,
    )
    runner = PythonComponentRunner()
    # 注册内置组件，使 worker 能找到已发布的组件实现
    register_builtin_components(runner)
    job_service = JobService(
        session_factory=factory,
        organization_id=organization_id,
        created_by=organization_id,
    )

    service = FlowRuntimeService(
        session_factory=factory,
        organization_id=organization_id,
        registry=registry,
        runner=runner,
        job_service=job_service,
        clock=SystemClock(),
    )

    run_uuid = UUID(run_id)

    # 更新作业状态为 RUNNING
    async with session_scope(factory) as session:
        await session.execute(
            sa.update(Job)
            .values(
                status=JobStatus.RUNNING.value,
                updated_at=sa.func.now(),
                lock_version=Job.lock_version + 1,
            )
            .where(
                Job.kind == "flow_execute",
                Job.payload.op("->>")("run_id") == run_id,
            )
        )

    # 执行流程
    await service.execute(run_uuid)

    # 获取最终状态
    from packages.components.flow_runtime import FlowRun

    async with session_scope(factory) as session:
        run = await session.scalar(
            sa.select(FlowRun).where(FlowRun.id == run_uuid)
        )
        if run is None:
            return {"error": "run not found", "run_id": run_id}

        return {
            "run_id": run_id,
            "status": run.status,
            "output_digest": run.output_digest,
        }


@celery_app.task(name="irip.flow.execute")
def execute_flow_job(job_id: str, payload: dict) -> dict:
    """Celery 任务：执行流程。

    1. 从 payload 提取 run_id 和组织 ID；
    2. 构建 FlowRuntimeService；
    3. 调用 execute(run_id)；
    4. 返回结果摘要。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 作业载荷字典，包含 run_id 和 organization_id。

    Returns:
        dict: 执行结果摘要。
    """
    run_id: str = str(payload.get("run_id", ""))
    if not run_id:
        return {"error": "payload missing run_id", "job_id": job_id}

    try:
        return asyncio.run(_execute_flow_async(run_id, payload))
    except Exception as exc:
        # 标记作业为失败
        try:
            asyncio.run(_mark_job_failed(job_id, str(exc)))
        except Exception:
            pass
        return {"error": str(exc), "job_id": job_id, "run_id": run_id}


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


@celery_app.task(name="irip.flow.resume")
def resume_flow_job(job_id: str, payload: dict) -> dict:
    """Celery 任务：恢复流程执行。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 作业载荷字典，包含 run_id 和 organization_id。

    Returns:
        dict: 恢复结果摘要。
    """
    run_id: str = str(payload.get("run_id", ""))
    if not run_id:
        return {"error": "payload missing run_id", "job_id": job_id}

    try:
        return asyncio.run(_resume_flow_async(run_id, payload))
    except Exception as exc:
        try:
            asyncio.run(_mark_job_failed(job_id, str(exc)))
        except Exception:
            pass
        return {"error": str(exc), "job_id": job_id, "run_id": run_id}


async def _resume_flow_async(run_id: str, payload: dict) -> dict:
    """异步恢复流程执行。

    Args:
        run_id: 流程执行记录 UUID 字符串。
        payload: 作业载荷。

    Returns:
        dict: 恢复结果摘要。
    """
    from packages.common.clock import SystemClock
    from packages.common.database import build_session_factory, session_scope
    from packages.components.builtin import register_builtin_components
    from packages.components.flow_runtime import FlowRuntimeService, FlowRun
    from packages.components.registry import ComponentRegistryService
    from packages.components.runner import PythonComponentRunner
    from packages.jobs.service import JobService
    import sqlalchemy as sa

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

    # 设置 AI 配置的 session factory，使 LLM 组件能查询数据库获取 AI 配置
    from apps.api.routers.ai_config import set_session_factory as set_ai_config_session_factory
    set_ai_config_session_factory(factory)

    organization_id = UUID(str(payload["organization_id"]))

    registry = ComponentRegistryService(
        session_factory=factory,
        organization_id=organization_id,
    )
    runner = PythonComponentRunner()
    # 注册内置组件，使 worker 能找到已发布的组件实现
    register_builtin_components(runner)
    job_service = JobService(
        session_factory=factory,
        organization_id=organization_id,
        created_by=organization_id,
    )

    service = FlowRuntimeService(
        session_factory=factory,
        organization_id=organization_id,
        registry=registry,
        runner=runner,
        job_service=job_service,
        clock=SystemClock(),
    )

    run_uuid = UUID(run_id)
    await service.resume(run_uuid)

    async with session_scope(factory) as session:
        run = await session.scalar(
            sa.select(FlowRun).where(FlowRun.id == run_uuid)
        )
        if run is None:
            return {"error": "run not found", "run_id": run_id}

        return {
            "run_id": run_id,
            "status": run.status,
            "output_digest": run.output_digest,
        }
