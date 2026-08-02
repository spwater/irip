"""事实模块测试 fixtures（标准层空表清理后精简版）。

提供：
- fact_service: 连接测试数据库的 FactService，测试后自动清理事实数据。
- fact_setup: 工业对象（供创建事实时引用）。
- 依赖 tests/conftest.py 的 sync_engine / async_session_factory / test_user fixtures。

原 fixture 创建已发布模板版本 + 变量版本，依赖已删除的
FactTemplate / FactTemplateVersion / Variable / VariableVersion
（migration 0057）。CreateFactCommand 不再需要 template_version_id，
fact_setup 仅创建工业对象。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.ids import new_id
from packages.facts.service import FactService
from packages.standards.objects import IndustrialObject


@pytest.fixture
async def fact_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[FactService]:
    """FactService（使用 test_user 的 org_id），测试后清理事实数据。

    cleanup：删除该组织下的全部 fact_data_index / fact 记录。
    """
    org_id = test_user.department_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = FactService(
        session_factory=async_session_factory,
        department_id=org_id,
        actor_id=actor_id,
    )
    yield service

    # 清理：删除该组织下的全部事实相关数据
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM fact_data_index WHERE fact_id IN ("
                "SELECT id FROM fact WHERE department_id = :oid)"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact WHERE department_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def fact_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[dict]:
    """创建工业对象供创建事实时引用。

    返回字典：
        {
            "object_id": UUID,
            "department_id": UUID,
            "actor_id": UUID,
        }

    测试后自动清理工业对象与事实数据。
    """
    org_id = test_user.department_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    from packages.common.database import session_scope

    object_id = new_id()

    now = datetime.now(UTC)

    async with session_scope(async_session_factory) as session:
        # 插入工业对象
        obj = IndustrialObject(
            id=object_id,
            department_id=org_id,
            object_type="lab",
            code=f"test_obj_{object_id.hex[:8]}",
            display_name="测试对象",
            visibility_scope="tree",
            owner_user_id=actor_id,
            status="active",
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(obj)
        await session.flush()

    yield {
        "object_id": object_id,
        "department_id": org_id,
        "actor_id": actor_id,
    }

    # 清理事实数据 + 工业对象
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM fact_data_index WHERE fact_id IN ("
                "SELECT id FROM fact WHERE department_id = :oid)"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact WHERE department_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM industrial_object WHERE department_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()
