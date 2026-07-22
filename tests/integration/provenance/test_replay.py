"""推导回放与溯源图集成测试（IRIP Task 17）。

验证：
- 回放推导运行产生相同 output_digest 但不同 run id；
- 溯源图连通推导 → 事实 → 观察值；
- 相同证据 + 相同配方 → 相同 output_digest（确定性）。

设置完整的 L1→L2→L2.5 证据链：
已发布变量 → 已发布模板 → 已发布方法 → 工业对象 → 创建事实 →
冻结证据集 → 发布配方 → 创建推导运行 → 回放 → 溯源图

使用真实 DB session（非 mock），验证完整确定性回放。
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
from packages.facts.service import CreateFactCommand, FactService
from packages.provenance.derivations import DerivationService
from packages.provenance.evidence import EvidenceService
from packages.provenance.graph import ProvenanceGraphService
from packages.provenance.recipes import RecipeService
from packages.standards.methods import Method, MethodVersion
from packages.standards.objects import IndustrialObject
from packages.standards.templates import FactTemplate, FactTemplateVersion
from packages.standards.variables import Variable, VariableVersion


@pytest.fixture
async def provenance_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> dict:
    """创建完整的 L1 标准链 + L2 事实。

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
            code=f"prov_var_{variable_id.hex[:8]}",
            display_name="溯源测试变量",
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
            code=f"prov_method_{method_id.hex[:8]}",
            display_name="溯源测试方法",
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
            code=f"prov_obj_{object_id.hex[:8]}",
            display_name="溯源测试对象",
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
            code=f"prov_tpl_{template_id.hex[:8]}",
            display_name="溯源测试模板",
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

    # 清理（从 L2.5 到 L1 倒序删除）
    with sync_engine.connect() as conn:
        # L2.5: 溯源与推导
        conn.execute(
            sa.text(
                "DELETE FROM provenance_edge WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM derivation_run WHERE organization_id = :oid"
            ),
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
            sa.text(
                "DELETE FROM transformation_recipe "
                "WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM evidence_set_version WHERE evidence_set_id IN ("
                "SELECT id FROM evidence_set WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM evidence_set WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        )
        # L2: 事实
        conn.execute(
            sa.text(
                "DELETE FROM quality_assessment WHERE fact_revision_id IN ("
                "SELECT fr.id FROM fact_revision fr "
                "JOIN fact f ON fr.fact_id = f.id "
                "WHERE f.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
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
        # L1
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


def _make_fact_command(
    setup: dict,
    subject_id: str,
    value: str,
) -> CreateFactCommand:
    """构建创建事实命令。"""
    raw_id = new_id()
    return CreateFactCommand(
        fact_type="experiment_run",
        template_version_id=setup["template_version_id"],
        organization_id=setup["organization_id"],
        object_id=setup["object_id"],
        subject_id=subject_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        method_version_id=setup["method_version_id"],
        raw=(
            RawObservationInput(
                id=raw_id,
                source_path="particle_size",
                source_value=value,
                source_unit="um",
            ),
        ),
        normalized=(
            NormalizedObservationInput(
                variable_version_id=setup["variable_version_id"],
                raw_observation_id=raw_id,
                value=value,
                unit="um",
            ),
        ),
        artifacts=(),
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
        7. 图包含 fact_revision 节点；
        8. 图包含 observation 节点；
        9. 边连通 derivation_run → fact_revision → observation。
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

        # 7. 图包含 fact_revision 节点
        fr_nodes = [n for n in graph.nodes if n.node_type == "fact_revision"]
        assert len(fr_nodes) >= 1
        assert any(n.id == fact_ref.revision_id for n in fr_nodes)

        # 8. 图包含 observation 节点
        obs_nodes = [n for n in graph.nodes if n.node_type == "observation"]
        assert len(obs_nodes) >= 1

        # 9. 边连通 derivation_run → fact_revision → observation
        # 验证存在 selected_from 边
        selected_from_edges = [
            e for e in graph.edges if e.edge_type == "selected_from"
        ]
        assert len(selected_from_edges) >= 1

        # 验证 derivation_run → fact_revision 边
        dr_to_fr_edges = [
            e
            for e in graph.edges
            if e.source_type == "derivation_run"
            and e.target_type == "fact_revision"
        ]
        assert len(dr_to_fr_edges) >= 1

        # 验证 fact_revision → observation 边
        fr_to_obs_edges = [
            e
            for e in graph.edges
            if e.source_type == "fact_revision"
            and e.target_type == "observation"
        ]
        assert len(fr_to_obs_edges) >= 1

        # 验证溯源路径：从 derivation_run 可达 observation
        # 构建邻接表
        adjacency: dict[UUID, list[UUID]] = {}
        for e in graph.edges:
            if e.source_id not in adjacency:
                adjacency[e.source_id] = []
            adjacency[e.source_id].append(e.target_id)

        # BFS 从 derivation_run 到 observation
        visited: set[UUID] = set()
        queue: list[UUID] = [run_ref.id]
        reached_observations: set[UUID] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for neighbor in adjacency.get(current, []):
                # 检查是否为 observation 节点
                neighbor_node = next(
                    (n for n in graph.nodes if n.id == neighbor), None
                )
                if neighbor_node and neighbor_node.node_type == "observation":
                    reached_observations.add(neighbor)
                if neighbor not in visited:
                    queue.append(neighbor)

        assert len(reached_observations) >= 1, (
            "溯源图应从推导运行可达观察值节点"
        )
