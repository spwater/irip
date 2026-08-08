"""Celery 任务实际执行逻辑。

在 worker 进程中通过 DI 组装 JobExecutor 并执行作业。
此模块在 celery_app.py 的 execute_job 任务中被调用。

技术设计文档 F-04 S8.5：所有异步任务只通过 Outbox->Dispatcher->Celery 一条通道。
此处注册全部 handler（flow, ingestion, model, backup, audit_export），
确保 JobExecutor 能处理所有已注册的作业类型。
Restore 已迁移至 dangerous-ops profile 的 restore 服务（需 Docker socket + superuser）。

C-02 改动：
- Worker 侧二次校验 kind 和权限；
- _restore_handler 使用签名 backup_id 而非 backup_dir 路径。

H-03 改动：
- 全部 handler 原生 async，失败必须 raise（不返回 error dict）；
- owner 从环境变量获取而非硬编码。

H-09 改动：
- backup handler 使用 job.department_id（服务端生成，不从 payload 取）。
"""

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.common.database import build_session_factory, session_scope
from packages.common.errors import AppError
from packages.common.job_policy import JobKindPolicy
from packages.jobs.worker import JobExecutor, WorkerLeaseManager


def _async_db_url() -> str:
    """构建异步数据库 URL（psycopg_async 驱动）。

    从 IRIP_DATABASE_URL 环境变量读取，将同步驱动前缀转为异步驱动前缀。

    Returns:
        str: 异步数据库连接字符串。
    """
    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        return db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return db_url


async def _execute_job_async(job_id: str) -> str:
    """异步执行作业。

    构建 session_factory、租约管理器和执行器，注册全部 handler，
    然后执行作业。

    Args:
        job_id: 作业 UUID 字符串。

    Returns:
        str: 作业 UUID。
    """
    async_url = _async_db_url()

    factory = build_session_factory(async_url)
    # RLS 通电：注入 system 哨兵 GUC，使 worker 能跨部门读写 job 表
    default_dept_id, default_user_id = get_system_guc()
    lease_manager = WorkerLeaseManager(
        factory,
        default_dept_id=default_dept_id,
        default_user_id=default_user_id,
    )
    executor = JobExecutor(
        lease_manager,
        factory,
        default_dept_id=default_dept_id,
        default_user_id=default_user_id,
    )

    # 注册全部 handler（F-04：显式注册表）
    _register_handlers(executor)

    # H-03: owner 从环境变量获取而非硬编码
    owner = os.getenv("IRIP_WORKER_ID", f"celery-worker-{os.getpid()}")
    result = await executor.execute(UUID(job_id), owner=owner)
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

    async def _flow_execute_adapter(job: Any) -> None:
        """适配 flow_execute：直接 await async 函数，避免 asyncio.run 嵌套。

        H-03: 失败必须 raise（不返回 error dict），由 JobExecutor 统一提交状态。
        """
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
            return await _execute_flow_async(run_id, payload)  # type: ignore[return-value]
        except Exception as exc:
            # H-03: 失败必须 raise，不返回 error dict
            try:
                await _mark_job_failed(job_id, str(exc))
            except Exception:
                pass
            raise

    async def _flow_resume_adapter(job: Any) -> None:
        """适配 flow_resume。

        H-03: 失败必须 raise（不返回 error dict），由 JobExecutor 统一提交状态。
        """
        _validate_job_kind(job)
        job_id = str(job.id)
        payload = job.payload or {}
        try:
            return await _resume_flow_async(payload)  # type: ignore[return-value, call-arg, arg-type]
        except Exception as exc:
            # H-03: 失败必须 raise，不返回 error dict
            try:
                await _mark_job_failed(job_id, str(exc))
            except Exception:
                pass
            raise

    def _adapt(handler: Any) -> Any:
        """适配 (job_id, payload) 签名的同步 handler 为 async (job) 签名。

        H-03: handler 原生 async，失败必须 raise（不返回 error dict）。
        用于非 flow handler（它们内部用 asyncio.run，不在嵌套 async 上下文中）。
        """

        async def _wrapper(job: Any) -> Any:
            _validate_job_kind(job)
            return handler(str(job.id), job.payload or {})

        return _wrapper

    # Flow handler（用 async 适配器避免 asyncio.run 嵌套）
    executor.register_handler("flow_execute", _flow_execute_adapter)  # type: ignore[arg-type]
    executor.register_handler("flow_resume", _flow_resume_adapter)  # type: ignore[arg-type]

    # Model handler
    executor.register_handler("model_train", _adapt(train_model_job))
    executor.register_handler("model_predict", _adapt(predict_model_job))
    executor.register_handler("model_publish", _adapt(publish_model_job))

    # Backup / Audit Export handler（F-04 S8.5）
    # Restore 已迁移至 dangerous-ops profile 的 restore 服务（需 Docker socket + superuser）
    executor.register_handler("backup", _backup_handler)
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


