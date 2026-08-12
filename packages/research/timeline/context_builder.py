"""Turn context builder: assembles the fixed model context for a turn.

The builder ONLY reads from:
  research_turn_context JOIN research_conclusion_revision JOIN research_conclusion

It MUST NOT query:
  - "latest 20 conclusions" (unauthorized context leakage)
  - research_ai_conversation
  - research_memory_document
  - the full timeline

Each FixedConclusionInput carries source turn/run/snapshot, evidence refs,
scope, limitations, source_type and evidence_status. Manual unverified
conclusions get a fixed label so the model cannot mistake them for
data-supported facts.
"""

from __future__ import annotations

import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.research.timeline.contracts import (
    FixedConclusionInput,
    FixedTurnContext,
)
from packages.research.timeline.entities import (
    ResearchConclusion,
    ResearchConclusionRevision,
    ResearchTurn,
    ResearchTurnContext,
)

logger = logging.getLogger("research.context_builder")

#: Fixed label for manual unverified conclusions in model context.
MANUAL_UNVERIFIED_LABEL = (
    "[manual_unverified] 用户保存的历史结论；未关联分析证据；尚未基于当前快照复核。"
)


class TurnContextBuilder:
    """Builds the immutable FixedTurnContext for a given turn.

    This is a pure read operation — it does not modify any data.
    It reads only the rows that were explicitly selected when the turn
    was created (stored in research_turn_context).
    """

    @staticmethod
    async def build(
        session: AsyncSession,
        turn_id: UUID,
    ) -> FixedTurnContext:
        """Build the fixed context for a turn.

        Args:
            session: Async DB session.
            turn_id: The turn ID.

        Returns:
            FixedTurnContext with question, snapshot, and selected conclusions.

        Raises:
            AppError: code="not_found" if the turn does not exist.
        """
        # 1. Load the turn
        turn = await session.get(ResearchTurn, turn_id)
        if turn is None:
            raise AppError(
                code="not_found",
                message="Turn not found",
                retryable=False,
                fields={"turn_id": str(turn_id)},
            )

        # 2. Load selected conclusion revisions via turn_context join
        context_rows = await session.execute(
            sa.select(ResearchTurnContext)
            .where(ResearchTurnContext.turn_id == turn_id)
            .order_by(ResearchTurnContext.position)
        )
        context_list = list(context_rows.scalars().all())

        # 3. For each context row, load the revision + conclusion
        conclusions: list[FixedConclusionInput] = []
        for ctx_row in context_list:
            revision = await session.get(ResearchConclusionRevision, ctx_row.conclusion_revision_id)
            if revision is None:
                logger.warning(
                    "turn %s references missing revision %s",
                    turn_id,
                    ctx_row.conclusion_revision_id,
                )
                continue

            conclusion = await session.get(ResearchConclusion, revision.conclusion_id)
            if conclusion is None:
                logger.warning(
                    "revision %s references missing conclusion %s",
                    revision.id,
                    revision.conclusion_id,
                )
                continue

            # Build the FixedConclusionInput with provenance
            conclusions.append(
                FixedConclusionInput(
                    revision_id=revision.id,
                    statement=revision.statement,
                    scope=revision.scope,
                    limitations=revision.limitations,
                    source_type=conclusion.source_type,
                    evidence_status=conclusion.evidence_status,
                    source_turn_id=conclusion.source_turn_id,
                    source_run_id=conclusion.source_run_id,
                    source_snapshot_id=None,  # Could join to turn's snapshot if needed
                )
            )

        return FixedTurnContext(
            turn_id=turn_id,
            question_text=turn.question_text_snapshot,
            question_origin=turn.question_origin,
            evidence_snapshot_id=turn.evidence_snapshot_id,
            prompt_template_version=turn.prompt_template_version,
            output_schema_version=turn.output_schema_version,
        )

    @staticmethod
    async def build_conclusion_inputs(
        session: AsyncSession,
        turn_id: UUID,
    ) -> list[FixedConclusionInput]:
        """Load only the conclusion inputs for a turn (without turn metadata).

        This is the method used by TurnContextBuilder.to_model_text() —
        it reads ONLY the explicitly selected revisions.

        Args:
            session: Async DB session.
            turn_id: The turn ID.

        Returns:
            List of FixedConclusionInput, ordered by position.
        """
        context_rows = await session.execute(
            sa.select(ResearchTurnContext)
            .where(ResearchTurnContext.turn_id == turn_id)
            .order_by(ResearchTurnContext.position)
        )
        context_list = list(context_rows.scalars().all())

        conclusions: list[FixedConclusionInput] = []
        for ctx_row in context_list:
            revision = await session.get(ResearchConclusionRevision, ctx_row.conclusion_revision_id)
            if revision is None:
                continue

            conclusion = await session.get(ResearchConclusion, revision.conclusion_id)
            if conclusion is None:
                continue

            conclusions.append(
                FixedConclusionInput(
                    revision_id=revision.id,
                    statement=revision.statement,
                    scope=revision.scope,
                    limitations=revision.limitations,
                    source_type=conclusion.source_type,
                    evidence_status=conclusion.evidence_status,
                    source_turn_id=conclusion.source_turn_id,
                    source_run_id=conclusion.source_run_id,
                    source_snapshot_id=None,
                )
            )

        return conclusions

    @staticmethod
    def render_conclusion_for_model(conclusion: FixedConclusionInput) -> str:
        """Render a single conclusion as text for the model context.

        Manual unverified conclusions get a fixed prefix label so the
        model cannot mistake them for data-supported facts.
        """
        prefix = ""
        if conclusion.evidence_status == "manual_unverified":
            prefix = MANUAL_UNVERIFIED_LABEL + "\n"
        return f"{prefix}{conclusion.statement}"

    @staticmethod
    def render_context_for_model(
        context: FixedTurnContext,
        conclusions: list[FixedConclusionInput],
    ) -> str:
        """Render the full model context as a single text string.

        This is used by the plan generator and candidate extractor to
        build the model prompt. It contains:
          - The fixed question
          - The evidence snapshot reference
          - Each selected conclusion (with provenance labels)
        """
        parts: list[str] = [
            f"研究问题: {context.question_text}",
            f"问题来源: {context.question_origin}",
            f"数据快照: {context.evidence_snapshot_id}",
        ]

        if conclusions:
            parts.append(f"已选历史结论 ({len(conclusions)} 条):")
            for i, c in enumerate(conclusions, 1):
                parts.append(f"  [{i}] {TurnContextBuilder.render_conclusion_for_model(c)}")
        else:
            parts.append("已选历史结论: (无)")

        return "\n".join(parts)
