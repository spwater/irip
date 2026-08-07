"""备份/恢复 API 集成测试。

覆盖 apps/api/routers/backups.py 的全部端点：
- POST /api/v1/backups — 创建备份作业（daily / milestone）
- GET /api/v1/backups — 列出备份记录（按 type 过滤）
- GET /api/v1/backups/{id} — 备份记录详情
- DELETE /api/v1/backups/{id} — 删除里程碑备份（daily/pre_restore 不可删 → 422）
- GET /api/v1/backups/stats — 汇总统计

使用 FastAPI TestClient + dependency_overrides 覆盖认证与会话工厂。
RLS: 通过 rls_session_factory 自动设置 app.current_dept_id。

注意：create_backup 端点向 job 表插入记录时设置 created_by=current_user.user_id，
job.created_by 有 FK→app_user.id，因此测试需先插入真实用户。
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.backups import backups_router, get_backups_session_factory
from packages.backups.entities import BackupRecord, BackupStatus, BackupType
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id

pytestmark = pytest.mark.integration


# ============================================================
# 辅助函数
# ============================================================


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


def _insert_test_user(sync_engine, org_id: UUID) -> tuple[UUID, str]:
    """向数据库插入测试用户，返回 (user_id, email)。"""
    from packages.auth.passwords import hash_password

    user_id = new_id()
    email = f"backup-admin-{user_id.hex[:8]}@irip.local"
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, "
                "password_hash, status, lock_version) "
                "VALUES (:id, :org, :email, :name, :hash, 'active', 0)"
            ),
            {
                "id": user_id,
                "org": str(org_id),
                "email": email,
                "name": "Backup Admin",
                "hash": hash_password("Test-Password-2026!"),
            },
        )
        conn.commit()
    return user_id, email


def _cleanup_test_user(sync_engine, user_id: UUID) -> None:
    """清理测试用户。"""
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM app_user WHERE id = :uid"),
            {"uid": user_id},
        )
        conn.commit()


def _make_client(
    user: CurrentUser,
    session_factory: async_sessionmaker[AsyncSession],
) -> TestClient:
    """构建挂载 backups_router 的 TestClient，覆盖认证与会话依赖。"""
    app = FastAPI(title="IRIP Backup API Test")
    app.include_router(backups_router)

    # 覆盖认证依赖
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_backups_session_factory] = lambda: session_factory

    # AppError → HTTP 统一错误响应
    _STATUS_MAP: dict[str, int] = {
        "forbidden": 403,
        "not_found": 404,
        "conflict": 409,
        "validation_failed": 422,
        "invalid_cursor": 422,
        "internal_error": 500,
    }

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    return TestClient(app)


async def _insert_record(
    factory: async_sessionmaker[AsyncSession],
    record: BackupRecord,
) -> BackupRecord:
    """直接插入 BackupRecord。"""
    async with session_scope(factory) as session:
        session.add(record)
        await session.flush()
    return record


def _make_record(
    org_id: UUID,
    backup_type: str = BackupType.DAILY.value,
    **kwargs,
) -> BackupRecord:
    """构造 BackupRecord 实例。"""
    now = kwargs.pop("created_at", None) or datetime.now(UTC)
    expires_at = kwargs.pop("expires_at", None)
    if expires_at is None:
        if backup_type == BackupType.DAILY.value:
            expires_at = now + timedelta(days=14)
        elif backup_type == BackupType.PRE_RESTORE.value:
            expires_at = now + timedelta(days=7)
    return BackupRecord(
        id=kwargs.pop("id", None) or new_id(),
        job_id=kwargs.pop("job_id", None),
        backup_type=backup_type,
        name=kwargs.pop("name", None),
        description=kwargs.pop("description", None),
        backup_date=kwargs.pop("backup_date", None) or (now.date() if backup_type == BackupType.DAILY.value else None),
        file_path=kwargs.pop("file_path", f"/backups/{new_id().hex}"),
        file_size=kwargs.pop("file_size", None),
        sha256=kwargs.pop("sha256", None),
        status=kwargs.pop("status", BackupStatus.PENDING.value),
        created_by=kwargs.pop("created_by", None),
        created_at=now,
        expires_at=expires_at,
        department_id=org_id,
    )


def _run_async(coro):
    """在同步测试方法中运行协程。"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def org_id() -> UUID:
    """测试用组织 ID。"""
    return UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def admin_user_id(sync_engine, org_id: UUID) -> UUID:
    """插入真实测试用户（满足 job.created_by FK 约束），返回 user_id。"""
    user_id, _ = _insert_test_user(sync_engine, org_id)
    yield user_id
    _cleanup_test_user(sync_engine, user_id)


@pytest.fixture
def admin_user(admin_user_id: UUID, org_id: UUID) -> CurrentUser:
    """平台管理员 CurrentUser（使用真实 DB 用户 ID）。"""
    return CurrentUser(
        user_id=admin_user_id,
        email="admin@irip.local",
        roles=["platform_administrator"],
        department_id=org_id,
    )


