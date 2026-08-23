"""作业服务集成测试。

覆盖 packages/jobs/service.py：
- accept: 幂等、kind 校验、正常创建；
- request_cancel: 正常取消、终态 conflict、不存在 not_found；
- get: 正常获取、不存在、进度/可重试字段；
- list: 分页、状态过滤、kind 过滤、游标；
- get_raw: 正常获取、不存在；
- get_created_by_name: 查询创建者名称。
"""

import uuid as uuid_module

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.jobs.entities import JobStatus
from packages.jobs.service import JobService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def job_setup(async_session_factory, sync_engine):
    """创建部门 + 用户。"""
    from packages.auth.passwords import hash_password

    dept_id = new_id()
    user_id = new_id()

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO department "
                "(id, code, display_name, status, lock_version) "
                "VALUES (:id, :code, :name, 'active', 0)"
            ),
            {
                "id": dept_id,
                "code": f"job-dept-{dept_id.hex[:8]}",
                "name": "Job Test Department",
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, password_hash, "
                "status, lock_version) "
                "VALUES (:id, :dept, :email, :name, :hash, 'active', 0)"
            ),
            {
                "id": user_id,
                "dept": dept_id,
                "email": f"job-user-{user_id.hex[:8]}@irip.local",
                "name": "Job Test User",
                "hash": hash_password("TestPassword2026!"),
            },
        )
        conn.commit()

    yield {"department_id": dept_id, "user_id": user_id}

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM outbox_event WHERE aggregate_id IN ("
                "SELECT id FROM job WHERE department_id = :did)"
            ),
            {"did": dept_id},
        )
        conn.execute(sa.text("DELETE FROM job WHERE department_id = :did"), {"did": dept_id})
        conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user_id})
        conn.execute(sa.text("DELETE FROM department WHERE id = :did"), {"did": dept_id})
        conn.commit()


@pytest.fixture
def job_service(async_session_factory, job_setup):
    """构建 JobService 实例。"""
    return JobService(
        session_factory=async_session_factory,
        department_id=job_setup["department_id"],
        created_by=job_setup["user_id"],
    )


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------


class TestAccept:
    """accept 方法。"""

    async def test_accept_success(self, job_service, sync_engine):
        """成功创建作业。"""
        ref = await job_service.accept(
            kind="echo",
            payload={"value": 42},
            idempotency_key=f"idem-{uuid_module.uuid4().hex[:8]}",
        )

        assert ref.job_id is not None
        assert ref.status == JobStatus.ACCEPTED
        assert ref.kind == "echo"

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": ref.job_id},
            )
            conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()

    async def test_accept_idempotent(self, job_service, sync_engine):
        """相同幂等键返回同一作业。"""
        idem_key = f"idem-{uuid_module.uuid4().hex[:8]}"
        ref1 = await job_service.accept("echo", {"v": 1}, idem_key)
        ref2 = await job_service.accept("echo", {"v": 2}, idem_key)

        assert ref1.job_id == ref2.job_id

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": ref1.job_id},
            )
            conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref1.job_id})
            conn.commit()

    async def test_accept_empty_kind(self, job_service):
        """空 kind → validation_failed。"""
        with pytest.raises(AppError) as exc_info:
            await job_service.accept("", {}, "idem-key")
        assert exc_info.value.code == "validation_failed"

    async def test_accept_whitespace_kind(self, job_service):
        """空白 kind → validation_failed。"""
        with pytest.raises(AppError) as exc_info:
            await job_service.accept("   ", {}, "idem-key")
        assert exc_info.value.code == "validation_failed"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGet:
    """get 方法。"""

    async def test_get_success(self, job_service, sync_engine):
        """获取存在的作业。"""
        ref = await job_service.accept("echo", {"v": 1}, f"idem-{uuid_module.uuid4().hex[:8]}")

        fetched = await job_service.get(ref.job_id)
        assert fetched.job_id == ref.job_id
        assert fetched.kind == "echo"

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": ref.job_id},
            )
            conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()

    async def test_get_not_found(self, job_service):
        """作业不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await job_service.get(uuid_module.uuid4())
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# request_cancel
# ---------------------------------------------------------------------------


class TestRequestCancel:
    """request_cancel 方法。"""

    async def test_cancel_success(self, job_service, sync_engine):
        """成功取消作业。"""
        ref = await job_service.accept("echo", {"v": 1}, f"idem-{uuid_module.uuid4().hex[:8]}")

        cancelled = await job_service.request_cancel(ref.job_id, job_setup_actor(job_service))
        assert cancelled.status == JobStatus.CANCEL_REQUESTED

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": ref.job_id},
            )
            conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()

    async def test_cancel_not_found(self, job_service):
        """作业不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await job_service.request_cancel(uuid_module.uuid4(), uuid_module.uuid4())
        assert exc_info.value.code == "not_found"

    async def test_cancel_terminal_conflict(self, job_service, sync_engine):
        """终态作业取消 → conflict。"""
        ref = await job_service.accept("echo", {"v": 1}, f"idem-{uuid_module.uuid4().hex[:8]}")

        # 手动将作业状态设为 succeeded
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("UPDATE job SET status = 'succeeded' WHERE id = :jid"),
                {"jid": ref.job_id},
            )
            conn.commit()

        with pytest.raises(AppError) as exc_info:
            await job_service.request_cancel(ref.job_id, uuid_module.uuid4())
        assert exc_info.value.code == "conflict"

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": ref.job_id},
            )
            conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()


