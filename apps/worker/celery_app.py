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
from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

#: Redis URL（从环境变量读取，默认本地测试 Redis）。
REDIS_URL: str = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")

#: Worker 健康检查 HTTP 端口（可通过环境变量覆盖）。
WORKER_HEALTHCHECK_PORT: int = int(os.getenv("IRIP_WORKER_HEALTHCHECK_PORT", "9100"))

#: Celery 应用实例。
celery_app: Celery = Celery(
    "irip",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["apps.worker.tasks", "apps.worker.research_tasks"],
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
        # Outbox 事件投递调度：每 1 秒拉取未投递事件发送到 Celery
        "dispatch-outbox": {
            "task": "outbox.dispatch",
            "schedule": 1.0,
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
        # 每日数据库备份：每日 02:00 UTC 触发 PITR 基础备份（pg_basebackup + mc mirror）
        "daily-backup": {
            "task": "backup.daily",
            "schedule": crontab(hour=2, minute=0),
        },
        # 备份保留策略清理：每日 03:00 UTC 清理过期 daily/pre_restore 备份
        "backup-retention-cleanup": {
            "task": "backup.retention_cleanup",
            "schedule": crontab(hour=3, minute=0),
        },
        # ---- 研究域可信执行（阶段 2 新增） ----
        # 研究域心跳检查：每 30 秒扫描活跃 Run 心跳
        "research-heartbeat": {
            "task": "research.heartbeat",
            "schedule": 30.0,
        },
        # 研究域保温容器清理：每 60 秒清理过期保温容器
        "research-cleanup-warm": {
            "task": "research.cleanup_warm",
            "schedule": 60.0,
        },
        # 研究域队列提升：每 5 秒检查队列并提升等待 Run
        "research-promote-queued": {
            "task": "research.promote_queued",
            "schedule": 5.0,
        },
    },
)


@celery_app.task(name="jobs.execute", bind=True)  # type: ignore[untyped-decorator]
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


@celery_app.task(name="outbox.dispatch")  # type: ignore[untyped-decorator]
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


@celery_app.task(name="worker.heartbeat")  # type: ignore[untyped-decorator]
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
        r = redis.from_url(redis_url)  # type: ignore[no-untyped-call]
        r.set("irip:worker:heartbeat", str(time.time()), ex=120)
    except Exception:
        # 心跳记录失败不应影响心跳任务本身
        pass
    return "heartbeat-ok"


@celery_app.task(name="worker.reap_expired_leases")  # type: ignore[untyped-decorator]
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
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)
    # RLS 通电：注入 system 哨兵 GUC，使 reaper 能跨部门回收过期租约
    from apps.worker.tasks import get_system_guc

    default_dept_id, default_user_id = get_system_guc()
    lease_manager = WorkerLeaseManager(
        factory,
        default_dept_id=default_dept_id,
        default_user_id=default_user_id,
    )

    async def _reap() -> list[Any]:
        return await lease_manager.reap_expired()

    result = asyncio.run(_reap())
    return len(result)


@celery_app.task(name="worker.retry_wait_jobs")  # type: ignore[untyped-decorator]
def retry_wait_jobs() -> int:
    """Celery Beat 调度任务：重新入队 retry_wait 状态且已到 run_after 的作业。

    H-03: 重新投递而非只改状态。同事务创建 outbox 事件，确保 Dispatcher 重新投递。

    Returns:
        int: 重新入队的作业数。
    """
    import asyncio
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory, session_scope
    from packages.jobs.entities import Job, JobStatus
    from packages.jobs.outbox import OutboxEvent

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)

    async def _retry() -> int:
        # RLS 通电：job 表有 B 类 RLS，必须设 GUC 否则查询返回空集
        from apps.worker.tasks import get_system_guc
        from packages.common.clock import SystemClock
        from packages.common.tenant_guc import set_dept_guc, set_user_guc

        sys_dept, sys_user = get_system_guc()

        clock = SystemClock()
        count = 0
        async with session_scope(factory) as session:
            await set_dept_guc(session, sys_dept)
            await set_user_guc(session, sys_user)
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
                # H-03: 同事务创建 outbox 事件，确保 Dispatcher 重新投递
                event = OutboxEvent(
                    aggregate_type="job",
                    aggregate_id=job.id,
                    event_type="job.requeued",
                )
                session.add(event)
                count += 1
            await session.flush()
        return count

    return asyncio.run(_retry())


