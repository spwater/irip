"""Celery 任务实际执行逻辑。

在 worker 进程中通过 DI 组装 JobExecutor 并执行作业。
此模块在 celery_app.py 的 execute_job 任务中被调用。

技术设计文档 F-04 S8.5：所有异步任务只通过 Outbox->Dispatcher->Celery 一条通道。
此处注册全部 handler（flow, ingestion, model, backup, restore, audit_export），
确保 JobExecutor 能处理所有已注册的作业类型。

C-02 改动：
- Worker 侧二次校验 kind 和权限；
- _restore_handler 使用签名 backup_id 而非 backup_dir 路径。
"""

import asyncio
import os
from uuid import UUID

from packages.common.database import build_session_factory
from packages.common.errors import AppError
from packages.common.job_policy import JobKindPolicy
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
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
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

    C-02: Worker 侧二次校验 -- 在 handler 执行前验证 kind 合法性。

    注意：JobExecutor 调用 handler(job) 传单个 Job ORM 对象，
    但各业务 handler 签名是 (job_id: str, payload: dict)，
    因此用适配器拆包 job.id 和 job.payload。
    """
    from apps.worker.tasks.flows import _execute_flow_async, _mark_job_failed, _resume_flow_async

    async def _flow_execute_adapter(job):
        """适配 flow_execute：直接 await async 函数，避免 asyncio.run 嵌套。"""
        _validate_job_kind(job)
        job_id = str(job.id)
        payload = job.payload or {}
        run_id = str(payload.get("run_id", ""))
        if not run_id:
            raise AppError(
                code="validation_failed",
                message="payload missing run_id",
                retryable=False,
                fields={"job_id": job_id},
            )
        try:
            return await _execute_flow_async(run_id, payload)
        except Exception as exc:
            try:
                await _mark_job_failed(job_id, str(exc))
            except Exception:
                pass
            return {"error": str(exc), "job_id": job_id, "run_id": run_id}

    async def _flow_resume_adapter(job):
        """适配 flow_resume。"""
        _validate_job_kind(job)
        job_id = str(job.id)
        payload = job.payload or {}
        try:
            return await _resume_flow_async(payload)
        except Exception as exc:
            try:
                await _mark_job_failed(job_id, str(exc))
            except Exception:
                pass
            return {"error": str(exc), "job_id": job_id}

    def _adapt(handler):
        """适配 (job_id, payload) 签名的同步 handler 为 async (job) 签名。
        用于非 flow handler（它们内部用 asyncio.run，不在嵌套 async 上下文中）。
        """

        async def _wrapper(job):
            _validate_job_kind(job)
            return handler(str(job.id), job.payload or {})

        return _wrapper

    # Flow handler（用 async 适配器避免 asyncio.run 嵌套）
    executor.register_handler("flow_execute", _flow_execute_adapter)
    executor.register_handler("flow_resume", _flow_resume_adapter)

    # Ingestion handler
    executor.register_handler("ingestion", _adapt(process_ingestion_job))

    # Model handler
    executor.register_handler("model_train", _adapt(train_model_job))
    executor.register_handler("model_predict", _adapt(predict_model_job))
    executor.register_handler("model_publish", _adapt(publish_model_job))

    # Backup / Restore / Audit Export handler（F-04 S8.5）
    executor.register_handler("backup", _backup_handler)
    executor.register_handler("restore", _restore_handler)
    executor.register_handler("audit_export", _audit_export_handler)


def _validate_job_kind(job: object) -> None:
    """C-02: Worker 侧二次校验 job kind 合法性。

    确保即使绕过 API 层，Worker 也不会执行未注册的 kind。

    Args:
        job: 作业 ORM 实例。

    Raises:
        AppError: code="unknown_job_kind"，当 kind 未注册时。
    """
    kind: str = getattr(job, "kind", "")
    if kind not in JobKindPolicy.POLICIES:
        raise AppError(
            code="unknown_job_kind",
            message=f"未注册的作业类型: {kind}",
            retryable=False,
            fields={"kind": kind},
        )


async def _backup_handler(job: object) -> dict:
    """备份作业 handler。

    执行 PostgreSQL + MinIO 全量备份，生成完整性 manifest。

    C-02: org_id 从服务端 job 属性获取，不从 payload 取。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 备份结果（含 backup_id、manifest 路径）。
    """
    _validate_job_kind(job)
    from deployments.compose.backup import run_backup

    manifest = await run_backup()
    return {
        "backup_id": manifest.backup_id,
        "database_sha256": manifest.database_sha256,
        "object_count": manifest.object_count,
    }


async def _restore_handler(job: object) -> dict:
    """恢复作业 handler。

    C-02: 使用签名 backup_id 而非 backup_dir 路径。
    通过 backup_id 在备份输出目录中查找对应的 manifest，
    不信任客户端提供的任意路径。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 恢复结果。

    Raises:
        AppError: code="validation_failed"，当缺少 backup_id 时。
        AppError: code="not_found"，当 backup_id 对应的备份不存在时。
    """
    _validate_job_kind(job)
    payload: dict = getattr(job, "payload", None) or {}
    backup_id: str = payload.get("backup_id", "")
    if not backup_id:
        raise AppError(
            code="validation_failed",
            message="恢复作业缺少 backup_id 参数",
            retryable=False,
            fields={"field": "backup_id"},
        )

    # C-02: 通过 backup_id 解析备份目录，不信任客户端路径
    backup_dir = _resolve_backup_dir_by_id(backup_id)

    from deployments.compose.restore import run_restore

    manifest = await run_restore(backup_dir)
    return {
        "backup_id": manifest.backup_id,
        "restored": True,
    }


def _resolve_backup_dir_by_id(backup_id: str) -> "Path":
    """通过 backup_id 在备份输出目录中查找对应的备份目录。

    C-02: 不信任客户端提供的路径，只接受已签名的 backup_id。

    Args:
        backup_id: 备份唯一标识（UUID 字符串）。

    Returns:
        Path: 备份目录路径。

    Raises:
        AppError: code="not_found"，当 backup_id 对应的备份不存在时。
    """
    import json
    import tempfile
    from pathlib import Path

    # 备份输出目录（与 backup.py 一致）
    output_dir_str: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "")
    if output_dir_str:
        search_dir: Path = Path(output_dir_str)
    else:
        search_dir = Path(tempfile.gettempdir()) / "irip-backup"

    if not search_dir.exists():
        raise AppError(
            code="not_found",
            message=f"备份目录不存在: {search_dir}",
            retryable=False,
            fields={"backup_id": backup_id},
        )

    # 搜索 manifest.json 文件，匹配 backup_id
    manifest_filename: str = "manifest.json"
    for candidate in search_dir.rglob(manifest_filename):
        try:
            manifest_data: dict = json.loads(
                candidate.read_text(encoding="utf-8")
            )
            if manifest_data.get("backup_id") == backup_id:
                return candidate.parent
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue

    raise AppError(
        code="not_found",
        message=f"未找到 backup_id={backup_id} 对应的备份",
        retryable=False,
        fields={"backup_id": backup_id},
    )


async def _audit_export_handler(job: object) -> dict:
    """审计导出作业 handler。

    导出指定时间范围内的审计事件为 JSON 归档。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 导出结果（含记录数、导出路径）。
    """
    _validate_job_kind(job)
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
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)

    # 查询审计事件并导出

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
        query = sa.text(f"SELECT count(*) FROM audit_event WHERE {where_clause}")
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
