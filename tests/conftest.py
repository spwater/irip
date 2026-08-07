"""IRIP 测试根 conftest：共享数据库 fixtures + T06/T07 fixtures。

F-17: 需要数据库的测试自动标记为 @pytest.mark.integration。
提供 session-scoped 数据库引擎与异步会话工厂，供需要数据库的
单元测试和安全测试使用。集成测试目录（tests/integration/）有
自己的 conftest.py 覆盖这些 fixture（更近的 conftest 优先）。

路径：
- 优先使用 ``IRIP_TEST_DATABASE_URL`` 环境变量；
- 未设置时 skip（集成测试有自己的 testcontainers 回退）。

T06/T07 新增 fixtures（在根 conftest 中定义，供 integration 和 recovery 共用）：
- artifact_service: 连接 minio-test 的工件服务；
- job_harness: 连接 redis-test 的作业测试工具。
"""

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from packages.common.artifacts import ArtifactService
    from packages.jobs.entities import Job, JobRef, JobResult
    from packages.jobs.outbox import OutboxDispatcher
    from packages.jobs.service import JobService
    from packages.jobs.worker import JobExecutor, WorkerLeaseManager

# H-02 修复：导入所有 ORM 模型模块，确保 Base.metadata 包含所有表定义，
# 避免 SQLAlchemy 解析外键时找不到引用表（如 fact.flow_run_id -> flow_run.id）。
import packages.ai.service  # noqa: F401, E402
import packages.ai.tool_repository  # noqa: F401, E402
import packages.audit.events  # noqa: F401, E402
import packages.auth.entities  # noqa: F401, E402
import packages.common.artifacts  # noqa: F401, E402
import packages.components.flow_runtime  # noqa: F401, E402
import packages.components.registry  # noqa: F401, E402
import packages.connectors.entities  # noqa: F401, E402
import packages.departments.entities  # noqa: F401, E402
import packages.equipment.entities  # noqa: F401, E402
import packages.facts.entities  # noqa: F401, E402
import packages.jobs.entities  # noqa: F401, E402
import packages.jobs.outbox  # noqa: F401, E402
import packages.models.entities  # noqa: F401, E402
import packages.parameters.entities  # noqa: F401, E402
import packages.provenance.entities  # noqa: F401, E402
import packages.standards.object_type_dict  # noqa: F401, E402
import packages.standards.objects  # noqa: F401, E402