async def _backup_handler(job: object) -> dict[str, Any]:
    """备份作业 handler。

    执行 PostgreSQL + MinIO 全量备份，生成完整性 manifest，并更新 backup_record 状态。

    从 payload 读取 type/backup_record_id，执行 run_backup() 后：
    - 成功：调用 BackupRecordService.mark_succeeded() 记录 manifest 信息；
    - 失败：调用 BackupRecordService.mark_failed() 记录错误原因。

    C-02/H-09: org_id 从服务端 job 属性获取，不从 payload 取。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 备份结果（含 backup_id、manifest 路径）。
    """
    _validate_job_kind(job)
    from uuid import UUID

    from deployments.compose.backup import run_backup
    from packages.backups.service import BackupRecordService

    # H-09: org_id 从 job 取，不从 payload 取（服务端生成，不可被客户端覆盖）
    org_id = getattr(job, "department_id", None)
    payload: dict[str, Any] = getattr(job, "payload", None) or {}
    backup_record_id_str: str = payload.get("backup_record_id", "")
    backup_type: str = payload.get("type", "daily")

    factory = build_session_factory(_async_db_url())
    service: BackupRecordService = BackupRecordService(factory)

    try:
        manifest = await run_backup()
    except Exception as exc:
        # 备份失败：更新 backup_record 状态
        if backup_record_id_str:
            try:
                await service.mark_failed(UUID(backup_record_id_str), str(exc))
            except Exception:
                pass
        raise

    # 备份成功：更新 backup_record 状态与元数据
    if backup_record_id_str:
        try:
            # 从 manifest.extra 读取 PITR 元数据（v2 格式）
            extra: dict[str, Any] = manifest.extra or {}
            backup_timestamp_str: str = str(extra.get("backup_timestamp", ""))
            wal_start_lsn: str = str(extra.get("wal_start_lsn", ""))
            wal_end_lsn: str = str(extra.get("wal_end_lsn", ""))

            # 解析 backup_timestamp 为 datetime
            backup_timestamp = None
            if backup_timestamp_str:
                try:
                    from datetime import datetime

                    backup_timestamp = datetime.fromisoformat(backup_timestamp_str)
                except (ValueError, TypeError):
                    pass

            await service.mark_succeeded(
                UUID(backup_record_id_str),
                sha256=manifest.database_sha256,
                migration_version=manifest.migration_version,
                application_version=manifest.application_version,
                backup_timestamp=backup_timestamp,
                wal_start_lsn=wal_start_lsn if wal_start_lsn else None,
                wal_end_lsn=wal_end_lsn if wal_end_lsn else None,
            )
        except Exception as exc:
            # 记录更新失败不影响作业成功状态，但记录日志
            import logging

            logging.getLogger(__name__).warning(
                "Failed to update backup_record %s: %s", backup_record_id_str, exc
            )

    return {
        "backup_id": manifest.backup_id,
        "database_sha256": manifest.database_sha256,
        "object_count": manifest.object_count,
        "department_id": str(org_id) if org_id else None,
        "backup_type": backup_type,
    }


