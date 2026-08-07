"""备份/恢复 API PITR 升级集成测试。

验证 apps/api/routers/backups.py 的 PITR 升级变更：
- POST /api/v1/backups 响应含 backup_method；
- BackupRecordResponse 含 backup_method + backup_timestamp 字段；
- CreateRestoreRequest 接受 recovery_target_time；
- create_restore 将 recovery_target_time 写入 Job payload。

使用 FastAPI TestClient + dependency_overrides 覆盖认证与会话工厂。
对应 docs/arch-db-backup-pitr-upgrade.md §3.5 / T03。
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
from apps.api.routers.backups import (
    backups_router,
    get_backups_session_factory,
)
from packages.backups.entities import (
    BackupMethod,
    BackupRecord,
    BackupStatus,
    BackupType,
)
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id

pytestmark = pytest.mark.integration


# ---- 辅助函数 ----


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


def _insert_test_user(sync_engine, org_id: UUID) -> tuple[UUID, str]:
    """向数据库插入测试用户，返回 (user_id, email)。"""
    from packages.auth.passwords import hash_password

    user_id = new_id()
    email = f"pitr-admin-{user_id.hex[:8]}@irip.local"
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
                "name": "PITR Admin",
                "hash": hash_password("Test-Password-2026!"),
            },
        )
        conn.commit()
    return user_id, email


def _cleanup_test_user(sync_engine, user_id: UUID) -> None:
    """清理测试用户。"""
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM outbox_event WHERE aggregate_id IN (SELECT id FROM job WHERE created_by = :uid)"),
            {"uid": str(user_id)},
        )
        conn.execute(
            sa.text("DELETE FROM backup_record WHERE created_by = :uid"),
            {"uid": str(user_id)},
        )
        conn.execute(
            sa.text("DELETE FROM job WHERE created_by = :uid"),
            {"uid": str(user_id)},
        )
        conn.execute(
            sa.text("DELETE FROM app_user WHERE id = :uid"),
            {"uid": str(user_id)},
        )
        conn.commit()


def _make_client(
    user: CurrentUser,
    session_factory: async_sessionmaker[AsyncSession],
) -> TestClient:
    """构建挂载 backups_router 的 TestClient，覆盖认证与会话依赖。"""
    app = FastAPI(title="IRIP PITR Backup API Test")
    app.include_router(backups_router)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_backups_session_factory] = lambda: session_factory

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


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def org_id() -> UUID:
    """测试用组织 ID。"""
    return UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def rls_session_factory(sync_engine, org_id: UUID):
    """RLS 感知异步会话工厂。"""
    url = os.getenv("IRIP_TEST_DATABASE_URL")
    async_url = _to_async_url(url)
    engine = create_async_engine(async_url, poolclass=NullPool)

    org_id_str = str(org_id)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_tenant_guc(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET app.current_dept_id = '{org_id_str}'")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory


@pytest.fixture
def admin_user_id(sync_engine, org_id: UUID):
    """插入真实测试用户，返回 user_id。"""
    user_id, _ = _insert_test_user(sync_engine, org_id)
    yield user_id
    _cleanup_test_user(sync_engine, user_id)


@pytest.fixture
def admin_user(admin_user_id: UUID, org_id: UUID) -> CurrentUser:
    """平台管理员 CurrentUser。"""
    return CurrentUser(
        user_id=admin_user_id,
        email="admin@irip.local",
        roles=["platform_administrator"],
        department_id=org_id,
    )


@pytest.fixture
def client(admin_user: CurrentUser, rls_session_factory) -> TestClient:
    """挂载 backups_router 的 TestClient。"""
    return _make_client(admin_user, rls_session_factory)


@pytest.fixture(autouse=True)
def cleanup_records(sync_engine, org_id: UUID):
    """测试后清理 backup_record。"""
    yield
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM backup_record WHERE department_id = :oid"),
            {"oid": str(org_id)},
        )
        conn.commit()


# ============================================================
# 1. POST /api/v1/backups 响应含 backup_method
# ============================================================


class TestCreateBackupPitrResponse:
    """POST /api/v1/backups 创建备份作业的 PITR 响应测试。"""

    def test_create_backup_payload_contains_backup_method(
        self,
        sync_engine,
        client: TestClient,
    ):
        """创建备份作业时 Job payload 含 backup_method='pitr'。"""
        response = client.post(
            "/api/v1/backups/",
            json={"type": "daily"},
        )

        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "backup_record_id" in data

        # 验证 Job payload 含 backup_method
        with sync_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT payload FROM job WHERE id = :jid"),
                {"jid": str(data["job_id"])},
            )
            row = result.fetchone()
            assert row is not None
            payload = row[0]
            assert payload.get("backup_method") == "pitr"

    def test_create_backup_record_has_pitr_method(
        self,
        sync_engine,
        client: TestClient,
    ):
        """创建备份作业时 BackupRecord 的 backup_method='pitr'。"""
        response = client.post(
            "/api/v1/backups/",
            json={"type": "daily"},
        )

        assert response.status_code == 202
        data = response.json()

        with sync_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT backup_method FROM backup_record WHERE id = :rid"),
                {"rid": str(data["backup_record_id"])},
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "pitr"


# ============================================================
# 2. GET 响应含 backup_method + backup_timestamp
# ============================================================


class TestBackupRecordResponsePitrFields:
    """BackupRecordResponse 含 backup_method + backup_timestamp 字段测试。"""

    def test_list_backups_response_contains_backup_method(
        self,
        sync_engine,
        client: TestClient,
        org_id: UUID,
    ):
        """GET /api/v1/backups 响应含 backup_method 字段。"""
        now = datetime.now(UTC)
        record_id = new_id()
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO backup_record "
                    "(id, backup_type, file_path, status, backup_method, "
                    "department_id, created_at, expires_at, backup_date) "
                    "VALUES (:id, 'daily', '/backups/test', 'succeeded', 'pitr', "
                    ":org, :now, :expires, :date)"
                ),
                {
                    "id": str(record_id),
                    "org": str(org_id),
                    "now": now,
                    "expires": now + timedelta(days=14),
                    "date": now.date(),
                },
            )
            conn.commit()

        response = client.get("/api/v1/backups/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert "backup_method" in item
        assert "backup_timestamp" in item

    def test_get_backup_detail_contains_pitr_fields(
        self,
        sync_engine,
        client: TestClient,
        org_id: UUID,
    ):
        """GET /api/v1/backups/{id} 响应含 backup_method + backup_timestamp。"""
        now = datetime.now(UTC)
        record_id = new_id()
        backup_ts = datetime.now(UTC)
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO backup_record "
                    "(id, backup_type, file_path, status, backup_method, "
                    "backup_timestamp, department_id, created_at, expires_at, backup_date) "
                    "VALUES (:id, 'daily', '/backups/detail-test', 'succeeded', 'pitr', "
                    ":backup_ts, :org, :now, :expires, :date)"
                ),
                {
                    "id": str(record_id),
                    "backup_ts": backup_ts,
                    "org": str(org_id),
                    "now": now,
                    "expires": now + timedelta(days=14),
                    "date": now.date(),
                },
            )
            conn.commit()

        response = client.get(f"/api/v1/backups/{record_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["backup_method"] == "pitr"
        assert data["backup_timestamp"] is not None


# ============================================================
# 3. POST /api/v1/backups/{id}/restore 接受 recovery_target_time
# ============================================================


class TestRestoreEndpointRecoveryTargetTime:
    """POST /api/v1/backups/{id}/restore 接受 recovery_target_time 测试。"""

    def test_restore_with_recovery_target_time(
        self,
        sync_engine,
        client: TestClient,
        org_id: UUID,
    ):
        """恢复请求带 recovery_target_time 时写入 Job payload。"""
        now = datetime.now(UTC)
        record_id = new_id()
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO backup_record "
                    "(id, backup_type, file_path, status, backup_method, "
                    "department_id, created_at, expires_at, backup_date) "
                    "VALUES (:id, 'daily', '/backups/restore-test', 'succeeded', 'pitr', "
                    ":org, :now, :expires, :date)"
                ),
                {
                    "id": str(record_id),
                    "org": str(org_id),
                    "now": now,
                    "expires": now + timedelta(days=14),
                    "date": now.date(),
                },
            )
            conn.commit()

        target_time = "2026-08-16T10:30:00+00:00"
        response = client.post(
            f"/api/v1/backups/{record_id}/restore",
            json={"recovery_target_time": target_time},
        )

        assert response.status_code == 202
        data = response.json()
        job_id = data["job_id"]

        with sync_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT payload FROM job WHERE id = :jid"),
                {"jid": str(job_id)},
            )
            row = result.fetchone()
            assert row is not None
            payload = row[0]
            assert payload.get("recovery_target_time") == target_time

        # 清理 Job
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": str(job_id)},
            )
            conn.execute(
                sa.text("DELETE FROM job WHERE id = :jid"),
                {"jid": str(job_id)},
            )
            conn.commit()

    def test_restore_without_recovery_target_time(
        self,
        sync_engine,
        client: TestClient,
        org_id: UUID,
    ):
        """恢复请求不带 recovery_target_time 时 Job payload 中为 None。"""
        now = datetime.now(UTC)
        record_id = new_id()
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO backup_record "
                    "(id, backup_type, file_path, status, backup_method, "
                    "department_id, created_at, expires_at, backup_date) "
                    "VALUES (:id, 'daily', '/backups/restore-no-rtt', 'succeeded', 'pitr', "
                    ":org, :now, :expires, :date)"
                ),
                {
                    "id": str(record_id),
                    "org": str(org_id),
                    "now": now,
                    "expires": now + timedelta(days=14),
                    "date": now.date(),
                },
            )
            conn.commit()

        response = client.post(
            f"/api/v1/backups/{record_id}/restore",
            json={},
        )

        assert response.status_code == 202
        data = response.json()
        job_id = data["job_id"]

        with sync_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT payload FROM job WHERE id = :jid"),
                {"jid": str(job_id)},
            )
            row = result.fetchone()
            assert row is not None
            payload = row[0]
            assert payload.get("recovery_target_time") is None

        with sync_engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM outbox_event WHERE aggregate_id = :jid"),
                {"jid": str(job_id)},
            )
            conn.execute(
                sa.text("DELETE FROM job WHERE id = :jid"),
                {"jid": str(job_id)},
            )
            conn.commit()
