"""事实修订链端到端集成测试（IRIP Task 15）。

设置完整的 L1→L2 证据链：
已发布变量 → 已发布模板 → 已发布方法 → 工业对象 → 创建事实 → 修订 → 查询历史

使用真实 DB session（非 mock），验证完整修订链保留。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.ids import new_id
from packages.facts.observations import (
    NormalizedObservationInput,
    RawObservationInput,
)
from packages.facts.repository import FactRepository
from packages.facts.service import CreateFactCommand, FactService
from packages.standards.methods import Method, MethodVersion
from packages.standards.objects import IndustrialObject
from packages.standards.templates import FactTemplate, FactTemplateVersion
from packages.standards.variables import Variable, VariableVersion


@pytest.fixture
async def integration_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> dict:
    """创建完整的 L1 标准链：变量 → 模板 → 方法 → 工业对象。

    返回所有创建实体的 ID，供测试使用。
    测试后自动清理。
    """
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    variable_id = new_id()
    variable_version_id = new_id()
    method_id = new_id()
    method_version_id = new_id()
    object_id = new_id()
    template_id = new_id()
    template_version_id = new_id()

    now = datetime.now(UTC)

    async with session_scope(async_session_factory) as session:
        # L1: 已发布变量
        variable = Variable(
            id=variable_id,
            organization_id=org_id,
            code=f"integ_var_{variable_id.hex[:8]}",
            display_name="集成测试变量",
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

        # L1: 已发布方法
        method = Method(
            id=method_id,
            organization_id=org_id,
            code=f"integ_method_{method_id.hex[:8]}",
            display_name="集成测试方法",
            description="端到端测试方法",
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

        # L1: 工业对象
        obj = IndustrialObject(
            id=object_id,
            organization_id=org_id,
            object_type="lab",
            code=f"integ_obj_{object_id.hex[:8]}",
            display_name="集成测试对象",
            status="active",
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(obj)

        # L1: 已发布事实模板
        template = FactTemplate(
            id=template_id,
            organization_id=org_id,
            code=f"integ_tpl_{template_id.hex[:8]}",
            display_name="集成测试模板",
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
        "variable_id": variable_id,
        "variable_version_id": variable_version_id,
        "method_id": method_id,
        "method_version_id": method_version_id,
        "object_id": object_id,
        "template_id": template_id,
        "template_version_id": template_version_id,
        "organization_id": org_id,
        "actor_id": actor_id,
    }

    # 清理
    with sync_engine.connect() as conn:
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
        conn.execute(
            sa.text(
                "DELETE FROM fact_template_version WHERE template_id IN ("
                "SELECT id FROM fact_template WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact_template WHERE organization_id = :oid"),
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


@pytest.fixture
async def integration_fact_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    integration_setup: dict,
) -> FactService:
    """创建 FactService 实例用于集成测试。"""
    return FactService(
        session_factory=async_session_factory,
        organization_id=integration_setup["organization_id"],
        actor_id=integration_setup["actor_id"],
    )


class TestFactRevisionChain:
    """事实修订链端到端集成测试。"""

    @pytest.mark.asyncio
    async def test_full_revision_chain(
        self,
        integration_fact_service: FactService,
        integration_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """端到端：创建事实 → 修订 3 次 → 查询历史 → 验证完整链。

        验证：
        1. 创建事实（rev 1）→ revision=1, status=active
        2. 修订（rev 2）→ 修改 subject_id
        3. 修订（rev 3）→ 修改 subject_id
        4. 查询历史 → 3 个修订，按升序排列
        5. 查询 rev 1 → 旧 subject_id（不可变）
        6. 查询 rev 3 → 新 subject_id
        7. 观察值在各修订中正确保留
        8. 修订链链接存在
        """
        # 1. 创建事实
        raw_id = new_id()
        command = CreateFactCommand(
            fact_type="experiment_run",
            template_version_id=integration_setup["template_version_id"],
            organization_id=integration_setup["organization_id"],
            object_id=integration_setup["object_id"],
            subject_id="BATCH-2026-001",
            started_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            ended_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            method_version_id=integration_setup["method_version_id"],
            raw=(
                RawObservationInput(
                    id=raw_id,
                    source_path="particle_size",
                    source_value="42.5",
                    source_unit="um",
                ),
            ),
            normalized=(
                NormalizedObservationInput(
                    variable_version_id=integration_setup["variable_version_id"],
                    raw_observation_id=raw_id,
                    value="0.0425",
                    unit="mm",
                ),
            ),
            artifacts=(),
            idempotency_key="integ-batch-001",
            created_by=integration_setup["actor_id"],
        )

        ref1 = await integration_fact_service.create(command)
        assert ref1.revision == 1
        assert ref1.subject_id == "BATCH-2026-001"
        assert ref1.status == "active"

        # 2. 修订 → rev 2
        ref2 = await integration_fact_service.revise(
            ref1.fact_id,
            reason="修正批次号",
            changes={"subject_id": "BATCH-2026-001A"},
        )
        assert ref2.revision == 2
        assert ref2.subject_id == "BATCH-2026-001A"

        # 3. 修订 → rev 3
        ref3 = await integration_fact_service.revise(
            ref1.fact_id,
            reason="再次修正",
            changes={"subject_id": "BATCH-2026-001B"},
        )
        assert ref3.revision == 3
        assert ref3.subject_id == "BATCH-2026-001B"

        # 4. 查询历史 → 3 个修订
        revisions = await integration_fact_service.list_revisions(ref1.fact_id)
        assert len(revisions) == 3
        assert revisions[0].revision == 1
        assert revisions[1].revision == 2
        assert revisions[2].revision == 3

        # 5. 查询 rev 1 → 旧 subject_id
        old = await integration_fact_service.get(ref1.fact_id, revision=1)
        assert old.subject_id == "BATCH-2026-001"

        # 6. 查询 rev 3 → 新 subject_id
        latest = await integration_fact_service.get(ref1.fact_id)
        assert latest.revision == 3
        assert latest.subject_id == "BATCH-2026-001B"

        # 7. 观察值在各修订中正确保留
        raws_r1, norms_r1 = await integration_fact_service.get_observations(
            ref1.fact_id, revision=1
        )
        assert len(raws_r1) == 1
        assert raws_r1[0].source_path == "particle_size"
        assert raws_r1[0].source_value == "42.5"
        assert len(norms_r1) == 1
        assert norms_r1[0].value == "0.0425"

        raws_r3, norms_r3 = await integration_fact_service.get_observations(
            ref1.fact_id, revision=3
        )
        assert len(raws_r3) == 1
        assert raws_r3[0].source_path == "particle_size"
        assert len(norms_r3) == 1

        # 8. 修订链链接存在
        async with async_session_factory() as session:
            link_r2 = await FactRepository.get_revision_link(
                session, ref2.revision_id
            )
            link_r3 = await FactRepository.get_revision_link(
                session, ref3.revision_id
            )
        assert link_r2 is not None
        assert link_r2.from_revision_id == ref2.revision_id
        assert link_r2.to_revision_id == ref1.revision_id
        assert link_r2.link_type == "supersedes"

        assert link_r3 is not None
        assert link_r3.from_revision_id == ref3.revision_id
        assert link_r3.to_revision_id == ref2.revision_id
        assert link_r3.link_type == "supersedes"

    @pytest.mark.asyncio
    async def test_search_and_filter(
        self,
        integration_fact_service: FactService,
        integration_setup: dict,
    ) -> None:
        """端到端：创建事实 → 搜索 → 过滤列表。"""
        raw_id = new_id()
        command = CreateFactCommand(
            fact_type="experiment_run",
            template_version_id=integration_setup["template_version_id"],
            organization_id=integration_setup["organization_id"],
            object_id=integration_setup["object_id"],
            subject_id="SEARCH-INTEG-001",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=None,
            method_version_id=None,
            raw=(
                RawObservationInput(
                    id=raw_id,
                    source_path="temp",
                    source_value="25",
                ),
            ),
            normalized=(
                NormalizedObservationInput(
                    variable_version_id=integration_setup["variable_version_id"],
                    raw_observation_id=raw_id,
                    value="25",
                ),
            ),
            artifacts=(),
            idempotency_key="integ-search-001",
            created_by=integration_setup["actor_id"],
        )

        await integration_fact_service.create(command)

        # 搜索
        refs, _ = await integration_fact_service.search("SEARCH-INTEG")
        assert len(refs) >= 1
        assert any(r.subject_id == "SEARCH-INTEG-001" for r in refs)

        # 过滤列表
        refs_list, _ = await integration_fact_service.list_facts(
            filters={"fact_type": "experiment_run"}
        )
        assert any(r.subject_id == "SEARCH-INTEG-001" for r in refs_list)