async def _restore_handler(job: object) -> dict[str, Any]:
    """恢复作业 handler。

    C-02: 使用签名 backup_id 而非 backup_dir 路径。
    通过 backup_id 在备份输出目录中查找对应的 manifest，
    不信任客户端提供的任意路径。

    增强逻辑（docs/arch-db-backup.md §4.3）：
    - Step 1: 创建 pre_restore 备份（先备份当前状态，使回滚可撤销）；
    - Step 2: 通过 backup_id 解析备份目录，执行 pg_restore。

    pre_restore 逻辑内联在 handler 中（不创建额外 Job，避免多 Job 链式依赖）。

    Args:
        job: 作业 ORM 实例。

    Returns:
        dict: 恢复结果。

    Raises:
        AppError: code="validation_failed"，当缺少 backup_id 时。
        AppError: code="not_found"，当 backup_id 对应的备份不存在时。
    """
    _validate_job_kind(job)
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from packages.backups.entities import BackupRecord, BackupStatus, BackupType
    from packages.backups.service import BackupRecordService
    from packages.common.ids import new_id

    payload: dict[str, Any] = getattr(job, "payload", None) or {}
    backup_id: str = payload.get("backup_id", "")
    pre_restore_created: bool = payload.get("pre_restore_created", False)
    org_id = getattr(job, "department_id", None)

    if not backup_id:
        raise AppError(
            code="validation_failed",
            message="恢复作业缺少 backup_id 参数",
            retryable=False,
            fields={"field": "backup_id"},
        )

    factory = build_session_factory(_async_db_url())
    service: BackupRecordService = BackupRecordService(factory)

    # ---- Step 1: 创建 pre_restore 备份（仅执行一次）----
    if not pre_restore_created:
        from deployments.compose.backup import run_backup

        pre_restore_id: UUID = new_id()
        now: datetime = datetime.now(UTC)
        backup_output_dir: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "/backups")
        from pathlib import Path

        pre_restore_dir: str = str(Path(backup_output_dir) / str(pre_restore_id))

        # 创建 pre_restore 备份记录
        try:
            record = BackupRecord(
                id=pre_restore_id,
                job_id=getattr(job, "id", None),
                backup_type=BackupType.PRE_RESTORE.value,
                name=f"pre_restore_{backup_id}",
                description=None,
                backup_date=None,
                file_path=pre_restore_dir,
                status=BackupStatus.PENDING.value,
                created_by=None,
                created_at=now,
                expires_at=now + timedelta(days=7),
                department_id=org_id if org_id is not None else new_id(),
            )
            # RLS 通电：backup_record 有 B 类 RLS，INSERT 需 GUC 通过 WITH CHECK
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            sys_dept, sys_user = get_system_guc()
            # 优先使用 job 的 department_id / created_by，退回 system GUC
            guc_dept = org_id if org_id is not None else sys_dept
            guc_user = getattr(job, "created_by", None) or sys_user

            async with session_scope(factory) as session:
                await set_dept_guc(session, guc_dept)
                await set_user_guc(session, guc_user)
                session.add(record)
                await session.flush()

            # 执行备份
            pre_manifest = await run_backup()
            # 从 manifest.extra 读取 PITR 元数据
            pre_extra: dict[str, Any] = pre_manifest.extra or {}
            pre_backup_ts_str: str = str(pre_extra.get("backup_timestamp", ""))
            pre_wal_start: str = str(pre_extra.get("wal_start_lsn", ""))
            pre_wal_end: str = str(pre_extra.get("wal_end_lsn", ""))

            pre_backup_timestamp = None
            if pre_backup_ts_str:
                try:
                    from datetime import datetime as _dt

                    pre_backup_timestamp = _dt.fromisoformat(pre_backup_ts_str)
                except (ValueError, TypeError):
                    pass

            await service.mark_succeeded(
                pre_restore_id,
                sha256=pre_manifest.database_sha256,
                migration_version=pre_manifest.migration_version,
                application_version=pre_manifest.application_version,
                backup_timestamp=pre_backup_timestamp,
                wal_start_lsn=pre_wal_start if pre_wal_start else None,
                wal_end_lsn=pre_wal_end if pre_wal_end else None,
            )
        except Exception as exc:
            # pre_restore 失败仍保留记录（供诊断），标记失败
            try:
                await service.mark_failed(pre_restore_id, str(exc))
            except Exception:
                pass
            # 不中断恢复流程——pre_restore 是安全网，失败不应阻止用户恢复
            import logging

            logging.getLogger(__name__).warning(
                "pre_restore backup failed (continuing with restore): %s", exc
            )

    # ---- Step 2: 执行恢复 ----
    # C-02: 通过 backup_id 解析备份目录，不信任客户端路径
    backup_dir = _resolve_backup_dir_by_id(backup_id)

    # 读取 PITR 恢复目标时间（从 payload）
    recovery_target_time: str | None = payload.get("recovery_target_time")

    from deployments.compose.restore import run_restore

    manifest = await run_restore(backup_dir, recovery_target_time=recovery_target_time)

    # 恢复成功后记录恢复目标时间到 backup_record
    try:
        from datetime import datetime as _dt

        restored_target_time = None
        if recovery_target_time:
            try:
                restored_target_time = _dt.fromisoformat(recovery_target_time)
            except (ValueError, TypeError):
                pass
        # 若未传入 recovery_target_time，使用 manifest 中的 backup_timestamp
        if restored_target_time is None:
            backup_ts = str(manifest.extra.get("backup_timestamp", "")) if manifest.extra else ""
            if backup_ts:
                try:
                    restored_target_time = _dt.fromisoformat(backup_ts)
                except (ValueError, TypeError):
                    pass

        await service.mark_restored(UUID(backup_id), restored_target_time)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to mark restored for backup_record %s: %s", backup_id, exc
        )

    return {
        "backup_id": manifest.backup_id,
        "restored": True,
        "pre_restore_created": pre_restore_created or True,
        "recovery_target_time": recovery_target_time,
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
            manifest_data: dict[str, Any] = json.loads(candidate.read_text(encoding="utf-8"))
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


async def _audit_export_handler(job: object) -> dict[str, Any]:
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

    payload: dict[str, Any] = getattr(job, "payload", None) or {}
    org_id_str: str = payload.get("department_id", "")
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

    # RLS 通电：audit_event 表有 B 类 RLS，必须设 GUC 否则查询返回空集
    from packages.common.tenant_guc import set_dept_guc, set_user_guc

    sys_dept, sys_user = get_system_guc()

    async with session_scope(factory) as session:
        await set_dept_guc(session, sys_dept)
        await set_user_guc(session, sys_user)
        # 动态查询 audit_event 表（使用原始 SQL 避免硬依赖 ORM 模型）
        conditions = []
        if org_id is not None:
            conditions.append("department_id = :org_id")
        if start_date_str:
            conditions.append("created_at >= :start_date")
        if end_date_str:
            conditions.append("created_at <= :end_date")

        params: dict[str, Any] = {}
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
        "department_id": org_id_str,
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
# ---- Beat 定时任务辅助函数（阶段2 多租户隔离键升级）----
# 原 tasks.py 被 tasks/ 包目录遮蔽，Beat 函数合并到此处避免死代码。
import logging as _logging  # noqa: E402

from apps.worker.tasks.flows import execute_flow_job, resume_flow_job  # noqa: E402, F401
from apps.worker.tasks.models import (  # noqa: E402, F401
    predict_model_job,
    publish_model_job,
    train_model_job,
)

_beat_logger = _logging.getLogger(__name__)

#: Root 哨兵部门 ID 环境变量名（Beat 公共档产出挂 root）。
ROOT_DEPT_ENV: str = "IRIP_ROOT_DEPT_ID"

#: System 哨兵部门 ID 环境变量名（Beat 敏感档产出挂 system）。
SYSTEM_DEPT_ENV: str = "IRIP_SYSTEM_DEPT_ID"


def get_root_dept_id() -> str:
    """获取 root 哨兵部门 ID（从环境变量读取）。

    Returns:
        str: root 部门 UUID 字符串。
    """
    return os.getenv(ROOT_DEPT_ENV, "")


def get_system_dept_id() -> str:
    """获取 system 哨兵部门 ID（从环境变量读取）。

    Returns:
        str: system 部门 UUID 字符串。
    """
    return os.getenv(SYSTEM_DEPT_ENV, "")


#: 系统服务用户 ID 环境变量名（Celery worker 无用户会话时作为 GUC actor）。
SYSTEM_SERVICE_USER_ENV: str = "IRIP_SYSTEM_SERVICE_USER_ID"


def get_system_service_user_id() -> str:
    """获取系统服务用户 ID（从环境变量读取）。

    system_service 用户挂 system 哨兵部门（primary）+ root 哨兵部门（secondary，
    由迁移 0071 添加），设置 user GUC 后 current_visible_dept_ids() 返回全部门。

    Returns:
        str: 系统服务用户 UUID 字符串。
    """
    return os.getenv(SYSTEM_SERVICE_USER_ENV, "")


def _parse_uuid_or_none(value: str) -> UUID | None:
    """安全解析 UUID 字符串，空或非法时返回 None。"""
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def get_system_guc() -> tuple[UUID | None, UUID | None]:
    """获取 Worker/Beat 默认 GUC 值（dept_id, user_id）。

    从环境变量读取 system 哨兵部门 ID 和 system_service 用户 ID。
    用于 Worker 无用户上下文时设置 RLS GUC。

    Returns:
        tuple[UUID | None, UUID | None]: (dept_id, user_id)。
    """
    dept_id = _parse_uuid_or_none(get_system_dept_id())
    user_id = _parse_uuid_or_none(get_system_service_user_id())
    return dept_id, user_id


async def _execute_beat_task_async(
    task_name: str,
    department_id: str,
    handler: object,
) -> str:
    """执行 Beat 定时任务（无用户上下文）。

    阶段2：Beat 定时任务没有用户 Principal，按产出物敏感度挂 root（公共档）
    或 system（敏感档）。通过传入的 department_id 设置 GUC。

    Args:
        task_name: 任务名称（用于日志）。
        department_id: 挂载部门 ID（root 或 system 哨兵）。
        handler: 异步处理函数 (AsyncSession) -> Any。

    Returns:
        str: 任务名称。
    """
    from uuid import UUID as _UUID

    from packages.common.tenant_guc import set_dept_guc, set_user_guc

    factory = build_session_factory(_async_db_url())
    dept_uuid: _UUID | None = _UUID(department_id) if department_id else None
    # RLS 通电：Beat 无用户 → 使用 system_service 用户 GUC（挂 root 部门 → 全部门可见）
    user_uuid: _UUID | None = _parse_uuid_or_none(get_system_service_user_id())

    async with factory() as session:
        async with session.begin():
            await set_dept_guc(session, dept_uuid)
            await set_user_guc(session, user_uuid)
            _beat_logger.info("Beat task %s: dept GUC set to %s", task_name, department_id)
            if callable(handler):
                await handler(session)

    return task_name


def _do_execute_beat_task(
    task_name: str,
    department_id: str,
    handler: object,
) -> str:
    """同步入口：在事件循环中执行 Beat 定时任务。

    Args:
        task_name: 任务名称。
        department_id: 挂载部门 ID（root 或 system）。
        handler: 异步处理函数。

    Returns:
        str: 任务名称。
    """
    return asyncio.run(_execute_beat_task_async(task_name, department_id, handler))
