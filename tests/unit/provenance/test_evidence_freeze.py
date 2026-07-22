"""证据集冻结与配方发布单元测试（IRIP Task 17）。

验证：
- 冻结证据集后，所有成员引用精确事实修订（fact_revision > 0, status="frozen"）；
- 质量过滤仅纳入质量通过的事实；
- 冻结后证据集不可变（再次冻结 → 错误）；
- 配方发布创建不可变版本；
- 推导使用不存在的组件 → AppError(code="component_unavailable")。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL）。
复用 tests/unit/facts/conftest.py 的 fact_service / fact_setup fixtures。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.observations import (
    NormalizedObservationInput,
    RawObservationInput,
)
from packages.facts.service import CreateFactCommand, FactService
from packages.provenance.evidence import EvidenceService
from packages.provenance.recipes import RecipeService


@pytest.fixture
async def evidence_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> "EvidenceService":
    """证据集服务（使用 test_user 的 org_id），测试后清理。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = EvidenceService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    # 清理证据集相关数据
    with sync_engine.connect() as conn:
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
        conn.commit()


@pytest.fixture
async def recipe_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> "RecipeService":
    """推导配方服务（使用 test_user 的 org_id），测试后清理。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    service = RecipeService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=actor_id,
    )
    yield service

    # 清理配方相关数据
    with sync_engine.connect() as conn:
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
        conn.commit()


def _make_fact_command(
    setup: dict,
    subject_id: str = "EV-001",
    value: str = "42.5",
    idempotency_key: str | None = None,
) -> CreateFactCommand:
    """构建创建事实命令的辅助函数。"""
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
        idempotency_key=idempotency_key,
        created_by=setup["actor_id"],
    )


class TestEvidenceFreeze:
    """证据集冻结测试。"""

    @pytest.mark.asyncio
    async def test_frozen_evidence_members_reference_exact_revisions(
        self,
        evidence_service: EvidenceService,
        fact_service: FactService,
        fact_setup: dict,
    ) -> None:
        """冻结证据集后，所有成员引用精确事实修订。

        验证：
        1. 创建事实；
        2. 创建证据集；
        3. 冻结证据集；
        4. 所有成员 fact_revision > 0；
        5. 证据集 status="frozen"。
        """
        # 1. 创建事实
        command = _make_fact_command(fact_setup, subject_id="EV-FREEZE-001")
        ref = await fact_service.create(command)
        assert ref.revision == 1

        # 2. 创建证据集
        create_result = await evidence_service.create_set("Test Evidence Set")
        set_id = create_result["set_id"]

        # 3. 冻结证据集
        ev_ref = await evidence_service.freeze(set_id)
        assert ev_ref.status == "frozen"
        assert ev_ref.version == 1
        assert ev_ref.member_count >= 1

        # 4. 验证成员引用精确修订
        members = await evidence_service.list_members(set_id)
        assert len(members) >= 1
        for m in members:
            assert m.fact_revision > 0
            assert m.decision == "included"

        # 验证包含刚创建的事实
        member_fact_ids = {m.fact_id for m in members}
        assert ref.fact_id in member_fact_ids

        # 5. 验证证据集状态
        set_detail = await evidence_service.get_set(set_id)
        assert set_detail["status"] == "frozen"

    @pytest.mark.asyncio
    async def test_freeze_with_quality_filter(
        self,
        evidence_service: EvidenceService,
        fact_service: FactService,
        fact_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """质量过滤仅纳入质量通过的事实。

        流程：
        1. 创建事实；
        2. 手动插入 quality_assessment 记录（overall_status="passed"）；
        3. 创建证据集并按 quality="passed" 过滤冻结；
        4. 验证成员包含该事实。
        """
        # 1. 创建事实
        command = _make_fact_command(fact_setup, subject_id="EV-QUALITY-001")
        ref = await fact_service.create(command)

        # 2. 插入 quality_assessment 记录
        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO quality_assessment "
                    "(fact_revision_id, overall_status, summary, results) "
                    "VALUES (:fr_id, 'passed', "
                    "'{\"passed\": 1, \"warning\": 0, \"blocked\": 0}'::jsonb, "
                    "'[]'::jsonb)"
                ),
                {"fr_id": ref.revision_id},
            )

        # 3. 创建证据集并按质量过滤冻结
        create_result = await evidence_service.create_set("Quality Filter Set")
        set_id = create_result["set_id"]

        ev_ref = await evidence_service.freeze(
            set_id, fact_filter={"quality": "passed"}
        )
        assert ev_ref.status == "frozen"
        assert ev_ref.member_count >= 1

        # 4. 验证成员包含该事实
        members = await evidence_service.list_members(set_id)
        member_fact_ids = {m.fact_id for m in members}
        assert ref.fact_id in member_fact_ids

    @pytest.mark.asyncio
    async def test_frozen_set_immutable(
        self,
        evidence_service: EvidenceService,
        fact_service: FactService,
        fact_setup: dict,
    ) -> None:
        """冻结后证据集不可变（再次冻结 → 错误）。

        流程：
        1. 创建证据集并冻结；
        2. 尝试再次冻结 → AppError。
        """
        # 创建事实
        command = _make_fact_command(fact_setup, subject_id="EV-IMMUTABLE-001")
        await fact_service.create(command)

        # 创建并冻结证据集
        create_result = await evidence_service.create_set("Immutable Set")
        set_id = create_result["set_id"]
        await evidence_service.freeze(set_id)

        # 再次冻结 → 错误
        with pytest.raises(AppError) as exc_info:
            await evidence_service.freeze(set_id)

        assert exc_info.value.code == "evidence_not_frozen"


class TestRecipePublish:
    """配方发布测试。"""

    @pytest.mark.asyncio
    async def test_recipe_publish_creates_immutable_version(
        self,
        recipe_service: RecipeService,
    ) -> None:
        """发布配方创建不可变版本。

        验证：
        1. 创建配方（draft）；
        2. 发布版本 → RecipeVersion；
        3. 版本号 = 1，status = "published"；
        4. 配方 status 变为 "published"。
        """
        # 1. 创建配方
        create_result = await recipe_service.create_recipe(
            code="test-recipe-001",
            display_name="测试配方",
        )
        recipe_id = create_result["recipe_id"]
        assert create_result["status"] == "draft"

        # 2. 发布版本
        rv = await recipe_service.publish_version(
            recipe_id=recipe_id,
            component_name="robust-parameter-estimator",
            component_version="0.1.0",
            parameters={"outlier_method": "mad", "threshold": 3.5},
            random_seed=42,
            output_definitions=("estimated_value",),
        )

        # 3. 验证版本
        assert rv.version == 1
        assert rv.status == "published"
        assert rv.component_name == "robust-parameter-estimator"
        assert rv.component_version == "0.1.0"
        assert rv.random_seed == 42
        assert rv.output_definitions == ("estimated_value",)

        # 4. 验证配方状态
        detail = await recipe_service.get_recipe(recipe_id)
        assert detail["status"] == "published"
        assert detail["version"] == 1


class TestComponentUnavailable:
    """推导组件不可用测试。"""

    @pytest.mark.asyncio
    async def test_component_unavailable(
        self,
        evidence_service: EvidenceService,
        recipe_service: RecipeService,
        fact_service: FactService,
        fact_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """推导使用不存在的组件 → AppError(code="component_unavailable")。

        流程：
        1. 创建事实 + 冻结证据集；
        2. 创建配方 + 发布版本（使用不存在的组件名）；
        3. 创建推导运行 → AppError(code="component_unavailable")。
        """
        from packages.provenance.derivations import DerivationService

        # 1. 创建事实
        command = _make_fact_command(fact_setup, subject_id="EV-COMP-001")
        await fact_service.create(command)

        # 创建证据集并冻结
        create_result = await evidence_service.create_set("Comp Test Set")
        set_id = create_result["set_id"]
        ev_ref = await evidence_service.freeze(set_id)

        # 2. 创建配方 + 发布版本（使用不存在的组件）
        recipe_create = await recipe_service.create_recipe(
            code="test-recipe-unavail",
            display_name="不存在的组件配方",
        )
        recipe_id = recipe_create["recipe_id"]

        rv = await recipe_service.publish_version(
            recipe_id=recipe_id,
            component_name="non-existent-component",
            component_version="0.0.0",
            parameters={},
            random_seed=42,
            output_definitions=("output",),
        )

        # 3. 创建推导运行 → AppError
        org_id = fact_setup["organization_id"]
        actor_id = fact_setup["actor_id"]
        derivation_service = DerivationService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )

        with pytest.raises(AppError) as exc_info:
            await derivation_service.create_run(
                evidence_set_version_id=ev_ref.version_id,
                recipe_version_id=rv.id,
            )

        assert exc_info.value.code == "component_unavailable"