class RecordingTaskSender:
    """测试用 TaskSender 替身：记录投递任务，不连接真实 broker。

    Phase 3 架构收敛（T3-3）：``packages.jobs`` 的 Outbox 调度器改为通过
    ``TaskSender`` 协议依赖注入接收任务投递通道。测试中注入本替身，
    ``send_task`` 不抛异常（使事件被标记为已投递），并记录调用以供断言。
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, list, str]] = []

    def send_task(self, name: str, args: list, queue: str) -> None:
        """记录任务投递（模拟成功，不连接 broker）。"""
        self.sent.append((name, list(args), queue))


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


# F-17: 需要数据库的 fixture 自动标记为 integration
# 这些 fixture 依赖 IRIP_TEST_DATABASE_URL，属于集成测试范畴


@pytest.fixture(scope="session")
def sync_engine() -> Iterator[Engine]:
    """提供同步 SQLAlchemy 引擎连接到测试数据库。

    F-17: 标记为 integration —— 需要真实数据库连接。

    路径 1（主）：``IRIP_TEST_DATABASE_URL`` 已设置。
    路径 2：未设置时 skip（依赖集成测试的 testcontainers 回退）。
    """
    url = os.getenv("IRIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping DB-dependent test")
        return  # 不可达，满足类型检查

    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def async_session_factory(
    sync_engine: Engine,
) -> async_sessionmaker[AsyncSession]:
    """提供异步会话工厂（NullPool，适配 TestClient 跨事件循环场景）。

    F-17: 依赖 sync_engine，自动继承 integration 标记。
    """
    async_url = _to_async_url(sync_engine.url.render_as_string(hide_password=False))
    engine = create_async_engine(async_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---- T06/T07: 测试用户与组织 ----


@dataclass(frozen=True)
class TestUser:
    """测试用户信息。"""

    user_id: UUID
    email: str
    department_id: UUID


def _insert_test_user(engine: Engine, email: str) -> TestUser:
    """插入测试用户，返回用户 ID 和组织 ID。

    自动创建 department 记录以满足 app_user.department_id FK 约束。
    """
    from packages.auth.passwords import hash_password
    from packages.common.ids import new_id

    user_id = new_id()
    org_id = new_id()
    with engine.connect() as conn:
        # 先创建 department（满足 app_user.department_id FK 约束）
        conn.execute(
            sa.text(
                "INSERT INTO department "
                "(id, code, display_name, status, lock_version) "
                "VALUES (:id, :code, :name, 'active', 0)"
            ),
            {
                "id": org_id,
                "code": f"test-dept-{org_id.hex[:8]}",
                "name": "Test Department",
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, "
                "password_hash, status, lock_version) "
                "VALUES (:id, :org, :email, :name, :hash, :status, 0)"
            ),
            {
                "id": user_id,
                "org": org_id,
                "email": email,
                "name": "Test User",
                "hash": hash_password("Test-Password-2026!"),
                "status": "active",
            },
        )
        conn.commit()
    return TestUser(user_id=user_id, email=email, department_id=org_id)


def _cleanup_test_user(engine: Engine, user_id: UUID) -> None:
    """清理测试用户及其关联 department。

    按依赖顺序删除子记录（component/flow/model 等）以避免 FK 约束冲突。
    """
    with engine.connect() as conn:
        # 先查出 department_id
        result = conn.execute(
            sa.text("SELECT department_id FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        row = result.fetchone()
        dept_id = row[0] if row else None

        # ---- 删除引用 app_user 的子记录（避免 FK 约束冲突） ----

        # flow 相关（flow_definition_version 不可变，需禁用 trigger）
        conn.execute(
            sa.text(
                "DELETE FROM flow_node_execution WHERE flow_run_id IN ("
                "SELECT id FROM flow_run WHERE department_id = :did)"
            ),
            {"did": dept_id},
        )
        conn.execute(
            sa.text("DELETE FROM flow_run WHERE department_id = :did"),
            {"did": dept_id},
        )
        conn.execute(sa.text("ALTER TABLE flow_definition_version DISABLE TRIGGER ALL"))
        conn.execute(
            sa.text(
                "DELETE FROM flow_definition_version WHERE flow_definition_id IN ("
                "SELECT id FROM flow_definition WHERE owner_user_id = :uid)"
            ),
            {"uid": user_id},
        )
        conn.execute(sa.text("ALTER TABLE flow_definition_version ENABLE TRIGGER ALL"))
        conn.execute(
            sa.text("DELETE FROM flow_definition WHERE owner_user_id = :uid"),
            {"uid": user_id},
        )

        # component 相关（component_version 是不可变表，需先禁用 trigger）
        conn.execute(sa.text("ALTER TABLE component_version DISABLE TRIGGER ALL"))
        conn.execute(
            sa.text(
                "DELETE FROM component_version WHERE component_id IN ("
                "SELECT id FROM component WHERE owner_user_id = :uid)"
            ),
            {"uid": user_id},
        )
        conn.execute(sa.text("ALTER TABLE component_version ENABLE TRIGGER ALL"))
        conn.execute(
            sa.text("DELETE FROM component WHERE owner_user_id = :uid"),
            {"uid": user_id},
        )

        # model 相关（model_version 是不可变表，需先禁用 trigger）
        conn.execute(sa.text("ALTER TABLE model_version DISABLE TRIGGER ALL"))
        conn.execute(
            sa.text(
                "DELETE FROM model_version WHERE model_id IN ("
                "SELECT id FROM model WHERE owner_user_id = :uid)"
            ),
            {"uid": user_id},
        )
        conn.execute(sa.text("ALTER TABLE model_version ENABLE TRIGGER ALL"))
        conn.execute(
            sa.text("DELETE FROM model WHERE owner_user_id = :uid"),
            {"uid": user_id},
        )

        # ---- 删除其余引用 app_user 的记录 ----
        conn.execute(
            sa.text(
                "DELETE FROM outbox_event "
                "WHERE aggregate_id IN (SELECT id FROM job WHERE created_by = :uid)"
            ),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM artifact WHERE uploaded_by = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM job WHERE created_by = :uid"),
            {"uid": user_id},
        )
        conn.execute(
            sa.text("DELETE FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        if dept_id is not None:
            conn.execute(
                sa.text("DELETE FROM department WHERE id = :did"),
                {"did": dept_id},
            )
        conn.commit()


@pytest.fixture
def test_user(sync_engine: Engine) -> Iterator[TestUser]:
    """测试用户（含 department_id）。"""
    import uuid as uuid_module

    email = f"test-{uuid_module.uuid4().hex[:8]}@irip.local"
    user = _insert_test_user(sync_engine, email)
    yield user
    _cleanup_test_user(sync_engine, user.user_id)


# ---- T06: artifact_service fixture ----


@pytest.fixture
def artifact_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: TestUser,
) -> "ArtifactService":
    """工件服务（连接 minio-test）。"""
    from packages.common.artifacts import ArtifactService
    from packages.common.s3_repository import S3Repository

    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "localhost:59000")
    access_key = os.getenv("IRIP_MINIO_ACCESS_KEY", "irip")
    secret_key = os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password")
    bucket = os.getenv("IRIP_MINIO_BUCKET", "irip-test")

    s3_repo = S3Repository(
        endpoint_url=f"http://{endpoint}",
        access_key=access_key,
        secret_key=secret_key,
        bucket_name=bucket,
    )
    # 确保 bucket 存在
    s3_repo.ensure_bucket()

    return ArtifactService(
        s3_repo=s3_repo,
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        uploaded_by=test_user.user_id,
    )


# ---- T07: job_harness fixture ----


class JobHarness:
    """作业测试工具。

    封装 JobService、JobExecutor、WorkerLeaseManager、OutboxDispatcher，
    提供测试所需的全部操作。
    """

    def __init__(
        self,
        job_service: "JobService",
        executor: "JobExecutor",
        lease_manager: "WorkerLeaseManager",
        dispatcher: "OutboxDispatcher",
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._service = job_service
        self._executor = executor
        self._lease_manager = lease_manager
        self._dispatcher = dispatcher
        self._factory = session_factory

    async def accept(
        self,
        kind: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> "JobRef":
        """提交作业。"""
        return await self._service.accept(kind, payload, idempotency_key)

    async def get(self, job_id: UUID) -> "JobRef":
        """获取作业状态。"""
        return await self._service.get(job_id)

    async def request_cancel(self, job_id: UUID) -> "JobRef":
        """请求取消作业。"""
        from uuid import uuid4

        return await self._service.request_cancel(job_id, uuid4())

    async def deliver(self, job_id: UUID, owner: str | None = None) -> "JobResult | None":
        """投递作业到 worker 执行。"""
        from uuid import uuid4

        worker_id = owner or f"worker-{uuid4().hex[:8]}"
        return await self._executor.execute(job_id, worker_id)

    async def deliver_twice(self, job_id: UUID) -> None:
        """模拟重复投递：同一作业执行两次。"""
        await self._executor.execute(job_id, "worker-dup-1")
        await self._executor.execute(job_id, "worker-dup-2")

    async def deliver_with_retries(self, job_id: UUID, fail_times: int) -> None:
        """模拟带重试的投递。

        前 fail_times 次投递使用会失败的 handler，
        第 fail_times+1 次投递使用正常 handler。
        """
        # 重新注册 handler 模拟重试
        original_handler = self._executor._handlers.get("flaky")
        fail_count = [0]

        async def flaky_handler(job: "Job") -> dict[str, object]:
            if fail_count[0] < fail_times:
                fail_count[0] += 1
                raise RuntimeError(f"Transient failure #{fail_count[0]}")
            return job.payload or {}

        self._executor.register_handler("flaky", flaky_handler)

        # 多次投递直到成功
        from packages.jobs.entities import TERMINAL_STATUSES, JobStatus

        max_rounds = fail_times + 1
        for _ in range(max_rounds):
            ref = await self.get(job_id)
            if ref.status in TERMINAL_STATUSES:
                break
            if ref.status == JobStatus.RETRY_WAIT:
                # 清除 run_after 使其可立即重试
                import sqlalchemy as sa

                from packages.common.database import session_scope
                from packages.jobs.entities import Job

                async with session_scope(self._factory) as session:
                    await session.execute(
                        sa.update(Job)
                        .values(
                            status=JobStatus.QUEUED.value,
                            run_after=None,
                            updated_at=sa.func.now(),
                        )
                        .where(Job.id == job_id)
                    )
            await self.deliver(job_id)

        if original_handler is not None:
            self._executor.register_handler("flaky", original_handler)

    async def start_then_abandon(self, kind: str) -> "JobRef":
        """启动作业后放弃（模拟 worker 崩溃）。"""
        ref = await self.accept(kind, {"value": 1}, f"idem-abandon-{kind}")
        # 获取租约但不执行
        acquired = await self._lease_manager.acquire(ref.job_id, "crashed-worker")
        assert acquired, "Failed to acquire lease for abandon test"
        return ref

    async def simulate_worker_crash(self, job_id: UUID) -> None:
        """模拟 worker 崩溃（获取租约但不释放）。"""
        acquired = await self._lease_manager.acquire(job_id, "crashed-worker")
        assert acquired, "Failed to acquire lease for crash simulation"

    async def reap_expired_leases(self) -> list[UUID]:
        """回收过期租约。

        先将所有 running 作业的 lease_expires_at 设为过去时间（模拟过期），
        然后调用实际的 reap_expired 回收。
        """
        from datetime import UTC, datetime, timedelta

        import sqlalchemy as sa

        from packages.common.database import session_scope
        from packages.jobs.entities import Job, JobStatus

        past_time = datetime.now(UTC) - timedelta(seconds=60)
        async with session_scope(self._factory) as session:
            await session.execute(
                sa.update(Job)
                .values(lease_expires_at=past_time)
                .where(
                    Job.status == JobStatus.RUNNING.value,
                    Job.lease_owner.is_not(None),
                )
            )

        return await self._lease_manager.reap_expired()

    async def authoritative_results(self, job_id: UUID) -> "list[JobResult]":
        """从数据库获取权威结果。"""
        import sqlalchemy as sa

        from packages.common.database import session_scope
        from packages.jobs.entities import Job, JobResult, JobStatus

        async with session_scope(self._factory) as session:
            job = await session.scalar(sa.select(Job).where(Job.id == job_id))
            if job is None or job.result is None:
                return []
            return [
                JobResult(
                    job_id=job.id,
                    status=JobStatus(job.status),
                    payload=job.result,
                    last_error=job.last_error,
                )
            ]

    async def dispatch_outbox(self) -> int:
        """调度 outbox 事件。"""
        return await self._dispatcher.dispatch()

    async def reset_outbox(self) -> int:
        """重置已投递事件为未投递。"""
        return await self._dispatcher.reset_delivered()

    async def undelivered_count(self) -> int:
        """获取未投递事件数。"""
        return await self._dispatcher.get_undelivered_count()


@pytest.fixture
async def job_harness(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: TestUser,
) -> AsyncIterator[JobHarness]:
    """作业测试工具（连接 redis-test）。"""
    from packages.common.errors import AppError
    from packages.jobs.outbox import OutboxDispatcher, OutboxEvent
    from packages.jobs.service import JobService
    from packages.jobs.worker import JobExecutor, WorkerLeaseManager

    redis_url = os.getenv("IRIP_REDIS_URL", "redis://localhost:56379/0")

    # 清理 prior test runs 残留的 outbox 事件（防止跨运行污染）
    from packages.common.database import session_scope

    async with session_scope(async_session_factory) as session:
        await session.execute(sa.delete(OutboxEvent))

    job_service = JobService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
        created_by=test_user.user_id,
    )

    lease_manager = WorkerLeaseManager(async_session_factory)
    executor = JobExecutor(lease_manager, async_session_factory)
    dispatcher = OutboxDispatcher(
        async_session_factory,
        redis_url=redis_url,
        task_sender=RecordingTaskSender(),
    )

    # 注册测试 handlers
    async def echo_handler(job: "Job") -> dict[str, object]:
        return job.payload or {}

    async def validation_fail_handler(job: "Job") -> dict[str, object]:
        raise AppError(
            code="validation_failed",
            message="Invalid input",
            retryable=False,
        )

    executor.register_handler("echo", echo_handler)
    executor.register_handler("validation_fail", validation_fail_handler)

    yield JobHarness(
        job_service=job_service,
        executor=executor,
        lease_manager=lease_manager,
        dispatcher=dispatcher,
        session_factory=async_session_factory,
    )
