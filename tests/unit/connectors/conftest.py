"""连接器与映射评分测试 fixtures。

提供：
- mapping_service: 连接测试数据库的 MappingService；
- mapping_profile_service: 连接测试数据库的 MappingProfileService；
- published_variable: 已发布标准变量（含别名）工厂函数；
- 依赖 tests/conftest.py 的 sync_engine / async_session_factory / test_user fixtures。
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.connectors.mapping import (
    MappingProfileService,
    MappingService,
)
from packages.standards.service import StandardService


@pytest.fixture
async def mapping_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
) -> AsyncIterator[MappingService]:
    """MappingService（使用 test_user 的 org_id）。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]
    service = MappingService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service


@pytest.fixture
async def mapping_profile_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[MappingProfileService]:
    """MappingProfileService，测试后清理映射配置与密钥数据。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]
    service = MappingProfileService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM mapping_profile_version WHERE profile_id IN "
                "(SELECT id FROM mapping_profile WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM mapping_profile WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM secret WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def standard_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[StandardService]:
    """StandardService，用于创建已发布标准变量，测试后清理。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]
    service = StandardService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

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


async def create_published_variable(
    standard_service: StandardService,
    *,
    code: str,
    display_name: str,
    data_type: str = "number",
    canonical_unit: str | None = "mm",
    alias: str | None = None,
) -> UUID:
    """创建并发布一个标准变量（可选别名），返回已发布版本 ID。

    Args:
        standard_service: 标准变量服务。
        code: 变量编码。
        display_name: 显示名。
        data_type: 数据类型。
        canonical_unit: 标准单位。
        alias: 别名（可选）。

    Returns:
        UUID: 已发布变量版本 ID。
    """
    await standard_service.create_variable(
        code=code,
        display_name=display_name,
        data_type=data_type,
        canonical_unit=canonical_unit,
    )
    detail = await standard_service.get_variable_by_code(code)
    variable_id = UUID(detail["id"])
    if alias is not None:
        await standard_service.add_alias(variable_id, alias=alias, language="en")
    version = await standard_service.submit_for_review(variable_id)
    await standard_service.publish_variable(variable_id)
    return version.id
