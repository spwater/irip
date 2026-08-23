"""上下文构建 Mixin 单元测试。

覆盖 packages/research/execution/context_builder.py：
- _check_scope: snapshot 变更、question 变更、首次知识库、资源升级、正常；
- _build_research_context: DAG 步骤摘要、空计划、无 dag_structure；
- _load_snapshot_data: factory 为 None 返回空串、快照不存在返回空串。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.research.execution.context_builder import ContextBuilderMixin
from packages.research.execution.models_trusted import ScopeBoundary

# ---------------------------------------------------------------------------
# 辅助：构建可实例化的 ContextBuilderMixin 子类
# ---------------------------------------------------------------------------


class _TestableContextBuilder(ContextBuilderMixin):
    """可实例化的上下文构建器（绕过基类复杂构造）。"""

    def __init__(self, factory: Any = None) -> None:
        self._factory = factory
        self._dept_id = uuid4()


# ---------------------------------------------------------------------------
# _check_scope
# ---------------------------------------------------------------------------


class TestCheckScope:
    """范围越界检测。"""

    def test_within_scope(self) -> None:
        """所有条件匹配 → is_within_scope=True。"""
        builder = _TestableContextBuilder()
        scope = ScopeBoundary(
            snapshot_id=uuid4(),
            question_version=1,
            resource_tier="standard",
            knowledge_base_used=True,
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=scope.snapshot_id,
            current_question_version=1,
            current_method="python",
            current_resource_tier="standard",
        )
        assert result.is_within_scope is True
        assert result.violation_type == ""

    def test_snapshot_changed(self) -> None:
        """快照变更 → snapshot_changed。"""
        builder = _TestableContextBuilder()
        scope = ScopeBoundary(snapshot_id=uuid4(), question_version=1)
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=uuid4(),  # 不同
            current_question_version=1,
            current_method="python",
            current_resource_tier="standard",
        )
        assert result.is_within_scope is False
        assert result.violation_type == "snapshot_changed"

    def test_question_changed(self) -> None:
        """问题版本变更 → question_changed。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(snapshot_id=snapshot_id, question_version=1)
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=2,  # 不同
            current_method="python",
            current_resource_tier="standard",
        )
        assert result.is_within_scope is False
        assert result.violation_type == "question_changed"

    def test_knowledge_first_use(self) -> None:
        """首次使用知识库 → knowledge_first_use。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=1,
            knowledge_base_used=False,
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=1,
            current_method="knowledge",
            current_resource_tier="standard",
        )
        assert result.is_within_scope is False
        assert result.violation_type == "knowledge_first_use"

    def test_knowledge_already_used(self) -> None:
        """知识库已使用 → 不报越界。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=1,
            knowledge_base_used=True,
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=1,
            current_method="knowledge",
            current_resource_tier="standard",
        )
        assert result.is_within_scope is True

    def test_resource_upgraded(self) -> None:
        """资源升级 → resource_upgraded。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=1,
            resource_tier="standard",
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=1,
            current_method="python",
            current_resource_tier="heavy",
        )
        assert result.is_within_scope is False
        assert result.violation_type == "resource_upgraded"

    def test_resource_same_tier(self) -> None:
        """资源档位相同 → 不报越界。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=1,
            resource_tier="heavy",
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=1,
            current_method="python",
            current_resource_tier="heavy",
        )
        assert result.is_within_scope is True

    def test_resource_downgrade_ok(self) -> None:
        """资源降级 → 不报越界。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=1,
            resource_tier="heavy",
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=1,
            current_method="python",
            current_resource_tier="standard",
        )
        assert result.is_within_scope is True

    def test_unknown_tier_treated_as_standard(self) -> None:
        """未知资源档位 → 按 standard (0) 处理。"""
        builder = _TestableContextBuilder()
        snapshot_id = uuid4()
        scope = ScopeBoundary(
            snapshot_id=snapshot_id,
            question_version=1,
            resource_tier="standard",
        )
        result = builder._check_scope(
            scope=scope,
            current_snapshot_id=snapshot_id,
            current_question_version=1,
            current_method="python",
            current_resource_tier="unknown_tier",
        )
        assert result.is_within_scope is True


# ---------------------------------------------------------------------------
# _build_research_context
# ---------------------------------------------------------------------------


class TestBuildResearchContext:
    """研究上下文构建。"""

    def test_with_dag_steps(self) -> None:
        """有 DAG 步骤 → 返回步骤摘要。"""
        builder = _TestableContextBuilder()
        plan = MagicMock()
        plan.dag_structure = {
            "steps": [
                {"step_key": "s1", "question": "问题1"},
                {"step_key": "s2", "question": "问题2"},
            ]
        }
        result = builder._build_research_context(uuid4(), plan)
        assert "步骤 s1: 问题1" in result
        assert "步骤 s2: 问题2" in result

    def test_empty_steps(self) -> None:
        """空步骤列表 → 空字符串。"""
        builder = _TestableContextBuilder()
        plan = MagicMock()
        plan.dag_structure = {"steps": []}
        result = builder._build_research_context(uuid4(), plan)
        assert result == ""

    def test_no_dag_structure(self) -> None:
        """dag_structure 为 None → 空字符串。"""
        builder = _TestableContextBuilder()
        plan = MagicMock()
        plan.dag_structure = None
        result = builder._build_research_context(uuid4(), plan)
        assert result == ""

    def test_plan_is_none(self) -> None:
        """plan 为 None → 空字符串。"""
        builder = _TestableContextBuilder()
        result = builder._build_research_context(uuid4(), None)
        assert result == ""

    def test_plan_without_dag_structure_attr(self) -> None:
        """plan 无 dag_structure 属性 → 空字符串。"""
        builder = _TestableContextBuilder()
        plan = object()  # 无 dag_structure 属性
        result = builder._build_research_context(uuid4(), plan)
        assert result == ""

    def test_dag_not_dict(self) -> None:
        """dag_structure 非 dict → 空字符串。"""
        builder = _TestableContextBuilder()
        plan = MagicMock()
        plan.dag_structure = "not-a-dict"
        result = builder._build_research_context(uuid4(), plan)
        assert result == ""


# ---------------------------------------------------------------------------
# _load_snapshot_data
# ---------------------------------------------------------------------------


class TestLoadSnapshotData:
    """快照数据加载。"""

    @pytest.mark.asyncio
    async def test_factory_none_returns_empty(self) -> None:
        """factory 为 None → 空字符串。"""
        builder = _TestableContextBuilder(factory=None)
        result = await builder._load_snapshot_data(uuid4())
        assert result == ""

    @pytest.mark.asyncio
    async def test_snapshot_not_found_returns_empty(self) -> None:
        """快照不存在 → 空字符串。"""
        import contextlib

        mock_session = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=execute_result)

        @contextlib.asynccontextmanager
        async def _ctx():
            yield mock_session

        builder = _TestableContextBuilder(factory=_ctx)
        result = await builder._load_snapshot_data(uuid4())
        assert result == ""
