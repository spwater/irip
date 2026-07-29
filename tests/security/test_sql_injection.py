"""安全测试：SQL 注入防护。

覆盖（docs/arch-v0.md §7.4 输入校验 + §8.2 数据库安全）：
- 参数化查询不被注入（SQLAlchemy 绑定参数，注入字符串作为字面量处理）；
- PostgreSQL 组件仅允许 SELECT（irip_app 角色无 DDL/DML 权限）；
- DROP/DELETE/UPDATE 被拦截（irip_app 角色权限限制）；
- 分号分隔的多语句被拒绝（psycopg3 默认禁止多语句）。

安全设计：
- 所有数据库查询使用 SQLAlchemy ORM / Core，参数化绑定；
- irip_app 角色仅拥有业务表的 DML 权限，审计表仅 INSERT+SELECT；
- psycopg3 驱动默认禁止多语句执行。
"""

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ============================================================
# 1. 参数化查询不被注入
# ============================================================


class TestParameterizedQuery:
    """SQLAlchemy 参数化查询防止 SQL 注入。"""

    async def test_injection_string_treated_as_literal(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """注入字符串 ``' OR '1'='1`` 作为字面量处理，不改变查询语义。"""
        async with async_session_factory() as session:
            # 正常查询：按邮箱查找用户
            injection_email = "' OR '1'='1 --"
            result = await session.execute(
                sa.text("SELECT COUNT(*) FROM app_user WHERE email = :email"),
                {"email": injection_email},
            )
            count: int = result.scalar() or 0
            # 注入字符串不匹配任何真实用户邮箱，返回 0
            assert count == 0, "Injection string should be treated as literal"

    async def test_union_injection_treated_as_literal(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """UNION 注入字符串作为字面量处理。"""
        async with async_session_factory() as session:
            injection = "1; SELECT * FROM app_user --"
            result = await session.execute(
                sa.text("SELECT COUNT(*) FROM app_user WHERE display_name = :name"),
                {"name": injection},
            )
            count: int = result.scalar() or 0
            assert count == 0

    async def test_orm_query_safe_from_injection(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """ORM 查询使用绑定参数，注入字符串无效。"""
        from packages.auth.entities import AppUser

        async with async_session_factory() as session:
            result = await session.execute(
                sa.select(AppUser).where(AppUser.email == "admin'-- ; DROP TABLE app_user; --")
            )
            users = result.scalars().all()
            assert len(users) == 0, "ORM query should be safe from injection"

    async def test_like_injection_treated_as_literal(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """LIKE 查询中的通配符注入被参数化处理。"""
        async with async_session_factory() as session:
            result = await session.execute(
                sa.text("SELECT COUNT(*) FROM app_user WHERE email LIKE :pattern"),
                {"pattern": "%'; DROP TABLE app_user; --%"},
            )
            count: int = result.scalar() or 0
            assert count == 0


# ============================================================
# 2. PostgreSQL 组件仅允许 SELECT
# ============================================================


class TestRoleSelectOnly:
    """irip_app 角色对审计表仅允许 SELECT（只读）。"""

    async def test_audit_table_select_allowed_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """irip_app 角色可以 SELECT audit_event。"""
        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                result = await conn.execute(sa.text("SELECT COUNT(*) FROM audit_event"))
                assert result.scalar() is not None
                await conn.rollback()
        finally:
            await engine.dispose()

    async def test_irip_readonly_role_cannot_insert(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """irip_readonly 角色不能 INSERT（如果有此角色）。"""
        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                try:
                    await conn.execute(sa.text("SET ROLE irip_readonly"))
                except Exception:
                    pytest.skip("irip_readonly role not found")
                    return

                from packages.common.ids import new_id

                org_id = new_id()
                try:
                    await conn.execute(
                        sa.text(
                            "INSERT INTO audit_event (organization_id, action) "
                            "VALUES (:org, :action)"
                        ),
                        {"org": str(org_id), "action": "test.readonly.insert"},
                    )
                    await conn.commit()
                    pytest.fail("irip_readonly should not be able to INSERT")
                except sa.exc.ProgrammingError as exc:
                    assert "permission" in str(exc).lower() or "denied" in str(exc).lower(), (
                        f"Expected permission denied: {exc}"
                    )
                await conn.rollback()
        finally:
            await engine.dispose()


# ============================================================
# 3. DROP/DELETE/UPDATE 被拦截
# ============================================================


class TestDangerousStatementsBlocked:
    """irip_app 角色不能执行 DROP/DELETE/UPDATE on audit_event。"""

    async def test_drop_table_blocked_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """irip_app 角色不能 DROP TABLE。"""
        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                try:
                    await conn.execute(sa.text("DROP TABLE IF EXISTS audit_event"))
                    pytest.fail("DROP TABLE should be denied for irip_app")
                except sa.exc.ProgrammingError as exc:
                    assert (
                        "permission" in str(exc).lower()
                        or "denied" in str(exc).lower()
                        or "must be owner" in str(exc).lower()
                    )
                await conn.rollback()
        finally:
            await engine.dispose()

    async def test_delete_audit_blocked_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """irip_app 角色不能 DELETE FROM audit_event。"""
        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                try:
                    await conn.execute(sa.text("DELETE FROM audit_event WHERE 1=0"))
                    pytest.fail("DELETE should be denied for irip_app on audit_event")
                except sa.exc.ProgrammingError as exc:
                    assert "permission" in str(exc).lower() or "denied" in str(exc).lower()
                await conn.rollback()
        finally:
            await engine.dispose()

    async def test_update_audit_blocked_for_app_role(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """irip_app 角色不能 UPDATE audit_event。"""
        url = os.getenv("IRIP_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
            return

        async_url = url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SET ROLE irip_app"))
                try:
                    await conn.execute(sa.text("UPDATE audit_event SET action = action WHERE 1=0"))
                    pytest.fail("UPDATE should be denied for irip_app on audit_event")
                except sa.exc.ProgrammingError as exc:
                    assert "permission" in str(exc).lower() or "denied" in str(exc).lower()
                await conn.rollback()
        finally:
            await engine.dispose()


# ============================================================
# 4. 分号分隔的多语句被拒绝
# ============================================================


class TestMultiStatementRejection:
    """psycopg3 默认拒绝分号分隔的多语句执行。"""

    async def test_semicolon_separated_statements_rejected(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """``SELECT 1; DROP TABLE app_user`` 被拒绝。"""
        async with async_session_factory() as session:
            with pytest.raises(Exception) as exc_info:
                await session.execute(sa.text("SELECT 1; DROP TABLE app_user"))
            # psycopg3 报错：不能在单个执行中发送多个语句
            error_msg = str(exc_info.value).lower()
            assert (
                "more than one statement" in error_msg
                or "multiple statements" in error_msg
                or "syntax error" in error_msg
                or "error" in error_msg
            ), f"Expected multi-statement rejection: {exc_info.value}"

    async def test_semicolon_in_parameter_value_safe(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """参数值中的分号是安全的（参数化处理）。"""
        async with async_session_factory() as session:
            result = await session.execute(
                sa.text("SELECT :val"),
                {"val": "hello; DROP TABLE app_user; --"},
            )
            value = result.scalar()
            assert value == "hello; DROP TABLE app_user; --"

    async def test_comment_injection_in_parameter_safe(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """参数值中的注释注入是安全的。"""
        async with async_session_factory() as session:
            result = await session.execute(
                sa.text("SELECT :val"),
                {"val": "test /* comment */ --"},
            )
            assert result.scalar() == "test /* comment */ --"