# ---- 数据库备份调度任务 ----


@celery_app.task(name="backup.daily")  # type: ignore[untyped-decorator]
def daily_backup() -> str:
    """Celery Beat 调度任务：每日数据库自动备份。

    每日 02:00 UTC 触发，创建 Job(kind=backup, payload={type:daily, backup_method:pitr}) +
    outbox 事件 + backup_record 记录（expires_at = now + 14 days）。
    Worker 随后执行 PITR 基础备份（pg_basebackup + mc mirror）。

    Returns:
        str: 创建的作业 UUID（失败时返回错误信息）。
    """
    import asyncio
    import os
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from packages.backups.entities import BackupRecord, BackupStatus, BackupType
    from packages.common.database import build_session_factory, session_scope
    from packages.common.ids import new_id
    from packages.jobs.entities import Job, JobStatus
    from packages.jobs.outbox import OutboxDispatcher

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)

    # 阶段2：系统备份挂 system 哨兵部门（敏感档），从环境变量读取
    from apps.worker.tasks import get_system_dept_id

    dept_id_str: str = get_system_dept_id()
    try:
        dept_id: UUID = UUID(dept_id_str) if dept_id_str else new_id()
    except ValueError:
        dept_id = new_id()

    # 过渡期：保留 org_id 供双写（阶段3退役后删除）
    org_id_str: str = os.getenv("IRIP_SYSTEM_ORG_ID", "")
    try:
        UUID(org_id_str) if org_id_str else new_id()
    except ValueError:
        new_id()

    backup_output_dir: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "/backups")
    from pathlib import Path

    async def _create_daily_backup() -> str:
        from packages.common.tenant_guc import set_dept_guc, set_user_guc

        job_id: UUID = new_id()
        now: datetime = datetime.now(UTC)
        backup_dir: str = str(Path(backup_output_dir) / str(job_id))

        # RLS 通电：Beat 无用户 → 使用 system_service 用户 GUC（挂 root → 全部门可见）
        # 使 current_visible_dept_ids() 返回含 system 哨兵的部门集，INSERT 通过 WITH CHECK
        from apps.worker.tasks import get_system_service_user_id

        sys_user_id_str: str = get_system_service_user_id()
        try:
            sys_user_id: UUID | None = UUID(sys_user_id_str) if sys_user_id_str else None
        except (ValueError, TypeError):
            sys_user_id = None

        async with session_scope(factory) as session:
            await set_dept_guc(session, dept_id)
            await set_user_guc(session, sys_user_id)
            job = Job(
                id=job_id,
                department_id=dept_id,  # 阶段2：挂 system 哨兵
                kind="backup",
                status=JobStatus.ACCEPTED.value,
                payload={
                    "type": BackupType.DAILY.value,
                    "backup_record_id": str(job_id),
                    "backup_method": "pitr",
                    "triggered_by": "system",
                },
                idempotency_key=f"backup:{job_id}",
                attempt=0,
                max_attempts=1,
                created_at=now,
                updated_at=now,
            )
            session.add(job)

            record = BackupRecord(
                id=job_id,
                job_id=job_id,
                backup_type=BackupType.DAILY.value,
                name=None,
                description=None,
                backup_date=now.date(),
                file_path=backup_dir,
                status=BackupStatus.PENDING.value,
                created_by=None,
                created_at=now,
                expires_at=now + timedelta(days=14),
                department_id=dept_id,  # 阶段2：挂 system 哨兵
                backup_method="pitr",
            )
            session.add(record)
            await session.flush()

            await OutboxDispatcher.enqueue(
                session,
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.accepted",
                payload={"job_id": str(job_id), "kind": "backup"},
            )
        return str(job_id)

    return asyncio.run(_create_daily_backup())


