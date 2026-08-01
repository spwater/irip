"""参数审批集成测试 fixtures（IRIP Task 18，标准层空表清理后精简版）。

创建完整的 L2→L2.5 证据链：
工业对象 → 创建事实 → 冻结证据集 → 发布配方 → 创建推导运行

原 L1 标准链（已发布变量 → 已发布模板）依赖已删除的 Variable /
VariableVersion / FactTemplate / FactTemplateVersion（migration 0057）。
CreateFactCommand 不再需要 template_version_id，param_setup 仅创建工业对象。

供参数审批、过期检测等测试复用。
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.ids import new_id
from packages.facts.service import CreateFactCommand, FactService
from packages.provenance.derivations import DerivationService
from packages.provenance.evidence import EvidenceService
from packages.provenance.recipes import RecipeService
from packages.standards.objects import IndustrialObject


@pytest.fixture
async def param_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> dict:
    """创建工业对象供创建事实时引用。

    返回所有创建实体的 ID，供参数测试使用。
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
            code=f"param_obj_{object_id.hex[:8]}",
            display_name="参数测试对象",
            status="active",
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(obj)
        await session.flush()

    # variable_code 是 parameter 表的纯文本列（无 FK 到已删除的 variable 表），
    # 使用静态字符串即可。
    yield {
        "object_id": object_id,
        "variable_code": f"param_var_{object_id.hex[:8]}",
        "organization_id": org_id,
        "actor_id": actor_id,
    }

    # 清理（从 L3 到 L1 倒序删除）
    with sync_engine.connect() as conn:
        # L3: 参数
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