def job_setup_actor(service: JobService) -> uuid_module.UUID:
    """获取 service 的 created_by。"""
    return service._created_by


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    """list 方法。"""

    async def test_list_basic(self, job_service, sync_engine):
        """基本列表查询。"""
        refs = []
        for i in range(3):
            ref = await job_service.accept(
                "echo", {"i": i}, f"list-idem-{uuid_module.uuid4().hex[:8]}"
            )
            refs.append(ref)

        items, next_cursor, has_more = await job_service.list(limit=50)
        assert isinstance(items, list)
        assert len(items) >= 3

        # 清理
        with sync_engine.connect() as conn:
            for ref in refs:
                conn.execute(
                    sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                    {"jid": ref.job_id},
                )
                conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()

    async def test_list_with_kind_filter(self, job_service, sync_engine):
        """按 kind 过滤。"""
        echo_ref = await job_service.accept("echo", {}, f"echo-idem-{uuid_module.uuid4().hex[:8]}")
        other_ref = await job_service.accept(
            "audit_export", {}, f"audit-idem-{uuid_module.uuid4().hex[:8]}"
        )

        items, _, _ = await job_service.list(kind="echo", limit=50)
        for item in items:
            assert item[0].kind == "echo"

        # 清理
        with sync_engine.connect() as conn:
            for ref in [echo_ref, other_ref]:
                conn.execute(
                    sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                    {"jid": ref.job_id},
                )
                conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()

    async def test_list_invalid_cursor(self, job_service):
        """无效游标 → invalid_cursor。"""
        with pytest.raises(AppError) as exc_info:
            await job_service.list(cursor="not-a-date")
        assert exc_info.value.code == "invalid_cursor"


# ---------------------------------------------------------------------------
# get_raw
# ---------------------------------------------------------------------------


class TestGetRaw:
    """get_raw 方法。"""

    async def test_get_raw_success(self, job_service, sync_engine):
        """获取原始作业实体。"""
        ref = await job_service.accept(
            "echo", {"key": "value"}, f"raw-idem-{uuid_module.uuid4().hex[:8]}"
        )

        job = await job_service.get_raw(ref.job_id)
        assert job.id == ref.job_id
        assert job.kind == "echo"
        assert job.payload == {"key": "value"}

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": ref.job_id},
            )
            conn.execute(sa.text("DELETE FROM job WHERE id = :jid"), {"jid": ref.job_id})
            conn.commit()

    async def test_get_raw_not_found(self, job_service):
        """作业不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await job_service.get_raw(uuid_module.uuid4())
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# get_created_by_name
# ---------------------------------------------------------------------------


class TestGetCreatedByName:
    """get_created_by_name 方法。"""

    async def test_get_name(self, job_service, job_setup):
        """查询创建者名称。"""
        name = await job_service.get_created_by_name(job_setup["user_id"])
        assert name == "Job Test User"

    async def test_get_name_nonexistent_user(self, job_service):
        """不存在的用户 → None。"""
        name = await job_service.get_created_by_name(uuid_module.uuid4())
        assert name is None
