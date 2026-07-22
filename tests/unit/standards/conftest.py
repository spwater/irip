"""标准模块测试 fixtures。

提供：
- standard_service: 连接测试数据库的 StandardService，测试后自动清理变量数据。
- template_service / method_service / package_service: 同上，测试后自动清理。
- 依赖 tests/conftest.py 的 sync_engine / async_session_factory / test_user fixtures。
"""

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.standards.methods import MethodService
from packages.standards.packages import PackageService
from packages.standards.service import StandardService
from packages.standards.templates import TemplateService


@pytest.fixture
async def standard_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[StandardService]:
    """StandardService（使用 test_user 的 org_id），测试后清理标准变量数据。

    cleanup：删除该组织下的全部 variable_alias / variable_version / variable 记录。
    """
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = StandardService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    # 清理：删除该组织下的全部标准变量数据
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM variable_alias WHERE variable_id IN "
                "(SELECT id FROM variable WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM variable_version WHERE variable_id IN "
                "(SELECT id FROM variable WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM variable WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def template_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[TemplateService]:
    """TemplateService，测试后自动清理模板数据。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = TemplateService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM fact_template_version WHERE template_id IN "
                "(SELECT id FROM fact_template WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM fact_template WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def method_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[MethodService]:
    """MethodService，测试后自动清理方法数据。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = MethodService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM method_version WHERE method_id IN "
                "(SELECT id FROM method WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM method WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def package_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[PackageService]:
    """PackageService，测试后自动清理标准包数据。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = PackageService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM standard_package_version WHERE package_id IN "
                "(SELECT id FROM standard_package WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM standard_package WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        )
        conn.commit()
