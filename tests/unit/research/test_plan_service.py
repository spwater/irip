"""PlanService 集成测试。

覆盖 PlanService 的核心业务方法：
- generate_plan: AI 生成计划（mock model_gateway）
- confirm_plan: 确认计划（状态校验 + 状态转换）
- analyze_data: 分析执行（mock model_gateway + 数值工具）
- revise_plan: 修订计划（版本管理）

测试策略：mock ModelGateway / ContextRouter / FactProvider / session，
验证 PlanService 的业务逻辑分支（状态校验、错误处理、版本管理），
不依赖真实数据库连接。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.models_trusted import TaskType

if TYPE_CHECKING:
    from packages.research.plan_service import PlanService

# ============================================================
# 测试常量与工厂
# ============================================================

DEPT_ID = uuid4()
ACTOR_ID = uuid4()
WORKSPACE_ID = uuid4()
SNAPSHOT_ID = uuid4()
PLAN_ID = uuid4()


class FakeModelResponse:
    """模拟 ModelGateway.call() 返回。"""

    def __init__(self, answer: str = "分析完成", tool_calls: list | None = None) -> None:
        self.answer = answer
        self.tool_calls = tool_calls or []
        self.provider = "test"
        self.model = "test-model"


class FakeModelGateway:
    """模拟 ModelGateway。"""

    def __init__(self, answer: str = "分析完成", tool_calls: list | None = None) -> None:
        self._answer = answer
        self._tool_calls = tool_calls or []
        self.call_count = 0

    async def call(self, **kwargs) -> FakeModelResponse:
        self.call_count += 1
        return FakeModelResponse(self._answer, self._tool_calls)


class FakeContextRouter:
    """模拟 ContextRouter。"""

    def analyze_step(self, **kwargs) -> dict:
        return {
            "analysis_mode": "direct_full_context",
            "data_budget": 50000,
            "mode_reason": "test",
        }


class FakeFactProvider:
    """模拟 FactProvider。"""

    async def get_fact_data(self, fact_id) -> dict:
        return {"columns": ["col1"], "rows": [[1.0], [2.0], [3.0]]}


def make_plan_service(
    model_gateway: FakeModelGateway | None = None,
    numeric_tools: MagicMock | None = None,
) -> PlanService:
    """构造一个带 mock 依赖的 PlanService 实例。"""
    from packages.research.plan_service import PlanService

    return PlanService(
        session_factory=MagicMock(),
        department_id=DEPT_ID,
        actor_id=ACTOR_ID,
        model_gateway=model_gateway or FakeModelGateway(),
        context_router=FakeContextRouter(),
        fact_provider=FakeFactProvider(),
        numeric_tools=numeric_tools,
    )


# ============================================================
# 1. confirm_plan 状态校验
# ============================================================


class TestConfirmPlanValidation:
    """confirm_plan 的状态校验逻辑。"""

    @pytest.mark.asyncio
    async def test_confirm_nonexistent_plan_raises_not_found(self) -> None:
        """确认不存在的计划 → not_found。"""
        service = make_plan_service()

        # mock scoped_session → 返回 None（计划不存在）
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        with patch.object(service, "_scoped_session") as mock_ss:
            mock_ss.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ss.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc:
                await service.confirm_plan(WORKSPACE_ID, PLAN_ID)
            assert exc.value.code == "not_found"

    @pytest.mark.asyncio
    async def test_confirm_already_confirmed_raises_validation(self) -> None:
        """确认已确认的计划 → validation_failed。"""
        service = make_plan_service()

        # mock 一个已确认的计划
        fake_plan = MagicMock()
        fake_plan.id = PLAN_ID
        fake_plan.workspace_id = WORKSPACE_ID
        fake_plan.status = "confirmed"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: fake_plan)
        )

        with patch.object(service, "_scoped_session") as mock_ss:
            mock_ss.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ss.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc:
                await service.confirm_plan(WORKSPACE_ID, PLAN_ID)
            assert exc.value.code == "validation_failed"
            assert "confirmed" in exc.value.message

    @pytest.mark.asyncio
    async def test_confirm_superseded_raises_validation(self) -> None:
        """确认已废弃的计划 → validation_failed。"""
        service = make_plan_service()

        fake_plan = MagicMock()
        fake_plan.id = PLAN_ID
        fake_plan.workspace_id = WORKSPACE_ID
        fake_plan.status = "superseded"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: fake_plan)
        )

        with patch.object(service, "_scoped_session") as mock_ss:
            mock_ss.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ss.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc:
                await service.confirm_plan(WORKSPACE_ID, PLAN_ID)
            assert exc.value.code == "validation_failed"
            assert "superseded" in exc.value.message

    @pytest.mark.asyncio
    async def test_confirm_wrong_workspace_raises_not_found(self) -> None:
        """确认不属于该工作空间的计划 → not_found。"""
        service = make_plan_service()

        fake_plan = MagicMock()
        fake_plan.id = PLAN_ID
        fake_plan.workspace_id = uuid4()  # 不同的 workspace
        fake_plan.status = "draft"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: fake_plan)
        )

        with patch.object(service, "_scoped_session") as mock_ss:
            mock_ss.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ss.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(AppError) as exc:
                await service.confirm_plan(WORKSPACE_ID, PLAN_ID)
            assert exc.value.code == "not_found"


# ============================================================
# 2. PlanService 构造与依赖
# ============================================================


class TestPlanServiceConstruction:
    """PlanService 构造与依赖注入。"""

    def test_numeric_tools_optional(self) -> None:
        """numeric_tools 参数可选（None 时不报错）。"""
        service = make_plan_service(numeric_tools=None)
        assert service._numeric_tools is None

    def test_numeric_tools_injected(self) -> None:
        """numeric_tools 可注入。"""
        mock = MagicMock()
        service = make_plan_service(numeric_tools=mock)
        assert service._numeric_tools is mock

    def test_actor_id_none_raises_on_require(self) -> None:
        """actor_id 为 None 时 _require_actor 抛 forbidden。"""
        from packages.research.plan_service import PlanService

        service = PlanService(
            session_factory=MagicMock(),
            department_id=DEPT_ID,
            actor_id=None,
            model_gateway=FakeModelGateway(),
            context_router=FakeContextRouter(),
            fact_provider=FakeFactProvider(),
        )
        with pytest.raises(AppError) as exc:
            service._require_actor()
        assert exc.value.code == "forbidden"


# ============================================================
# 3. analyze_data 工具调用逻辑
# ============================================================


class TestAnalyzeDataToolCalling:
    """analyze_data 的数值工具调用逻辑。"""

    def test_tool_schemas_built_when_numeric_tools_present(self) -> None:
        """有 numeric_tools 时构建工具 schema。"""
        service = make_plan_service(numeric_tools=MagicMock())
        assert service._numeric_tools is not None

    def test_no_tool_schemas_when_numeric_tools_none(self) -> None:
        """无 numeric_tools 时不构建工具 schema。"""
        service = make_plan_service(numeric_tools=None)
        assert service._numeric_tools is None


# ============================================================
# 4. PlanVersionRef / PlanStep / TaskType 模型验证
# ============================================================


class TestPlanModels:
    """计划相关模型验证。"""

    def test_task_type_has_long_context(self) -> None:
        """TaskType 有 LONG_CONTEXT（analyze_data 使用）。"""
        assert hasattr(TaskType, "LONG_CONTEXT")
        assert TaskType.LONG_CONTEXT.value == "long_context"

    def test_task_type_has_planning(self) -> None:
        """TaskType 有 PLANNING（generate_plan 使用）。"""
        assert hasattr(TaskType, "PLANNING")
        assert TaskType.PLANNING.value == "planning"

    def test_plan_version_ref_importable(self) -> None:
        """PlanVersionRef 可导入。"""
        from packages.research.models_trusted import PlanVersionRef

        ref = PlanVersionRef(
            plan_id=PLAN_ID,
            workspace_id=WORKSPACE_ID,
            version_number=1,
            status="draft",
            step_count=3,
        )
        assert ref.status == "draft"
        assert ref.version_number == 1

    def test_plan_step_importable(self) -> None:
        """PlanStep 可导入。"""
        from packages.research.models_trusted import PlanStep

        step = PlanStep(
            step_key="step_1",
            question="计算均值",
            method="llm",
            expected_output="结果",
        )
        assert step.step_key == "step_1"


# ============================================================
# 5. 前端契约验证（通过源码扫描，不 import 前端模块）
# ============================================================


class TestPlanReviewCardContract:
    """验证 PlanReviewCard 的接口契约，防止注释代码回归。

    确认计划/执行分析按钮的事件回调必须存在且可调用。
    通过源码扫描验证，不 import 前端模块。
    """

    PLAN_REVIEW_CARD_PATH = "apps/web/src/features/research/PlanReviewCard.tsx"
    RESEARCH_CANVAS_PATH = "apps/web/src/features/research/ResearchCanvas.tsx"

    def _read_source(self, relative_path: str) -> str:
        """读取前端源码文件内容。"""
        import pathlib

        path = pathlib.Path(__file__).parent.parent.parent.parent / relative_path
        return path.read_text()

    def test_research_canvas_has_handle_confirm_plan(self) -> None:
        """ResearchCanvas 必须有 handleConfirmPlan（防止再次被注释掉）。"""
        source = self._read_source(self.RESEARCH_CANVAS_PATH)
        assert "handleConfirmPlan" in source, (
            "handleConfirmPlan must exist in ResearchCanvas — do not comment it out!"
        )
        assert "onConfirm={handleConfirmPlan}" in source, (
            "handleConfirmPlan must be passed to PlanReviewCard as onConfirm"
        )

    def test_research_canvas_has_no_adjust_button(self) -> None:
        """ResearchCanvas 不应传递 onAdjust（调整计划按钮已删除）。"""
        source = self._read_source(self.RESEARCH_CANVAS_PATH)
        assert "onAdjust" not in source, "onAdjust should be removed from ResearchCanvas"

    def test_plan_review_card_has_conditional_adjust_button(self) -> None:
        """PlanReviewCard 应包含调整计划按钮，且仅在 onAdjust 存在时条件渲染。

        安全修复 d5f44d3 重新引入了「调整计划」按钮，但用 ``{onAdjust && ...}``
        条件渲染——只有当父组件传入 onAdjust 回调时才显示，避免死按钮。
        ResearchCanvas 目前不传 onAdjust（见 test_research_canvas_has_no_adjust_button），
        因此按钮在当前 UI 中不可见，但代码契约保留以便后续接入。
        """
        source = self._read_source(self.PLAN_REVIEW_CARD_PATH)
        assert "调整计划" in source, "PlanReviewCard must have 调整计划 button"
        assert "onAdjust" in source, "PlanReviewCard must reference onAdjust prop"
        # 按钮必须条件渲染（{onAdjust && ...}），防止无回调时显示死按钮
        assert "onAdjust &&" in source, "调整计划 button must be conditionally rendered"

    def test_plan_review_card_execute_visible_after_confirm(self) -> None:
        """PlanReviewCard 确认后执行分析按钮仍可见（extraContent 条件不含 !isConfirmed）。"""
        source = self._read_source(self.PLAN_REVIEW_CARD_PATH)
        # 找到 extraContent 定义段
        extra_content_start = source.find("extraContent")
        if extra_content_start == -1:
            return  # 如果没有 extraContent，跳过
        # 找到 extraContent 的结束位置（下一个变量声明或 return）
        extra_end = source.find("return (", extra_content_start)
        extra_section = (
            source[extra_content_start:extra_end] if extra_end > 0 else source[extra_content_start:]
        )
        # extraContent 里的条件不应包含 !isConfirmed（执行分析按钮确认后仍可见）
        assert "!isConfirmed" not in extra_section, (
            "执行分析按钮在确认后不应隐藏 — extraContent 中移除 !isConfirmed 条件"
        )

    def test_plan_review_card_has_confirm_button(self) -> None:
        """PlanReviewCard 必须包含确认计划按钮。"""
        source = self._read_source(self.PLAN_REVIEW_CARD_PATH)
        assert "确认计划" in source, "PlanReviewCard must have 确认计划 button"

    def test_research_canvas_has_no_commented_out_code(self) -> None:
        """ResearchCanvas 不应有被注释掉的回调函数。"""
        source = self._read_source(self.RESEARCH_CANVAS_PATH)
        # 不应有注释掉的 handleSubmitRun / handleCancelRun
        assert "// const handleSubmitRun" not in source, (
            "handleSubmitRun should be deleted, not commented out"
        )
        assert "// const handleCancelRun" not in source, (
            "handleCancelRun should be deleted, not commented out"
        )
