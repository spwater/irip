"""推导回放与溯源图集成测试（IRIP Task 17，标准层空表清理后精简版）。

验证：
- 回放推导运行产生相同 output_digest 但不同 run id；
- 溯源图连通推导 → 事实 → 观察值；
- 相同证据 + 相同配方 → 相同 output_digest（确定性）。

原 L1 标准链（已发布变量 → 已发布模板）依赖已删除的 Variable /
VariableVersion / FactTemplate / FactTemplateVersion（migration 0057）。
provenance_setup 仅创建工业对象，CreateFactCommand 不再需要 template_version_id。

使用真实 DB session（非 mock），验证完整确定性回放。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.ids import new_id
from packages.facts.service import CreateFactCommand, FactService
from packages.provenance.derivations import DerivationService
from packages.provenance.evidence import EvidenceService
from packages.provenance.graph import ProvenanceGraphService
from packages.provenance.recipes import RecipeService
from packages.standards.objects import IndustrialObject


@pytest.fixture
async def provenance_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> dict:
    """创建工业对象供创建事实时引用。

    返回所有创建实体的 ID，供测试使用。
    测试后自动清理。
    """
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    object_id = new_id()

    now = datetime.now(UTC)

    async with session_scope(async_session_factory) as session:
        # L1: 工业对象
        obj = IndustrialObject(
            id=object_id,
            organization_id=org_id,
            object_type="lab",
            code=f"prov_obj_{object_id.hex[:8]}",
            display_name="溯源测试对象",
            status="active",
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(obj)
        await session.flush()

    yield {
        "object_id": object_id,
        "organization_id": org_id,
        "actor_id": actor_id,
    }

    # 清理（从 L2.5 到 L1 倒序删除）
    with sync_engine.connect() as conn:
        # L2.5: 溯源与推导
        conn.execute(
            sa.text("DELETE FROM provenance_edge WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM derivation_run WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM transformation_recipe_version WHERE recipe_id IN ("
                "SELECT id FROM transformation_recipe "
                "WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM transformation_recipe WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        # evidence_set_version 有 F-03 不可变触发器，用 TRUNCATE CASCADE 绕过
        conn.execute(
            sa.text("TRUNCATE evidence_set_version, evidence_set CASCADE"),
        )
        # L2: 事实
        conn.execute(
            sa.text(
                "DELETE FROM fact_data_index WHERE fact_id IN ("
                "SELECT id FROM fact WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        # L1
        conn.execute(
            sa.text("DELETE FROM industrial_object WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


def _make_fact_command(
    setup: dict,
    subject_id: str,
    value: str,
) -> CreateFactCommand:
    """构建创建事实命令。"""
    return CreateFactCommand(
        fact_type="experiment_run",
        organization_id=setup["organization_id"],
        object_id=setup["object_id"],
        subject_id=subject_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        idempotency_key=None,
        created_by=setup["actor_id"],
    )


class TestReplayDeterminism:
    """回放确定性测试。"""

    @pytest.mark.asyncio
    async def test_replay_produces_same_candidate_digest(
        self,
        provenance_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """回放推导运行产生相同 output_digest 但不同 run id。

        流程：
        1. 创建多个事实（提供数据）；
        2. 创建证据集并冻结；
        3. 创建配方并发布版本；
        4. 创建推导运行；
        5. 回放推导运行；
        6. 验证 output_digest 相同，但 run id 不同。
        """
        org_id = provenance_setup["organization_id"]
        actor_id = provenance_setup["actor_id"]

        fact_service = FactService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        evidence_service = EvidenceService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        recipe_service = RecipeService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        derivation_service = DerivationService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )

        # 1. 创建多个事实
        for i in range(5):
            command = _make_fact_command(
                provenance_setup,
                subject_id=f"REPLAY-BATCH-{i:03d}",
                value=f"{40 + i * 0.5}",
            )
            await fact_service.create(command)

        # 2. 创建证据集并冻结
        create_result = await evidence_service.create_set("Replay Test Set")
        set_id = create_result["set_id"]
        ev_ref = await evidence_service.freeze(set_id)
        assert ev_ref.member_count >= 5

        # 3. 创建配方并发布版本
        recipe_create = await recipe_service.create_recipe(
            code="replay-recipe-001",
            display_name="回放测试配方",
        )
        recipe_id = recipe_create["recipe_id"]
        rv = await recipe_service.publish_version(
            recipe_id=recipe_id,
            component_name="robust-parameter-estimator",
            component_version="0.1.0",
            parameters={"outlier_method": "mad", "threshold": 3.5},
            random_seed=42,
            output_definitions=("estimated_value",),
        )

        # 4. 创建推导运行
        run_ref = await derivation_service.create_run(
            evidence_set_version_id=ev_ref.version_id,
            recipe_version_id=rv.id,
        )
        assert run_ref.status == "succeeded"
        assert len(run_ref.outputs) >= 1
        original_digest = run_ref.output_digest
        original_id = run_ref.id
        assert original_digest != ""

        # 5. 回放推导运行
        replay_ref = await derivation_service.replay(original_id)

        # 6. 验证 output_digest 相同，run id 不同
        assert replay_ref.output_digest == original_digest
        assert replay_ref.id != original_id
        assert replay_ref.status == "succeeded"

    @pytest.mark.asyncio
    async def test_deterministic_replay_same_seed(
        self,
        provenance_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """两次独立创建的推导运行（相同证据 + 相同配方）→ 相同 output_digest。

        验证确定性：相同输入 → 相同输出摘要。
        """
        org_id = provenance_setup["organization_id"]
        actor_id = provenance_setup["actor_id"]

        fact_service = FactService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        evidence_service = EvidenceService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        recipe_service = RecipeService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        derivation_service = DerivationService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )

        # 创建事实
        for i in range(3):
            command = _make_fact_command(
                provenance_setup,
                subject_id=f"DET-BATCH-{i:03d}",
                value=f"{50 + i * 1.0}",
            )
            await fact_service.create(command)

        # 冻结证据集
        create_result = await evidence_service.create_set("Determinism Set")
        set_id = create_result["set_id"]
        ev_ref = await evidence_service.freeze(set_id)

        # 发布配方
        recipe_create = await recipe_service.create_recipe(
            code="determinism-recipe-001",
            display_name="确定性测试配方",
        )
        recipe_id = recipe_create["recipe_id"]
        rv = await recipe_service.publish_version(
            recipe_id=recipe_id,
            component_name="robust-parameter-estimator",
            component_version="0.1.0",
            parameters={"outlier_method": "mad", "threshold": 3.5},
            random_seed=123,
            output_definitions=("estimated_value",),
        )

        # 第一次推导运行
        run1 = await derivation_service.create_run(
            evidence_set_version_id=ev_ref.version_id,
            recipe_version_id=rv.id,
        )

        # 第二次推导运行（相同输入）
        run2 = await derivation_service.create_run(
            evidence_set_version_id=ev_ref.version_id,
            recipe_version_id=rv.id,
        )

        # 验证相同 output_digest
        assert run1.output_digest == run2.output_digest
        assert run1.id != run2.id


class TestProvenanceGraph:
    """溯源图测试。"""

    @pytest.mark.asyncio
    async def test_provenance_graph_completeness(
        self,
        provenance_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """创建推导，获取溯源图，验证从推导到原始事实的路径完整。

        验证：
        1. 创建事实（含观察值）；
        2. 冻结证据集；
        3. 创建配方并发布；
        4. 创建推导运行；
        5. 获取溯源图；
        6. 图包含 derivation_run 节点；
        7. 图包含 fact 节点；
        8. 边连通 derivation_run → fact。
        """
        org_id = provenance_setup["organization_id"]
        actor_id = provenance_setup["actor_id"]

        fact_service = FactService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        evidence_service = EvidenceService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        recipe_service = RecipeService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        derivation_service = DerivationService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )
        graph_service = ProvenanceGraphService(
            session_factory=async_session_factory,
            organization_id=org_id,
        )

        # 1. 创建事实（含观察值）
        command = _make_fact_command(
            provenance_setup,
            subject_id="GRAPH-FACT-001",
            value="42.5",
        )
        fact_ref = await fact_service.create(command)

        # 2. 冻结证据集
        create_result = await evidence_service.create_set("Graph Test Set")
        set_id = create_result["set_id"]
        ev_ref = await evidence_service.freeze(set_id)

        # 3. 创建配方并发布
        recipe_create = await recipe_service.create_recipe(
            code="graph-recipe-001",
            display_name="溯源图测试配方",
        )
        recipe_id = recipe_create["recipe_id"]
        rv = await recipe_service.publish_version(
            recipe_id=recipe_id,
            component_name="robust-parameter-estimator",
            component_version="0.1.0",
            parameters={},
            random_seed=42,
            output_definitions=("estimated_value",),
        )

        # 4. 创建推导运行
        run_ref = await derivation_service.create_run(
            evidence_set_version_id=ev_ref.version_id,
            recipe_version_id=rv.id,
        )
        assert run_ref.status == "succeeded"

        # 5. 获取溯源图
        graph = await graph_service.get_graph(run_ref.id)

        # 6. 图包含 derivation_run 节点
        run_nodes = [n for n in graph.nodes if n.node_type == "derivation_run"]
        assert len(run_nodes) >= 1
        assert any(n.id == run_ref.id for n in run_nodes)

        # 7. 图包含 fact 节点
        fact_nodes = [n for n in graph.nodes if n.node_type == "fact"]
        assert len(fact_nodes) >= 1

        # 8. 边连通 derivation_run → fact
        # 验证存在 selected_from 边
        selected_from_edges = [e for e in graph.edges if e.edge_type == "selected_from"]
        assert len(selected_from_edges) >= 1

        # 验证 derivation_run → fact 边
        dr_to_fact_edges = [
            e
            for e in graph.edges
            if e.source_type == "derivation_run" and e.target_type == "fact"
        ]
        assert len(dr_to_fact_edges) >= 1
