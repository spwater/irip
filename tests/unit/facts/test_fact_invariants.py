"""事实不变量单元测试（IRIP Task 15）。

验证：
- 创建事实成功 → revision=1；
- 标准化观察值必须有原始引用 → normalized_without_raw 错误；
- 幂等键返回已有事实（不创建重复）；
- 修订保留前一版本数据；
- 修订号递增；
- 修订不可变（旧修订数据在新修订后仍可查询）；
- 事实类型校验；
- 模板必须已发布；
- 原始与标准化观察值正确存储与查询；
- 全文搜索按 subject_id 和 fact_type 查找；
- 列表过滤正确；
- 修订按修订号排序；
- 修订链链接已创建。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL）。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.observations import (
    NormalizedObservationInput,
    RawObservationInput,
)
from packages.facts.repository import FactRepository
from packages.facts.service import CreateFactCommand, FactService


def _make_command(
    setup: dict,
    subject_id: str = "S-001",
    idempotency_key: str | None = None,
    fact_type: str = "experiment_run",
    template_version_id: UUID | None = None,
    raw_count: int = 1,
    norm_raw_none: bool = False,
) -> CreateFactCommand:
    """构建创建事实命令的辅助函数。

    使用预生成 UUID 让 normalized 引用同一 command 中的 raw。

    Args:
        setup: fact_setup fixture 返回的字典。
        subject_id: 主体标识。
        idempotency_key: 幂等键。
        fact_type: 事实类型。
        template_version_id: 模板版本 ID（None 使用 setup 中的）。
        raw_count: 原始观察值数量。
        norm_raw_none: 是否将 normalized 的 raw_observation_id 设为 None。

    Returns:
        CreateFactCommand: 创建事实命令。
    """
    # 预生成 raw observation IDs
    raw_ids = [new_id() for _ in range(raw_count)]

    raw_inputs = tuple(
        RawObservationInput(
            id=raw_ids[i],
            source_path=f"field_{i}",
            source_value=f"value_{i}",
            source_unit="mm" if i == 0 else "°C",
        )
        for i in range(raw_count)
    )

    if norm_raw_none:
        norm_raw_id: UUID | None = None
    else:
        norm_raw_id = raw_ids[0]

    norm_inputs = (
        NormalizedObservationInput(
            variable_version_id=setup["variable_version_id"],
            raw_observation_id=norm_raw_id,
            value="42.5",
            unit="mm",
        ),
    )

    return CreateFactCommand(
        fact_type=fact_type,  # type: ignore[arg-type]
        template_version_id=template_version_id or setup["template_version_id"],
        organization_id=setup["organization_id"],
        object_id=setup["object_id"],
        subject_id=subject_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        method_version_id=setup["method_version_id"],
        raw=raw_inputs,
        normalized=norm_inputs,
        artifacts=(),
        idempotency_key=idempotency_key,
        created_by=setup["actor_id"],
    )


class TestCreateFact:
    """事实创建测试。"""

    @pytest.mark.asyncio
    async def test_create_fact_success(self, fact_service: FactService, fact_setup: dict) -> None:
        """创建事实成功 → returns FactRevisionRef with revision=1。"""
        command = _make_command(fact_setup, subject_id="S-SUCCESS-001")
        ref = await fact_service.create(command)
        assert ref.revision == 1
        assert ref.fact_type == "experiment_run"
        assert ref.subject_id == "S-SUCCESS-001"
        assert ref.status == "active"

    @pytest.mark.asyncio
    async def test_normalized_observation_requires_raw_source(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """标准化观察值缺原始引用 → raises AppError(normalized_without_raw)。"""
        command = _make_command(
            fact_setup,
            subject_id="S-NORAW-001",
            norm_raw_none=True,
        )
        with pytest.raises(AppError) as exc_info:
            await fact_service.create(command)
        assert exc_info.value.code == "normalized_without_raw"

    @pytest.mark.asyncio
    async def test_idempotency_returns_existing(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """幂等键返回已有事实（不创建重复）。"""
        command = _make_command(
            fact_setup,
            subject_id="S-IDEM-001",
            idempotency_key="idem-key-001",
        )
        ref1 = await fact_service.create(command)
        assert ref1.revision == 1

        # 用相同 idempotency_key 再次创建 → 返回已有事实
        ref2 = await fact_service.create(command)
        assert ref2.fact_id == ref1.fact_id
        assert ref2.revision == ref1.revision

    @pytest.mark.asyncio
    async def test_fact_type_validation(self, fact_service: FactService, fact_setup: dict) -> None:
        """无效 fact_type → error。"""
        command = _make_command(
            fact_setup,
            subject_id="S-FTYPE-001",
            fact_type="invalid_type",  # type: ignore[arg-type]
        )
        with pytest.raises(AppError) as exc_info:
            await fact_service.create(command)
        assert exc_info.value.code == "validation_failed"

    @pytest.mark.asyncio
    async def test_template_must_be_published(
        self,
        fact_service: FactService,
        fact_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """模板版本未发布 → error。"""
        from packages.standards.templates import (
            FactTemplate,
            FactTemplateVersion,
        )

        # 创建一个未发布的模板 + 版本
        tpl_id = new_id()
        tv_id = new_id()
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            tpl = FactTemplate(
                id=tpl_id,
                organization_id=fact_setup["organization_id"],
                code=f"unpub_tpl_{tpl_id.hex[:8]}",
                display_name="未发布模板",
                fact_type="experiment_run",
                status="draft",
                version_count=1,
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            session.add(tpl)
            unpublished_tv = FactTemplateVersion(
                id=tv_id,
                template_id=tpl_id,
                version=1,
                code=tpl.code,
                display_name=tpl.display_name,
                fact_type="experiment_run",
                status="draft",
                lock_version=0,
            )
            session.add(unpublished_tv)
            await session.flush()
            await session.commit()

        command = _make_command(
            fact_setup,
            subject_id="S-UNPUB-001",
            template_version_id=tv_id,
        )
        with pytest.raises(AppError) as exc_info:
            await fact_service.create(command)
        assert exc_info.value.code == "template_not_published"


class TestRevisionHistory:
    """修订历史测试。"""

    @pytest.mark.asyncio
    async def test_revision_preserves_previous_version(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """修订保留前一版本数据。"""
        command = _make_command(fact_setup, subject_id="S-REV-001")
        ref1 = await fact_service.create(command)
        assert ref1.revision == 1
        assert ref1.subject_id == "S-REV-001"

        # 修订 → revision 2，修改 subject_id
        ref2 = await fact_service.revise(
            ref1.fact_id,
            reason="修正主体标识",
            changes={"subject_id": "S-REV-002"},
        )
        assert ref2.revision == 2
        assert ref2.subject_id == "S-REV-002"

        # 获取 revision 1 → 仍返回旧 subject_id
        old_ref = await fact_service.get(ref1.fact_id, revision=1)
        assert old_ref.revision == 1
        assert old_ref.subject_id == "S-REV-001"

        # 获取 revision 2 → 返回新 subject_id
        new_ref = await fact_service.get(ref1.fact_id, revision=2)
        assert new_ref.revision == 2
        assert new_ref.subject_id == "S-REV-002"

        # 获取最新 → 返回 revision 2
        latest = await fact_service.get(ref1.fact_id)
        assert latest.revision == 2
        assert latest.subject_id == "S-REV-002"

    @pytest.mark.asyncio
    async def test_revision_increments(self, fact_service: FactService, fact_setup: dict) -> None:
        """修订号递增：create (rev 1) → revise (rev 2) → revise (rev 3)。"""
        command = _make_command(fact_setup, subject_id="S-INC-001")
        ref1 = await fact_service.create(command)
        ref2 = await fact_service.revise(
            ref1.fact_id, reason="第一次修订", changes={"subject_id": "S-INC-002"}
        )
        ref3 = await fact_service.revise(
            ref1.fact_id, reason="第二次修订", changes={"subject_id": "S-INC-003"}
        )

        assert ref1.revision == 1
        assert ref2.revision == 2
        assert ref3.revision == 3

        revisions = await fact_service.list_revisions(ref1.fact_id)
        assert len(revisions) == 3

    @pytest.mark.asyncio
    async def test_list_revisions_ordered(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """修订按修订号升序排列。"""
        command = _make_command(fact_setup, subject_id="S-ORD-001")
        ref1 = await fact_service.create(command)
        await fact_service.revise(ref1.fact_id, reason="r2")
        await fact_service.revise(ref1.fact_id, reason="r3")

        revisions = await fact_service.list_revisions(ref1.fact_id)
        assert len(revisions) == 3
        assert revisions[0].revision == 1
        assert revisions[1].revision == 2
        assert revisions[2].revision == 3

    @pytest.mark.asyncio
    async def test_revision_link_created(
        self,
        fact_service: FactService,
        fact_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """修订后 fact_revision_link 存在（from → to）。"""
        command = _make_command(fact_setup, subject_id="S-LINK-001")
        ref1 = await fact_service.create(command)
        ref2 = await fact_service.revise(ref1.fact_id, reason="修订")

        async with async_session_factory() as session:
            link = await FactRepository.get_revision_link(session, ref2.revision_id)
        assert link is not None
        assert link.from_revision_id == ref2.revision_id
        assert link.to_revision_id == ref1.revision_id
        assert link.link_type == "supersedes"

    @pytest.mark.asyncio
    async def test_immutable_revision(
        self,
        fact_service: FactService,
        fact_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """修订不可变：旧修订数据在新修订后仍可查询且内容不变。"""
        command = _make_command(fact_setup, subject_id="S-IMM-001")
        ref1 = await fact_service.create(command)
        await fact_service.revise(ref1.fact_id, reason="修订", changes={"subject_id": "S-IMM-002"})

        # 旧修订 subject_id 不变
        old = await fact_service.get(ref1.fact_id, revision=1)
        assert old.subject_id == "S-IMM-001"

        # 直接查询数据库确认旧修订未被修改
        async with async_session_factory() as session:
            rev = await FactRepository.get_revision(
                session, ref1.fact_id, 1, fact_setup["organization_id"]
            )
        assert rev.subject_id == "S-IMM-001"


class TestObservations:
    """观察值测试。"""

    @pytest.mark.asyncio
    async def test_raw_and_normalized_observations(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """创建事实 → 获取观察值 → raw 和 normalized 列表正确。"""
        raw_id_1 = new_id()
        raw_id_2 = new_id()

        command = CreateFactCommand(
            fact_type="experiment_run",
            template_version_id=fact_setup["template_version_id"],
            organization_id=fact_setup["organization_id"],
            object_id=fact_setup["object_id"],
            subject_id="S-OBS-001",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=None,
            method_version_id=None,
            raw=(
                RawObservationInput(
                    id=raw_id_1,
                    source_path="temperature",
                    source_value="25.5",
                    source_unit="°C",
                ),
                RawObservationInput(
                    id=raw_id_2,
                    source_path="pressure",
                    source_value="101.3",
                    source_unit="kPa",
                ),
            ),
            normalized=(
                NormalizedObservationInput(
                    variable_version_id=fact_setup["variable_version_id"],
                    raw_observation_id=raw_id_1,
                    value="25.5",
                    unit="°C",
                ),
            ),
            artifacts=(),
            idempotency_key=None,
            created_by=fact_setup["actor_id"],
        )

        ref = await fact_service.create(command)
        raws, norms = await fact_service.get_observations(ref.fact_id)

        assert len(raws) == 2
        # 不假设排序，通过 source_path 查找
        raw_by_path = {r.source_path: r for r in raws}
        assert "temperature" in raw_by_path
        assert raw_by_path["temperature"].source_value == "25.5"
        assert raw_by_path["temperature"].source_unit == "°C"
        assert "pressure" in raw_by_path
        assert raw_by_path["pressure"].source_value == "101.3"

        assert len(norms) == 1
        assert norms[0].value == "25.5"
        assert norms[0].unit == "°C"
        assert norms[0].variable_version_id == fact_setup["variable_version_id"]


class TestSearch:
    """全文搜索测试。"""

    @pytest.mark.asyncio
    async def test_search_finds_by_subject_id(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """创建 subject_id="S-SEARCH-001" → 搜索 "S-SEARCH-001" → 找到。"""
        command = _make_command(fact_setup, subject_id="S-SEARCH-001")
        await fact_service.create(command)

        refs, _ = await fact_service.search("S-SEARCH-001")
        assert len(refs) >= 1
        found = [r for r in refs if r.subject_id == "S-SEARCH-001"]
        assert len(found) == 1

    @pytest.mark.asyncio
    async def test_search_finds_by_fact_type(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """搜索 "experiment" → 找到实验事实。"""
        command = _make_command(fact_setup, subject_id="S-SEARCH-FT-001")
        await fact_service.create(command)

        refs, _ = await fact_service.search("experiment")
        assert len(refs) >= 1
        found = [r for r in refs if r.subject_id == "S-SEARCH-FT-001"]
        assert len(found) == 1


class TestListFacts:
    """事实列表测试。"""

    @pytest.mark.asyncio
    async def test_list_facts_with_filters(
        self, fact_service: FactService, fact_setup: dict
    ) -> None:
        """创建多个事实 → 按 fact_type 过滤 → 正确子集。"""
        # 创建一个 experiment_run 事实
        cmd_exp = _make_command(fact_setup, subject_id="S-LIST-EXP", fact_type="experiment_run")
        await fact_service.create(cmd_exp)

        # 创建一个 simulation_run 事实（需要不同模板）
        from packages.common.database import session_scope
        from packages.standards.templates import (
            FactTemplate,
            FactTemplateVersion,
        )

        sim_template_id = new_id()
        sim_tv_id = new_id()
        now = datetime.now(UTC)
        async with session_scope(fact_service._factory) as session:
            t = FactTemplate(
                id=sim_template_id,
                organization_id=fact_setup["organization_id"],
                code=f"sim_tpl_{sim_template_id.hex[:8]}",
                display_name="仿真模板",
                fact_type="simulation_run",
                status="published",
                version_count=1,
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            session.add(t)
            tv = FactTemplateVersion(
                id=sim_tv_id,
                template_id=sim_template_id,
                version=1,
                code=t.code,
                display_name=t.display_name,
                fact_type="simulation_run",
                status="published",
                published_at=now,
                published_by=fact_setup["actor_id"],
                lock_version=0,
            )
            session.add(tv)
            await session.flush()

        cmd_sim = _make_command(
            fact_setup,
            subject_id="S-LIST-SIM",
            fact_type="simulation_run",
            template_version_id=sim_tv_id,
        )
        await fact_service.create(cmd_sim)

        # 按 experiment_run 过滤
        refs, _ = await fact_service.list_facts(filters={"fact_type": "experiment_run"})
        exp_subjects = [r.subject_id for r in refs]
        assert "S-LIST-EXP" in exp_subjects
        assert "S-LIST-SIM" not in exp_subjects

        # 按 simulation_run 过滤
        refs_sim, _ = await fact_service.list_facts(filters={"fact_type": "simulation_run"})
        sim_subjects = [r.subject_id for r in refs_sim]
        assert "S-LIST-SIM" in sim_subjects
        assert "S-LIST-EXP" not in sim_subjects
