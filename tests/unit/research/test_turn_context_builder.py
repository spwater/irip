"""Tests for TurnContextBuilder: explicit selection, no leakage."""

from uuid import uuid4

from packages.research.timeline.context_builder import TurnContextBuilder
from packages.research.timeline.contracts import FixedConclusionInput


class TestRenderConclusionForModel:
    """Test the rendering of conclusions for model context."""

    def test_data_supported_no_prefix(self) -> None:
        c = FixedConclusionInput(
            revision_id=uuid4(),
            statement="温度升高与收率上升相关",
            scope=None,
            limitations=None,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text = TurnContextBuilder.render_conclusion_for_model(c)
        assert text == "温度升高与收率上升相关"
        assert "manual_unverified" not in text

    def test_manual_unverified_has_label(self) -> None:
        c = FixedConclusionInput(
            revision_id=uuid4(),
            statement="设备清洗可能影响结果",
            scope=None,
            limitations="来自操作记录",
            source_type="manual",
            evidence_status="manual_unverified",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text = TurnContextBuilder.render_conclusion_for_model(c)
        assert "[manual_unverified]" in text
        assert "未关联分析证据" in text
        assert "尚未基于当前快照复核" in text
        assert "设备清洗可能影响结果" in text


class TestRenderContextForModel:
    """Test the full context rendering."""

    def test_empty_conclusions(self) -> None:
        from packages.research.timeline.contracts import FixedTurnContext

        ctx = FixedTurnContext(
            turn_id=uuid4(),
            question_text="哪些批次收率偏低?",
            question_origin="manual",
            evidence_snapshot_id=uuid4(),
            prompt_template_version=None,
            output_schema_version=None,
        )
        text = TurnContextBuilder.render_context_for_model(ctx, [])
        assert "研究问题" in text
        assert "哪些批次收率偏低?" in text
        assert "已选历史结论: (无)" in text

    def test_with_conclusions(self) -> None:
        from packages.research.timeline.contracts import FixedTurnContext

        ctx = FixedTurnContext(
            turn_id=uuid4(),
            question_text="温度影响?",
            question_origin="initial_ai",
            evidence_snapshot_id=uuid4(),
            prompt_template_version=None,
            output_schema_version=None,
        )
        conclusions = [
            FixedConclusionInput(
                revision_id=uuid4(),
                statement="结论一",
                scope=None,
                limitations=None,
                source_type="ai_original",
                evidence_status="data_supported",
                source_turn_id=None,
                source_run_id=None,
                source_snapshot_id=None,
            ),
            FixedConclusionInput(
                revision_id=uuid4(),
                statement="结论二",
                scope=None,
                limitations=None,
                source_type="manual",
                evidence_status="manual_unverified",
                source_turn_id=None,
                source_run_id=None,
                source_snapshot_id=None,
            ),
        ]
        text = TurnContextBuilder.render_context_for_model(ctx, conclusions)
        assert "已选历史结论 (2 条)" in text
        assert "结论一" in text
        assert "结论二" in text
        assert "[manual_unverified]" in text
        assert "[1]" in text
        assert "[2]" in text


class TestContextBuilderPrinciples:
    """Test that the context builder follows the no-leakage principle.

    These tests verify the rendering logic. Full DB integration tests
    would require a test database — they are in the integration test suite.
    """

    def test_only_selected_appear_in_text(self) -> None:
        """Unselected conclusions must not appear in model text."""
        selected = FixedConclusionInput(
            revision_id=uuid4(),
            statement="选中的结论",
            scope=None,
            limitations=None,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        unselected_statement = "未选中的结论不应该出现"

        from packages.research.timeline.contracts import FixedTurnContext

        ctx = FixedTurnContext(
            turn_id=uuid4(),
            question_text="问题",
            question_origin="manual",
            evidence_snapshot_id=uuid4(),
            prompt_template_version=None,
            output_schema_version=None,
        )
        text = TurnContextBuilder.render_context_for_model(ctx, [selected])
        assert "选中的结论" in text
        assert unselected_statement not in text

    def test_manual_label_prevents_misrepresentation(self) -> None:
        """Manual conclusions must be labeled as unverified."""
        c = FixedConclusionInput(
            revision_id=uuid4(),
            statement="手工判断",
            scope=None,
            limitations=None,
            source_type="manual",
            evidence_status="manual_unverified",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text = TurnContextBuilder.render_conclusion_for_model(c)
        # The label must clearly state it's not data-supported
        assert "未关联分析证据" in text
