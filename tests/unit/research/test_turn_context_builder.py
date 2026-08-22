"""Tests for TurnContextBuilder: explicit selection, no leakage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
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


# ============================================================
# TurnContextBuilder.build / build_conclusion_inputs — mock session tests
# ============================================================


def _turn_sn(**overrides: object) -> SimpleNamespace:
    """Create a turn SimpleNamespace."""
    defaults: dict[str, object] = {
        "id": uuid4(),
        "question_text_snapshot": "哪些批次收率偏低?",
        "question_origin": "manual",
        "evidence_snapshot_id": uuid4(),
        "prompt_template_version": "v1",
        "output_schema_version": "v1",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestBuild:
    """TurnContextBuilder.build — assembles FixedTurnContext from DB."""

    async def test_turn_not_found_raises(self) -> None:
        session = MagicMock()
        session.get = AsyncMock(return_value=None)

        with pytest.raises(AppError) as exc_info:
            await TurnContextBuilder.build(session, uuid4())
        assert exc_info.value.code == "not_found"

    async def test_build_no_context_rows(self) -> None:
        session = MagicMock()
        turn = _turn_sn()
        session.get = AsyncMock(return_value=turn)

        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = []
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)

        ctx = await TurnContextBuilder.build(session, turn.id)
        assert ctx.turn_id == turn.id
        assert ctx.question_text == "哪些批次收率偏低?"
        assert ctx.question_origin == "manual"

    async def test_build_with_context_rows(self) -> None:
        session = MagicMock()
        turn = _turn_sn()
        session.get = AsyncMock(return_value=turn)

        rev_id = uuid4()
        concl_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=turn.id,
            conclusion_revision_id=rev_id,
            position=0,
        )
        revision = SimpleNamespace(
            id=rev_id,
            conclusion_id=concl_id,
            statement="结论陈述",
            scope="scope1",
            limitations="limit1",
        )
        conclusion = SimpleNamespace(
            id=concl_id,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=uuid4(),
            source_run_id=uuid4(),
        )

        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx_row]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)

        # session.get called for turn, then revision, then conclusion
        session.get = AsyncMock(side_effect=[turn, revision, conclusion])

        ctx = await TurnContextBuilder.build(session, turn.id)
        assert ctx.turn_id == turn.id

    async def test_build_revision_missing_skips(self) -> None:
        session = MagicMock()
        turn = _turn_sn()

        rev_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=turn.id,
            conclusion_revision_id=rev_id,
            position=0,
        )

        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx_row]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)

        # session.get: turn found, revision None
        session.get = AsyncMock(side_effect=[turn, None])

        ctx = await TurnContextBuilder.build(session, turn.id)
        assert ctx.turn_id == turn.id
        # No conclusions built since revision was missing

    async def test_build_conclusion_missing_skips(self) -> None:
        session = MagicMock()
        turn = _turn_sn()

        rev_id = uuid4()
        concl_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=turn.id,
            conclusion_revision_id=rev_id,
            position=0,
        )
        revision = SimpleNamespace(
            id=rev_id,
            conclusion_id=concl_id,
            statement="结论陈述",
            scope="scope1",
            limitations="limit1",
        )

        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx_row]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)

        # session.get: turn found, revision found, conclusion None
        session.get = AsyncMock(side_effect=[turn, revision, None])

        ctx = await TurnContextBuilder.build(session, turn.id)
        assert ctx.turn_id == turn.id


class TestBuildConclusionInputs:
    """TurnContextBuilder.build_conclusion_inputs — loads only conclusion inputs."""

    async def test_no_context_rows(self) -> None:
        session = MagicMock()
        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = []
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)

        result = await TurnContextBuilder.build_conclusion_inputs(session, uuid4())
        assert result == []

    async def test_with_context_rows(self) -> None:
        session = MagicMock()
        rev_id = uuid4()
        concl_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=uuid4(),
            conclusion_revision_id=rev_id,
            position=0,
        )
        revision = SimpleNamespace(
            id=rev_id,
            conclusion_id=concl_id,
            statement="结论A",
            scope="scope",
            limitations=None,
        )
        conclusion = SimpleNamespace(
            id=concl_id,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=uuid4(),
            source_run_id=uuid4(),
        )

        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx_row]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)
        session.get = AsyncMock(side_effect=[revision, conclusion])

        result = await TurnContextBuilder.build_conclusion_inputs(session, uuid4())
        assert len(result) == 1
        assert result[0].statement == "结论A"
        assert result[0].source_type == "ai_original"

    async def test_revision_missing_skips(self) -> None:
        session = MagicMock()
        ctx_row = SimpleNamespace(
            turn_id=uuid4(),
            conclusion_revision_id=uuid4(),
            position=0,
        )
        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx_row]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)
        session.get = AsyncMock(return_value=None)

        result = await TurnContextBuilder.build_conclusion_inputs(session, uuid4())
        assert result == []

    async def test_conclusion_missing_skips(self) -> None:
        session = MagicMock()
        rev_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=uuid4(),
            conclusion_revision_id=rev_id,
            position=0,
        )
        revision = SimpleNamespace(
            id=rev_id,
            conclusion_id=uuid4(),
            statement="结论",
            scope=None,
            limitations=None,
        )
        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx_row]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)
        session.get = AsyncMock(side_effect=[revision, None])

        result = await TurnContextBuilder.build_conclusion_inputs(session, uuid4())
        assert result == []

    async def test_multiple_context_rows_ordered(self) -> None:
        session = MagicMock()
        rev1_id = uuid4()
        rev2_id = uuid4()
        concl1_id = uuid4()
        concl2_id = uuid4()
        ctx1 = SimpleNamespace(turn_id=uuid4(), conclusion_revision_id=rev1_id, position=0)
        ctx2 = SimpleNamespace(turn_id=uuid4(), conclusion_revision_id=rev2_id, position=1)

        rev1 = SimpleNamespace(
            id=rev1_id, conclusion_id=concl1_id, statement="结论1", scope=None, limitations=None
        )
        rev2 = SimpleNamespace(
            id=rev2_id, conclusion_id=concl2_id, statement="结论2", scope=None, limitations=None
        )
        concl1 = SimpleNamespace(
            id=concl1_id,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=None,
            source_run_id=None,
        )
        concl2 = SimpleNamespace(
            id=concl2_id,
            source_type="manual",
            evidence_status="manual_unverified",
            source_turn_id=None,
            source_run_id=None,
        )

        ctx_result = MagicMock()
        ctx_scalars = MagicMock()
        ctx_scalars.all.return_value = [ctx1, ctx2]
        ctx_result.scalars.return_value = ctx_scalars
        session.execute = AsyncMock(return_value=ctx_result)
        session.get = AsyncMock(side_effect=[rev1, concl1, rev2, concl2])

        result = await TurnContextBuilder.build_conclusion_inputs(session, uuid4())
        assert len(result) == 2
        assert result[0].statement == "结论1"
        assert result[1].statement == "结论2"
        assert result[1].evidence_status == "manual_unverified"