@pytest.fixture
def rls_session_factory(sync_engine, org_id: UUID):
    """RLS 感知异步会话工厂（每次连接设置 app.current_dept_id）。"""
    url = os.getenv("IRIP_TEST_DATABASE_URL")
    async_url = _to_async_url(url)
    engine = create_async_engine(async_url, poolclass=NullPool)

    org_id_str = str(org_id)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_tenant_guc(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET app.current_dept_id = '{org_id_str}'")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory


@pytest.fixture
def backup_client(
    admin_user: CurrentUser,
    rls_session_factory,
) -> TestClient:
    """挂载 backups_router 的 TestClient。"""
    return _make_client(admin_user, rls_session_factory)


@pytest.fixture
def cleanup_backup_records(sync_engine, org_id: UUID):
    """测试后清理 backup_record + job + outbox 表中指定组织的数据。"""
    yield
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM outbox_event WHERE aggregate_id IN ("
                "SELECT id FROM job WHERE department_id = :oid)"
            ),
            {"oid": str(org_id)},
        )
        conn.execute(
            sa.text("DELETE FROM backup_record WHERE department_id = :oid"),
            {"oid": str(org_id)},
        )
        conn.execute(
            sa.text("DELETE FROM job WHERE department_id = :oid"),
            {"oid": str(org_id)},
        )
        conn.commit()


# ============================================================
# 1. test_create_milestone_backup — POST 创建里程碑
# ============================================================


class TestCreateMilestoneBackup:
    """POST /api/v1/backups 创建里程碑备份。"""

    def test_create_milestone_backup_202(
        self,
        backup_client,
        cleanup_backup_records,
    ):
        """POST /api/v1/backups type=milestone + name → 202 Accepted。"""
        resp = backup_client.post(
            "/api/v1/backups/",
            json={
                "type": "milestone",
                "name": "release-v1.0",
                "description": "First stable release",
            },
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert "job_id" in data
        assert "backup_record_id" in data
        assert data["kind"] == "backup"
        assert data["status"] == "accepted"
        # job_id 与 backup_record_id 一致
        assert data["job_id"] == data["backup_record_id"]

    def test_create_milestone_without_name_422(
        self,
        backup_client,
        cleanup_backup_records,
    ):
        """POST /api/v1/backups type=milestone 无 name → 422 validation_failed。"""
        resp = backup_client.post(
            "/api/v1/backups/",
            json={"type": "milestone"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "validation_failed"
        assert "名称" in body["error"]["message"]

    def test_create_daily_backup_202(
        self,
        backup_client,
        cleanup_backup_records,
    ):
        """POST /api/v1/backups type=daily → 202 Accepted。"""
        resp = backup_client.post(
            "/api/v1/backups/",
            json={"type": "daily"},
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["kind"] == "backup"

    def test_create_invalid_type_422(
        self,
        backup_client,
        cleanup_backup_records,
    ):
        """POST /api/v1/backups type=invalid → 422 validation_failed。"""
        resp = backup_client.post(
            "/api/v1/backups/",
            json={"type": "invalid", "name": "test"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "validation_failed"


# ============================================================
# 2. test_list_backups_by_type — GET 按类型过滤
# ============================================================


class TestListBackupsByType:
    """GET /api/v1/backups 按类型过滤。"""

    def test_list_all_backups(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """GET /api/v1/backups/ 无 type 参数 → 返回全部。"""

        async def _setup():
            await _insert_record(
                rls_session_factory, _make_record(org_id, BackupType.DAILY.value)
            )
            await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.MILESTONE.value, name="m1"),
            )

        _run_async(_setup())

        resp = backup_client.get("/api/v1/backups/")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["has_more"] is False

    def test_list_backups_filtered_by_daily(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """GET /api/v1/backups/?type=daily → 只返回 daily 类型。"""

        async def _setup():
            await _insert_record(
                rls_session_factory, _make_record(org_id, BackupType.DAILY.value)
            )
            await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.MILESTONE.value, name="m1"),
            )
            await _insert_record(
                rls_session_factory, _make_record(org_id, BackupType.PRE_RESTORE.value)
            )

        _run_async(_setup())

        resp = backup_client.get("/api/v1/backups/?type=daily")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["backup_type"] == "daily"

    def test_list_backups_filtered_by_milestone(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """GET /api/v1/backups/?type=milestone → 只返回 milestone 类型。"""

        async def _setup():
            await _insert_record(
                rls_session_factory, _make_record(org_id, BackupType.DAILY.value)
            )
            await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.MILESTONE.value, name="m1"),
            )
            await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.MILESTONE.value, name="m2"),
            )

        _run_async(_setup())

        resp = backup_client.get("/api/v1/backups/?type=milestone")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) == 2
        assert all(item["backup_type"] == "milestone" for item in data["items"])


# ============================================================
# 3. test_get_backup_detail — GET 详情
# ============================================================


