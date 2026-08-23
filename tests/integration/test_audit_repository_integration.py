"""Integration tests for packages.audit.repository — AuditRecorder + AuditQueryRepository.

Uses the real test database (PG container at localhost:55432).
Tests insert, query with filters, and cursor-based pagination.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEvent, AuditEventData
from packages.audit.repository import AuditQueryRepository, AuditRecorder
from packages.common.database import session_scope
from packages.common.ids import new_id


@pytest.fixture
def dept_id(sync_engine) -> uuid.UUID:
    """Create a real department in the DB for audit event FK."""
    dept_id = new_id()
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO department "
                "(id, code, display_name, status, lock_version) "
                "VALUES (:id, :code, :name, 'active', 0)"
            ),
            {
                "id": dept_id,
                "code": f"audit-test-{dept_id.hex[:8]}",
                "name": "Audit Test Dept",
            },
        )
        conn.commit()
    yield dept_id
    # Cleanup: audit_event is immutable (F-03), must disable trigger to delete
    with sync_engine.connect() as conn:
        conn.execute(sa.text("ALTER TABLE audit_event DISABLE TRIGGER ALL"))
        conn.execute(
            sa.text("DELETE FROM audit_event WHERE department_id = :did"),
            {"did": dept_id},
        )
        conn.execute(sa.text("ALTER TABLE audit_event ENABLE TRIGGER ALL"))
        conn.execute(
            sa.text("DELETE FROM department WHERE id = :did"),
            {"did": dept_id},
        )
        conn.commit()


@pytest.fixture
def user_id() -> uuid.UUID:
    """A fixed actor user ID (doesn't need to exist in app_user for audit events)."""
    return new_id()


async def _insert_audit_event(
    factory: async_sessionmaker[AsyncSession],
    dept_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    """Insert an audit event via AuditRecorder and return the ORM object."""
    event_data = AuditEventData(
        department_id=dept_id,
        action=action,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload or {},
    )
    async with session_scope(factory) as session:
        from packages.common.tenant_guc import set_dept_guc, set_user_guc

        await set_dept_guc(session, dept_id)
        if actor_user_id is not None:
            await set_user_guc(session, actor_user_id)
        result = await AuditRecorder.record(session, event_data)
    return result


class TestAuditRecorder:
    """Tests for AuditRecorder.record."""

    @pytest.mark.integration
    async def test_record_inserts_event(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        result = await _insert_audit_event(
            async_session_factory,
            dept_id,
            "test.action",
            actor_user_id=user_id,
            resource_type="fact",
            resource_id=new_id(),
            payload={"key": "value"},
        )
        assert result.id is not None
        assert result.action == "test.action"
        assert result.department_id == dept_id
        assert result.actor_user_id == user_id
        assert result.payload == {"key": "value"}

    @pytest.mark.integration
    async def test_record_minimal_event(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
    ) -> None:
        result = await _insert_audit_event(
            async_session_factory,
            dept_id,
            "system.event",
        )
        assert result.action == "system.event"
        assert result.actor_user_id is None
        assert result.payload == {}


class TestAuditQueryRepository:
    """Tests for AuditQueryRepository.list_events."""

    @pytest.mark.integration
    async def test_list_all_events(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        await _insert_audit_event(
            async_session_factory, dept_id, "test.list.all", actor_user_id=user_id
        )

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            events = await AuditQueryRepository.list_events(
                session, action="test.list.all", limit=50
            )
        assert len(events) >= 1
        assert all(e.action == "test.list.all" for e in events)

    @pytest.mark.integration
    async def test_filter_by_action(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        await _insert_audit_event(
            async_session_factory, dept_id, "auth.login", actor_user_id=user_id
        )
        await _insert_audit_event(
            async_session_factory, dept_id, "artifact.upload", actor_user_id=user_id
        )

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            login_events = await AuditQueryRepository.list_events(
                session, action="auth.login", limit=50
            )
            upload_events = await AuditQueryRepository.list_events(
                session, action="artifact.upload", limit=50
            )

        assert all(e.action == "auth.login" for e in login_events)
        assert all(e.action == "artifact.upload" for e in upload_events)

    @pytest.mark.integration
    async def test_filter_by_user_id(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
    ) -> None:
        user_a = new_id()
        user_b = new_id()
        await _insert_audit_event(async_session_factory, dept_id, "test.user", actor_user_id=user_a)
        await _insert_audit_event(async_session_factory, dept_id, "test.user", actor_user_id=user_b)

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_a)
            events = await AuditQueryRepository.list_events(
                session, user_id=user_a, action="test.user", limit=50
            )

        assert all(e.actor_user_id == user_a for e in events)
        assert len(events) >= 1

    @pytest.mark.integration
    async def test_filter_by_resource_type(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        await _insert_audit_event(
            async_session_factory,
            dept_id,
            "test.resource",
            actor_user_id=user_id,
            resource_type="fact",
            resource_id=new_id(),
        )
        await _insert_audit_event(
            async_session_factory,
            dept_id,
            "test.resource",
            actor_user_id=user_id,
            resource_type="artifact",
            resource_id=new_id(),
        )

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            events = await AuditQueryRepository.list_events(
                session, object_type="fact", action="test.resource", limit=50
            )

        assert all(e.resource_type == "fact" for e in events)

    @pytest.mark.integration
    async def test_filter_by_date_range(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        await _insert_audit_event(
            async_session_factory, dept_id, "test.daterange", actor_user_id=user_id
        )

        now = datetime.now(UTC)
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            events = await AuditQueryRepository.list_events(
                session, action="test.daterange", start_date=start, end_date=end, limit=50
            )
        assert len(events) >= 1

    @pytest.mark.integration
    async def test_cursor_pagination(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        for _i in range(5):
            await _insert_audit_event(
                async_session_factory,
                dept_id,
                "test.page",
                actor_user_id=user_id,
            )

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            page1 = await AuditQueryRepository.list_events(session, action="test.page", limit=2)
            assert len(page1) <= 3  # limit+1

            if page1:
                cursor = page1[-1].occurred_at
                page2 = await AuditQueryRepository.list_events(
                    session, action="test.page", cursor_dt=cursor, limit=2
                )
                page1_ids = {e.id for e in page1}
                page2_ids = {e.id for e in page2}
                assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.integration
    async def test_limit_plus_one_for_has_more(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        for _i in range(5):
            await _insert_audit_event(
                async_session_factory,
                dept_id,
                "test.hasmore",
                actor_user_id=user_id,
            )

        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            events = await AuditQueryRepository.list_events(session, action="test.hasmore", limit=3)
        assert len(events) >= 3

    @pytest.mark.integration
    async def test_no_results_returns_empty_list(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        dept_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        async with session_scope(async_session_factory) as session:
            from packages.common.tenant_guc import set_dept_guc, set_user_guc

            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            events = await AuditQueryRepository.list_events(
                session, action="nonexistent.action.12345", limit=50
            )
        assert events == []
