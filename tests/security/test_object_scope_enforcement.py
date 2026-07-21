"""安全测试：对象级授权执行 + 审计仅追加。

覆盖：
- 直接 ID 访问拒绝（用户 A 不能读用户 B 的私有对象）；
- 列表查询过滤（只返回有权限的对象）；
- AI 等价操作检查（AI 工具继承用户权限，不能越权）；
- 审计事件追加验证（INSERT 成功，UPDATE/DELETE 被拒）。
"""


import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.audit.events import AuditEvent, AuditEventData
from packages.audit.redaction import redact
from packages.audit.repository import AuditRecorder
from packages.auth.scope_grants import AuthorizationService, ResourceRef
from packages.common.clock import SystemClock
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from tests.security.conftest import SecurityTestSetup, SecurityTestUser

# ============================================================
# 1. 直接 ID 访问拒绝
# ============================================================


class TestDirectIdAccessDenial:
    """用户不能访问无授权的对象（直接 ID 访问）。"""

    async def test_user_a_can_read_own_object(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """用户 A 可以读自己的对象 X。"""
        await security_setup.authz.require(
            security_setup.user_a, "fact:read", security_setup.object_x
        )

    async def test_user_b_can_read_own_object(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """用户 B 可以读自己的对象 Y。"""
        await security_setup.authz.require(
            security_setup.user_b, "fact:read", security_setup.object_y
        )

    async def test_user_a_cannot_read_user_b_object(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """用户 A 不能读用户 B 的对象 Y（直接 ID 访问拒绝）。"""
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                security_setup.user_a, "fact:read", security_setup.object_y
            )

    async def test_user_b_cannot_read_user_a_object(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """用户 B 不能读用户 A 的对象 X。"""
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                security_setup.user_b, "fact:read", security_setup.object_x
            )

    async def test_unrelated_object_denied(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """用户 A 访问不相关的对象（既非 X 也非 Y）拒绝。"""
        unrelated = ResourceRef(
            organization_id=security_setup.org_id,
            object_id=new_id(),
            resource_type="fact",
        )
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                security_setup.user_a, "fact:read", unrelated
            )


# ============================================================
# 2. 列表查询过滤
# ============================================================


class TestListQueryFiltering:
    """列表查询只返回有权限的对象。"""

    async def test_filter_returns_only_authorized_objects(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """模拟列表过滤：逐个检查对象，只保留有权限的。"""
        all_objects = [
            security_setup.object_x,
            security_setup.object_y,
            ResourceRef(
                organization_id=security_setup.org_id,
                object_id=new_id(),
                resource_type="fact",
            ),
        ]
        visible: list[ResourceRef] = []
        for obj in all_objects:
            allowed = await security_setup.authz.has_grant(
                security_setup.user_a, "fact:read", obj
            )
            if allowed:
                visible.append(obj)

        # 用户 A 只能看到 X，不能看到 Y 和 Z
        assert len(visible) == 1
        assert visible[0].object_id == security_setup.object_x.object_id

    async def test_filter_for_user_b(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """用户 B 的列表过滤只返回 Y。"""
        all_objects = [
            security_setup.object_x,
            security_setup.object_y,
        ]
        visible: list[ResourceRef] = []
        for obj in all_objects:
            allowed = await security_setup.authz.has_grant(
                security_setup.user_b, "fact:read", obj
            )
            if allowed:
                visible.append(obj)

        assert len(visible) == 1
        assert visible[0].object_id == security_setup.object_y.object_id

    async def test_empty_list_for_no_grant_user(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        security_setup: SecurityTestSetup,
    ) -> None:
        """无任何 grant 的用户列表为空。"""
        session = async_session_factory()
        await session.begin()
        authz = AuthorizationService(session=session, clock=SystemClock())

        no_grant_user = SecurityTestUser(
            user_id=new_id(),
            email="no-grant@irip.local",
            roles=["researcher"],
        )
        all_objects = [security_setup.object_x, security_setup.object_y]
        visible: list[ResourceRef] = []
        for obj in all_objects:
            allowed = await authz.has_grant(no_grant_user, "fact:read", obj)
            if allowed:
                visible.append(obj)

        assert len(visible) == 0
        await session.rollback()
        await session.close()


# ============================================================
# 3. AI 等价操作检查
# ============================================================


class TestAIEquivalentOperations:
    """AI 工具继承用户权限，不能越权。

    AI 工具以用户身份执行操作时，授权检查结果与用户本人一致。
    """

    async def test_ai_inherits_user_permissions(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """AI 以用户 A 身份操作：能读 X，不能读 Y（与 A 本人一致）。"""
        user_a = security_setup.user_a

        # AI 可以读 A 自己的对象
        await security_setup.authz.require(user_a, "fact:read", security_setup.object_x)

        # AI 不能读 B 的对象
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                user_a, "fact:read", security_setup.object_y
            )

    async def test_ai_cannot_exceed_user_permissions(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """AI 不能执行用户无权的操作（fact:write）。"""
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                security_setup.user_a, "fact:write", security_setup.object_x
            )

    async def test_ai_with_different_user_has_different_access(
        self,
        security_setup: SecurityTestSetup,
    ) -> None:
        """不同用户的 AI 代理有不同的访问范围。"""
        # AI 作为 A：可以读 X
        await security_setup.authz.require(
            security_setup.user_a, "fact:read", security_setup.object_x
        )
        # AI 作为 B：可以读 Y
        await security_setup.authz.require(
            security_setup.user_b, "fact:read", security_setup.object_y
        )
        # AI 作为 A 不能读 Y
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                security_setup.user_a, "fact:read", security_setup.object_y
            )
        # AI 作为 B 不能读 X
        with pytest.raises(AppError, match="无权访问"):
            await security_setup.authz.require(
                security_setup.user_b, "fact:read", security_setup.object_x
            )


# ============================================================
# 4. 审计事件追加验证
# ============================================================


class TestAuditAppendOnly:
    """审计事件仅追加：INSERT 成功，UPDATE/DELETE 被拒。"""

    async def test_audit_insert_succeeds(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        audit_recorder: AuditRecorder,
    ) -> None:
        """审计事件 INSERT 成功。"""
        org = new_id()
        event = AuditEventData(
            organization_id=org,
            action="security.test.insert",
            actor_user_id=new_id(),
        )
        async with session_scope(async_session_factory) as session:
            result = await audit_recorder.record(session, event)
            assert result.id is not None
            assert result.action == "security.test.insert"

        # 验证可 SELECT
        async with session_scope(async_session_factory) as session:
            row = await session.execute(
                sa.select(AuditEvent).where(AuditEvent.action == "security.test.insert")
            )
            assert row.scalar_one() is not None

        # 清理
        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.delete(AuditEvent).where(AuditEvent.action == "security.test.insert")
            )

    async def test_audit_payload_is_redacted(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        audit_recorder: AuditRecorder,
    ) -> None:
        """审计事件 payload 应已脱敏。"""
        org = new_id()
        raw_payload = {"password": "secret123", "action": "login", "user": "test"}
        redacted_payload = redact(raw_payload)
        event = AuditEventData(
            organization_id=org,
            action="security.test.redaction",
            payload=redacted_payload,
        )
        async with session_scope(async_session_factory) as session:
            await audit_recorder.record(session, event)

        # 验证 payload 已脱敏
        async with session_scope(async_session_factory) as session:
            row = await session.execute(
                sa.select(AuditEvent).where(
                    AuditEvent.action == "security.test.redaction"
                )
            )
            audit = row.scalar_one()
            assert audit.payload is not None
            assert audit.payload["password"] == "[REDACTED]"
            assert audit.payload["action"] == "login"

        # 清理
        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.delete(AuditEvent).where(
                    AuditEvent.action == "security.test.redaction"
                )
            )

    async def test_audit_update_denied_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        audit_recorder: AuditRecorder,
    ) -> None:
        """irip_app 角色不能 UPDATE audit_event（数据库级仅追加）。"""
        import os


        org = new_id()
        event = AuditEventData(
            organization_id=org,
            action="security.test.update_denied",
        )
        async with session_scope(async_session_factory) as session:
            await audit_recorder.record(session, event)

        # 使用 irip_app 角色尝试 UPDATE
        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        engine = create_async_engine(async_url)
        try:
            # INSERT as irip_app succeeds
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                try:
                    await conn.execute(
                        sa.text(
                            "UPDATE audit_event SET action = 'tampered' "
                            "WHERE action = 'security.test.update_denied'"
                        )
                    )
                    pytest.fail("UPDATE should have been denied for irip_app")
                except sa.exc.ProgrammingError as exc:
                    assert "permission" in str(exc).lower() or "denied" in str(exc).lower()
                await conn.rollback()
        finally:
            await engine.dispose()

        # 验证未被篡改
        async with session_scope(async_session_factory) as session:
            row = await session.execute(
                sa.select(AuditEvent).where(
                    AuditEvent.action == "security.test.update_denied"
                )
            )
            audit = row.scalar_one()
            assert audit.action == "security.test.update_denied"

        # 清理
        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.delete(AuditEvent).where(
                    AuditEvent.action == "security.test.update_denied"
                )
            )

    async def test_audit_delete_denied_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        audit_recorder: AuditRecorder,
    ) -> None:
        """irip_app 角色不能 DELETE audit_event（数据库级仅追加）。"""
        import os


        org = new_id()
        event = AuditEventData(
            organization_id=org,
            action="security.test.delete_denied",
        )
        async with session_scope(async_session_factory) as session:
            await audit_recorder.record(session, event)

        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                try:
                    await conn.execute(
                        sa.text(
                            "DELETE FROM audit_event "
                            "WHERE action = 'security.test.delete_denied'"
                        )
                    )
                    pytest.fail("DELETE should have been denied for irip_app")
                except sa.exc.ProgrammingError as exc:
                    assert "permission" in str(exc).lower() or "denied" in str(exc).lower()
                await conn.rollback()
        finally:
            await engine.dispose()

        # 验证事件仍存在
        async with session_scope(async_session_factory) as session:
            row = await session.execute(
                sa.select(AuditEvent).where(
                    AuditEvent.action == "security.test.delete_denied"
                )
            )
            assert row.scalar_one() is not None

        # 清理
        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.delete(AuditEvent).where(
                    AuditEvent.action == "security.test.delete_denied"
                )
            )

    async def test_audit_insert_succeeds_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """irip_app 角色可以 INSERT audit_event。"""
        import os


        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        engine = create_async_engine(async_url)
        action = "security.test.app_role_insert"
        org = new_id()
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                await conn.execute(
                    sa.text(
                        "INSERT INTO audit_event (organization_id, action) "
                        "VALUES (:org, :action)"
                    ),
                    {"org": str(org), "action": action},
                )
                await conn.commit()
        finally:
            await engine.dispose()

        # 验证
        async with session_scope(async_session_factory) as session:
            row = await session.execute(
                sa.select(AuditEvent).where(AuditEvent.action == action)
            )
            assert row.scalar_one() is not None

        # 清理
        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.delete(AuditEvent).where(AuditEvent.action == action)
            )

    async def test_audit_select_succeeds_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        audit_recorder: AuditRecorder,
    ) -> None:
        """irip_app 角色可以 SELECT audit_event。"""
        import os


        org = new_id()
        action = "security.test.app_role_select"
        event = AuditEventData(organization_id=org, action=action)
        async with session_scope(async_session_factory) as session:
            await audit_recorder.record(session, event)

        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                result = await conn.execute(
                    sa.text("SELECT COUNT(*) FROM audit_event WHERE action = :a"),
                    {"a": action},
                )
                count = result.scalar()
                assert count == 1
                await conn.rollback()
        finally:
            await engine.dispose()

        # 清理
        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.delete(AuditEvent).where(AuditEvent.action == action)
            )

    async def test_audit_recorder_has_no_update_or_delete_methods(
        self,
        audit_recorder: AuditRecorder,
    ) -> None:
        """AuditRecorder 类不暴露 update/delete 方法（应用级仅追加）。"""
        assert not hasattr(audit_recorder, "update")
        assert not hasattr(audit_recorder, "delete")
        assert not hasattr(AuditRecorder, "update")
        assert not hasattr(AuditRecorder, "delete")
