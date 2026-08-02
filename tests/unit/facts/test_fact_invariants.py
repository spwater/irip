"""事实不变量单元测试（标准层空表清理后精简版）。

验证：
- 创建事实成功 → FactRef 返回正确；
- 幂等键返回已有事实（不创建重复）；
- 事实类型校验；
- 全文搜索按 subject_id 和 fact_type 查找；
- 列表过滤正确。

原模板发布校验测试（test_template_must_be_published）与依赖
FactTemplateVersion 的 list 测试已随 migration 0057 删除。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL）。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.facts.service import CreateFactCommand, FactService


def _make_command(
    setup: dict,
    subject_id: str = "S-001",
    idempotency_key: str | None = None,
    fact_type: str = "experiment_run",
) -> CreateFactCommand:
    """构建创建事实命令的辅助函数。

    Args:
        setup: fact_setup fixture 返回的字典。
        subject_id: 主体标识。
        idempotency_key: 幂等键。
        fact_type: 事实类型。

    Returns:
        CreateFactCommand: 创建事实命令。
    """
    return CreateFactCommand(
        fact_type=fact_type,  # type: ignore[arg-type]
        department_id=setup["department_id"],
        object_id=setup["object_id"],
        subject_id=subject_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        idempotency_key=idempotency_key,
        created_by=setup["actor_id"],
    )


class TestCreateFact:
    """事实创建测试。"""

    @pytest.mark.asyncio
    async def test_create_fact_success(self, fact_service: FactService, fact_setup: dict) -> None:
        """创建事实成功 → returns FactRef with correct fields。"""
        command = _make_command(fact_setup, subject_id="S-SUCCESS-001")
        ref = await fact_service.create(command)
        assert ref.fact_type == "experiment_run"
        assert ref.subject_id == "S-SUCCESS-001"
        assert ref.status == "active"

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

        # 用相同 idempotency_key 再次创建 → 返回已有事实
        ref2 = await fact_service.create(command)
        assert ref2.fact_id == ref1.fact_id

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

        # 创建一个 simulation_run 事实
        cmd_sim = _make_command(fact_setup, subject_id="S-LIST-SIM", fact_type="simulation_run")
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
