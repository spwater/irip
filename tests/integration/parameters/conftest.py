"""参数审批集成测试 fixtures（IRIP Task 18）。

创建完整的 L1→L2→L2.5 证据链：
已发布变量 → 已发布模板 → 已发布方法 → 工业对象 → 创建事实 →
冻结证据集 → 发布配方 → 创建推导运行

供参数审批、过期检测等测试复用。
"""

from datetime import UTC, datetime

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
from packages.provenance.recipes import RecipeService
from packages.standards.methods import Method, MethodVersion
from packages.standards.objects import IndustrialObject
from packages.standards.templates import FactTemplate, FactTemplateVersion
from packages.standards.variables import Variable, VariableVersion


@pytest.fixture
async def param_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> dict:
    """创建完整的 L1 标准链 + L2 事实 + L2.5 推导链。

    返回所有创建实体的 ID，供参数测试使用。
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
            code=f"param_var_{variable_id.hex[:8]}",
            display_name="参数测试变量",
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
            code=f"param_method_{method_id.hex[:8]}",
            display_name="参数测试方法",
            description="参数审批测试方法",
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
            code=f"param_obj_{object_id.hex[:8]}",
            display_name="参数测试对象",
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
            code=f"param_tpl_{template_id.hex[:8]}",
            display_name="参数测试模板",
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
        "variable_code": variable.code,
        "method_id": method_id,
        "method_version_id": method_version_id,
        "object_id": object_id,
        "template_id": template_id,
        "template_version_id": template_version_id,
        "organization_id": org_id,
        "actor_id": actor_id,
    }

    # 清理（从 L3 到 L1 倒序删除）
    with sync_engine.connect() as conn:
        # L3: 参数
        conn.execute(
            sa.text(
                "DELETE FROM parameter_staleness WHERE parameter_version_id IN ("
                "SELECT pv.id FROM parameter_version pv "
                "JOIN parameter p ON pv.parameter_id = p.id "
                "WHERE p.organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM parameter_candidate WHERE parameter_id IN ("
                "SELECT id FROM parameter WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM parameter_version WHERE parameter_id IN ("
                "SELECT id FROM parameter WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM parameter WHERE organization_id = :oid"),
            {"oid": org_id},
        )
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
        conn.execute(
            sa.text(
                "DELETE FROM evidence_set_version WHERE evidence_set_id IN ("
                "SELECT id FROM evidence_set WHERE organization_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM evidence_set WHERE organization_id = :oid"),
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


async def _create_derivation_chain(
    setup: dict,
    async_session_factory: async_sessionmaker[AsyncSession],
    num_facts: int = 3,
    recipe_code: str = "param-recipe-001",
    subject_prefix: str = "PARAM",
) -> dict:
    """创建完整推导链：事实 → 证据集 → 配方 → 推导运行。

    返回包含 run_ref 和 fact_refs 的字典。
    """
    org_id = setup["organization_id"]
    actor_id = setup["actor_id"]

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

    # 1. 创建事实
    fact_refs = []
    for i in range(num_facts):
        command = _make_fact_command(
            setup,
            subject_id=f"{subject_prefix}-BATCH-{i:03d}",
            value=f"{40 + i * 0.5}",
        )
        ref = await fact_service.create(command)
        fact_refs.append(ref)

    # 2. 冻结证据集
    create_result = await evidence_service.create_set("Parameter Test Set")
    set_id = create_result["set_id"]
    ev_ref = await evidence_service.freeze(set_id)
    assert ev_ref.member_count >= num_facts

    # 3. 发布配方
    recipe_create = await recipe_service.create_recipe(
        code=recipe_code,
        display_name="参数测试配方",
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

    return {
        "run_ref": run_ref,
        "fact_refs": fact_refs,
        "ev_ref": ev_ref,
        "recipe_version": rv,
    }
