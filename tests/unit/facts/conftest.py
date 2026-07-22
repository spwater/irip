"""事实模块测试 fixtures。

提供：
- fact_service: 连接测试数据库的 FactService，测试后自动清理事实数据。
- fact_setup: 已发布的模板版本、方法版本、工业对象、变量版本，
  供创建事实时引用。
- 依赖 tests/conftest.py 的 sync_engine / async_session_factory / test_user fixtures。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.ids import new_id
from packages.facts.service import FactService
from packages.standards.methods import Method, MethodVersion
from packages.standards.objects import IndustrialObject
from packages.standards.templates import FactTemplate, FactTemplateVersion
from packages.standards.variables import Variable, VariableVersion


@pytest.fixture
async def fact_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[FactService]:
    """FactService（使用 test_user 的 org_id），测试后清理事实数据。

    cleanup：删除该组织下的全部 fact_revision / raw_observation /
    normalized_observation / fact_artifact / fact_revision_link / fact 记录。
    """
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = FactService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    # 清理：删除该组织下的全部事实相关数据
    with sync_engine.connect() as conn:
        # 先删关联数据
        conn.execute(
            sa.text(
                "DELETE FROM fact_revision_link WHERE from_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM fact_artifact WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM normalized_observation WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM raw_observation WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM fact_revision WHERE fact_id IN ("
                "SELECT id FROM fact WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def fact_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: Engine,
) -> AsyncIterator[dict]:
    """创建已发布的模板版本、方法版本、工业对象、变量版本。

    返回字典：
        {
            "template_version_id": UUID,
            "method_version_id": UUID,
            "object_id": UUID,
            "variable_version_id": UUID,
            "organization_id": UUID,
        }

    测试后自动清理这些标准数据。
    """
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    # 直接插入已发布的标准数据
    from packages.common.database import session_scope

    template_id = new_id()
    template_version_id = new_id()
    method_id = new_id()
    method_version_id = new_id()
    object_id = new_id()
    variable_id = new_id()
    variable_version_id = new_id()

    now = datetime.now(UTC)

    async with session_scope(async_session_factory) as session:
        # 插入已发布的 variable + variable_version
        variable = Variable(
            id=variable_id,
            organization_id=org_id,
            code=f"test_var_{variable_id.hex[:8]}",
            display_name="测试变量",
            data_type="number",
            canonical_unit="mm",
            quantity_kind="length",
            status="published",
            version_count=1,
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(variable)
        var_version = VariableVersion(
            id=variable_version_id,
            variable_id=variable_id,
            version=1,
            code=variable.code,
            display_name=variable.display_name,
            data_type="number",
            canonical_unit="mm",
            quantity_kind="length",
            status="published",
            published_at=now,
            published_by=actor_id,
            lock_version=0,
        )
        session.add(var_version)

        # 插入已发布的 method + method_version
        method = Method(
            id=method_id,
            organization_id=org_id,
            code=f"test_method_{method_id.hex[:8]}",
            display_name="测试方法",
            description="测试用方法",
            status="published",
            version_count=1,
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(method)
        method_version = MethodVersion(
            id=method_version_id,
            method_id=method_id,
            version=1,
            code=method.code,
            display_name=method.display_name,
            description=method.description,
            status="published",
            published_at=now,
            published_by=actor_id,
            lock_version=0,
        )
        session.add(method_version)

        # 插入工业对象
        obj = IndustrialObject(
            id=object_id,
            organization_id=org_id,
            object_type="lab",
            code=f"test_obj_{object_id.hex[:8]}",
            display_name="测试对象",
            status="active",
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(obj)

        # 插入已发布的 fact_template + fact_template_version
        template = FactTemplate(
            id=template_id,
            organization_id=org_id,
            code=f"test_tpl_{template_id.hex[:8]}",
            display_name="测试模板",
            fact_type="experiment_run",
            status="published",
            version_count=1,
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(template)
        template_version = FactTemplateVersion(
            id=template_version_id,
            template_id=template_id,
            version=1,
            code=template.code,
            display_name=template.display_name,
            fact_type="experiment_run",
            required_conditions=[],
            observations=[
                {
                    "variable_version_id": str(variable_version_id),
                    "required": True,
                    "cardinality": "one",
                }
            ],
            required_artifact_roles=[],
            quality_rule_codes=[],
            status="published",
            published_at=now,
            published_by=actor_id,
            lock_version=0,
        )
        session.add(template_version)
        await session.flush()

    yield {
        "template_version_id": template_version_id,
        "method_version_id": method_version_id,
        "object_id": object_id,
        "variable_version_id": variable_version_id,
        "organization_id": org_id,
        "actor_id": actor_id,
    }

    # 清理标准数据（先删事实相关数据，再删标准数据）
    with sync_engine.connect() as conn:
        # 先删事实相关数据（避免 FK 冲突）
        conn.execute(
            sa.text(
                "DELETE FROM fact_revision_link WHERE from_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM fact_artifact WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM normalized_observation WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM raw_observation WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM fact_revision WHERE fact_id IN ("
                "SELECT id FROM fact WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        # 再删标准数据
        conn.execute(
            sa.text(
                "DELETE FROM fact_template_version WHERE template_id IN ("
                "SELECT id FROM fact_template WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM fact_template WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM method_version WHERE method_id IN ("
                "SELECT id FROM method WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM method WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM industrial_object WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM variable_version WHERE variable_id IN ("
                "SELECT id FROM variable WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM variable WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()