@celery_app.task(name="backup.retention_cleanup")  # type: ignore[untyped-decorator]
def retention_cleanup() -> int:
    """Celery Beat 调度任务：清理过期备份。

    每日 03:00 UTC 触发，查询 expires_at < now() 的备份记录，
    删除文件系统目录和数据库记录。仅清理 daily 和 pre_restore（milestone 永久保留）。

    Returns:
        int: 实际清理的记录数量。
    """
    import asyncio
    import os

    from packages.backups.service import BackupRecordService
    from packages.common.database import build_session_factory

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)
    service: BackupRecordService = BackupRecordService(factory)

    # 阶段2：Beat 清理任务操作 backup_record（敏感档）→ 挂 system 哨兵
    from apps.worker.tasks import get_system_dept_id, get_system_service_user_id

    dept_id_str: str = get_system_dept_id()
    user_id_str: str = get_system_service_user_id()

    async def _cleanup() -> int:
        from uuid import UUID as _UUID

        dept_uuid: _UUID | None = _UUID(dept_id_str) if dept_id_str else None
        user_uuid: _UUID | None = _UUID(user_id_str) if user_id_str else None
        return await service.delete_expired(dept_id=dept_uuid, user_id=user_uuid)

    return asyncio.run(_cleanup())


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
    server: HTTPServer = HTTPServer(("0.0.0.0", listen_port), _HealthcheckHandler)

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


# ---- Worker 健康检查自动接通 ----


def _assert_not_superuser() -> None:
    """安全断言：运行时连接角色不能是 superuser 或 bypassrls。

    RLS 是唯一隔离层，运行时连接角色不能是 superuser 或 bypassrls，
    否则 RLS 将被绕过，纵深归零。使用同步 SQLAlchemy 引擎执行检查。
    """
    db_url: str = os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        return

    # 确保使用同步驱动（psycopg，非 psycopg_async）
    sync_url: str = db_url
    if sync_url.startswith("postgresql+psycopg_async://"):
        sync_url = sync_url.replace("postgresql+psycopg_async://", "postgresql+psycopg://", 1)

    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, current_user "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
            row = result.fetchone()
            if row and (row[0] or row[1]):
                raise RuntimeError(
                    f"安全断言失败：运行时连接角色 {row[2]} 是 superuser 或 bypassrls，"
                    "RLS 将被绕过。请检查 IRIP_DATABASE_APP_USER 配置。"
                )
    finally:
        engine.dispose()


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _start_healthcheck_on_worker_init(**kwargs: object) -> None:
    """Worker 子进程启动时自动启动健康检查 HTTP 服务器 + RLS 安全断言。

    通过 ``worker_process_init`` signal 接通 ``run_worker_healthcheck_server()``，
    使每个 Worker 进程在 9100 端口提供 ``GET /health`` 端点，
    供 Docker Compose / Kubernetes liveness probe 探测存活状态。

    同时执行 RLS 安全断言：运行时连接角色不能是 superuser 或 bypassrls，
    否则 RLS 将被绕过。断言失败时抛出 RuntimeError 阻止 worker 启动。

    在 prefork 模式下（--concurrency>1）每个子进程都会触发此信号。
    第一个子进程成功绑定端口；后续子进程端口冲突时静默跳过，
    因为只需要一个进程对外提供健康检查端点即可。
    """
    # 安全断言：拒绝 superuser/bypassrls 运行时连接
    _assert_not_superuser()
    try:
        run_worker_healthcheck_server()
    except OSError:
        # 端口已被前一个子进程占用（prefork 多进程），静默跳过
        pass