class TestGetBackupDetail:
    """GET /api/v1/backups/{id} 详情。"""

    def test_get_existing_record_200(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """GET /api/v1/backups/{id} 存在的记录 → 200。"""
        record_id_holder = []

        async def _setup():
            record = await _insert_record(
                rls_session_factory,
                _make_record(
                    org_id,
                    BackupType.MILESTONE.value,
                    name="detail-test",
                    description="test description",
                    status=BackupStatus.SUCCEEDED.value,
                    file_size=2048,
                    sha256="abcdef1234567890",
                ),
            )
            record_id_holder.append(record.id)

        _run_async(_setup())

        resp = backup_client.get(f"/api/v1/backups/{record_id_holder[0]}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == str(record_id_holder[0])
        assert data["backup_type"] == "milestone"
        assert data["name"] == "detail-test"
        assert data["status"] == "succeeded"
        assert data["file_size"] == 2048

    def test_get_nonexistent_404(
        self,
        backup_client,
        cleanup_backup_records,
    ):
        """GET /api/v1/backups/{nonexistent_id} → 404 not_found。"""
        resp = backup_client.get(f"/api/v1/backups/{uuid4()}")
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error"]["code"] == "not_found"


# ============================================================
# 4. test_delete_milestone — DELETE 里程碑
# ============================================================


class TestDeleteMilestone:
    """DELETE /api/v1/backups/{id} 删除里程碑备份。"""

    def test_delete_milestone_204(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """DELETE /api/v1/backups/{id} milestone → 204 No Content。"""
        record_id_holder = []

        async def _setup():
            record = await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.MILESTONE.value, name="to-delete"),
            )
            record_id_holder.append(record.id)

        _run_async(_setup())

        resp = backup_client.delete(f"/api/v1/backups/{record_id_holder[0]}")
        assert resp.status_code == 204, resp.text

        # 确认已删除
        resp2 = backup_client.get(f"/api/v1/backups/{record_id_holder[0]}")
        assert resp2.status_code == 404


# ============================================================
# 5. test_delete_daily_forbidden — DELETE daily/pre_restore 不可删
# ============================================================


class TestDeleteDailyForbidden:
    """DELETE daily / pre_restore 备份应返回 422 validation_failed。

    源码使用 AppError(code="validation_failed") 表达"业务规则不允许删除"，
    validation_failed 映射 HTTP 422。这是业务校验而非权限拒绝（用户已通过
    system:manage 权限检查），因此 422 是正确的语义。
    """

    def test_delete_active_daily_rejected(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """DELETE 未过期的 daily 备份 → 422 validation_failed。"""
        record_id_holder = []

        async def _setup():
            record = await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.DAILY.value),
            )
            record_id_holder.append(record.id)

        _run_async(_setup())

        resp = backup_client.delete(f"/api/v1/backups/{record_id_holder[0]}")
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "validation_failed"

    def test_delete_pre_restore_rejected(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """DELETE pre_restore 备份 → 422 validation_failed（回滚安全网）。"""
        record_id_holder = []

        async def _setup():
            record = await _insert_record(
                rls_session_factory,
                _make_record(org_id, BackupType.PRE_RESTORE.value),
            )
            record_id_holder.append(record.id)

        _run_async(_setup())

        resp = backup_client.delete(f"/api/v1/backups/{record_id_holder[0]}")
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "validation_failed"


# ============================================================
# 6. test_backup_stats — GET 统计
# ============================================================


class TestBackupStats:
    """GET /api/v1/backups/stats 汇总统计。"""

    def test_backup_stats_empty(
        self,
        backup_client,
        cleanup_backup_records,
    ):
        """无记录时 stats 全为 0。"""
        resp = backup_client.get("/api/v1/backups/stats")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_count"] == 0
        assert data["total_size_bytes"] == 0
        assert data["daily_count"] == 0
        assert data["milestone_count"] == 0
        assert data["succeeded_count"] == 0
        assert data["failed_count"] == 0

    def test_backup_stats_with_records(
        self,
        backup_client,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """有记录时 stats 正确统计。"""

        async def _setup():
            # 1 daily succeeded + 1 daily failed + 1 milestone pending
            await _insert_record(
                rls_session_factory,
                _make_record(
                    org_id,
                    BackupType.DAILY.value,
                    status=BackupStatus.SUCCEEDED.value,
                    file_size=1024,
                ),
            )
            await _insert_record(
                rls_session_factory,
                _make_record(
                    org_id,
                    BackupType.DAILY.value,
                    status=BackupStatus.FAILED.value,
                    file_size=0,
                ),
            )
            await _insert_record(
                rls_session_factory,
                _make_record(
                    org_id,
                    BackupType.MILESTONE.value,
                    name="stats-test",
                    status=BackupStatus.PENDING.value,
                    file_size=2048,
                ),
            )

        _run_async(_setup())

        resp = backup_client.get("/api/v1/backups/stats")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_count"] == 3
        assert data["total_size_bytes"] == 1024 + 0 + 2048
        assert data["daily_count"] == 2
        assert data["milestone_count"] == 1
        assert data["succeeded_count"] == 1
        assert data["failed_count"] == 1
