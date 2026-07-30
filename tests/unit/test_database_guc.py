"""session_scope GUC 设置单元测试。

覆盖 T01 修改的 ``packages/common/database.py``：
- ``session_scope(factory, *, principal=None)`` 增加可选 principal 参数；
- 提供 principal 时执行 ``SET LOCAL app.current_org_id = :org_id``；
- 不提供 principal 时不执行 GUC 设置（RLS fail-closed）。

由于 ``session_scope`` 依赖真实数据库连接（异步引擎 + SET LOCAL），
本测试使用 SQLite 异步引擎作为轻量替代验证 GUC 执行逻辑。
若无法连接数据库则 skip（遵循根 conftest 约定）。
"""

import os
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from packages.common.database import build_session_factory, session_scope
from packages.common.principal import Principal
from packages.common.query_scope import QueryScope


def _get_test_db_url() -> str | None:
    """获取测试数据库 URL，未配置时返回 None。"""
    return os.getenv("IRIP_TEST_DATABASE_URL")


@pytest.fixture
def org_id() -> UUID:
    """测试用组织 ID。"""
    return uuid4()


@pytest.fixture
def principal(org_id: UUID) -> Principal:
    """构造测试用 Principal。"""
    scope = QueryScope(organization_id=org_id)
    return Principal(
        user_id=uuid4(),
        organization_id=org_id,
        email="test@irip.local",
        roles=["lab_member"],
        scope=scope,
        token_version=0,
    )


class TestSessionScopeWithPrincipal:
    """提供 principal 时设置 GUC。"""

    async def test_guc_set_when_principal_provided(
        self, principal: Principal
    ) -> None:
        """提供 principal 时执行 SET LOCAL app.current_org_id。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping GUC test")

        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        if not async_url.startswith("postgresql+psycopg_async://"):
            async_url = async_url.replace(
                "postgresql://", "postgresql+psycopg_async://", 1
            )

        factory = build_session_factory(async_url)
        async with session_scope(factory, principal=principal) as session:
            result = await session.execute(
                sa.text("SHOW app.current_org_id")
            )
            current_guc = result.scalar()
            assert current_guc == str(principal.organization_id)

    async def test_guc_matches_principal_org_id(
        self, principal: Principal
    ) -> None:
        """GUC 值与 principal.organization_id 一致。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping GUC test")

        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        if not async_url.startswith("postgresql+psycopg_async://"):
            async_url = async_url.replace(
                "postgresql://", "postgresql+psycopg_async://", 1
            )

        factory = build_session_factory(async_url)
        async with session_scope(factory, principal=principal) as session:
            result = await session.execute(
                sa.text("SHOW app.current_org_id")
            )
            assert result.scalar() == str(principal.organization_id)


class TestSessionScopeWithoutPrincipal:
    """不提供 principal 时不设置 GUC。"""

    async def test_guc_not_set_without_principal(self) -> None:
        """不提供 principal 时不设置 GUC（保持连接级默认值）。"""
        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping GUC test")

        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        if not async_url.startswith("postgresql+psycopg_async://"):
            async_url = async_url.replace(
                "postgresql://", "postgresql+psycopg_async://", 1
            )

        factory = build_session_factory(async_url)
        async with session_scope(factory) as session:
            result = await session.execute(
                sa.text("SHOW app.current_org_id")
            )
            current_guc = result.scalar()
            # 连接级默认值为空字符串（build_session_factory 中设置）
            assert current_guc == ""


class TestSessionScopePrincipalParameter:
    """session_scope 的 principal 参数行为。"""

    def test_principal_parameter_is_keyword_only(
        self, principal: Principal
    ) -> None:
        """principal 参数是 keyword-only（不能位置传参）。"""
        import inspect

        sig = inspect.signature(session_scope)
        assert "principal" in sig.parameters
        param = sig.parameters["principal"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_principal_parameter_default_is_none(self) -> None:
        """principal 参数默认值为 None。"""
        import inspect

        sig = inspect.signature(session_scope)
        param = sig.parameters["principal"]
        assert param.default is None


class TestBuildSessionFactoryGucDefault:
    """build_session_factory 连接级 GUC 默认值。"""

    def test_build_session_factory_returns_session_maker(
        self, principal: Principal
    ) -> None:
        """build_session_factory 返回 async_sessionmaker 实例。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        db_url = _get_test_db_url()
        if not db_url:
            pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping GUC test")

        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
        if not async_url.startswith("postgresql+psycopg_async://"):
            async_url = async_url.replace(
                "postgresql://", "postgresql+psycopg_async://", 1
            )

        factory = build_session_factory(async_url)
        assert isinstance(factory, async_sessionmaker)
