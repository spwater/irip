"""Research Timeline ORM entities (9 new tables).

All entities inherit from Base and share the same metadata as existing
research domain entities.  Importing this module ensures the timeline
tables are registered on ``Base.metadata``.

Tables:
  - ResearchRecommendationBatch
  - ResearchRecommendationItem
  - ResearchTurn
  - ResearchTurnContext
  - ResearchTurnResult
  - CandidateExtractionJob
  - ResearchConclusionCandidate
  - ResearchConclusion
  - ResearchConclusionRevision
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import packages.auth.entities  # noqa: F401
import packages.research.entities  # noqa: F401 — ensures research_workspace/snapshot registered
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class ResearchRecommendationBatch(Base):
    """One recommendation request/result (initial or followup).

    Contains 1-4 non-duplicate questions.  Status: queued → running → succeeded | failed.
    """

    __tablename__ = "research_recommendation_batch"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_workspace.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_evidence_snapshot.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(sa.Text, nullable=False)  # initial | followup
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'queued'"))
    prompt_template_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    output_schema_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchRecommendationBatch(id={self.id!r}, mode={self.mode!r}, "
            f"status={self.status!r})"
        )


class ResearchRecommendationItem(Base):
    """A single recommended question within a batch."""

    __tablename__ = "research_recommendation_item"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    batch_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_recommendation_batch.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rationale: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_hints: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"ResearchRecommendationItem(id={self.id!r}, position={self.position!r})"


class ResearchTurn(Base):
    """One round of research work (analysis or synthesis).

    Status: question_draft → planning → plan_review → plan_confirmed
    → queued → running → succeeded → conclusion_reviewed
    (plus: planning_failed, run_failed, cancelled, succeeded_without_saved_conclusion)
    """

    __tablename__ = "research_turn"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_workspace.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)  # analysis | synthesis
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'question_draft'")
    )
    question_text_snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    question_origin: Mapped[str] = mapped_column(
        sa.Text, nullable=False
    )  # initial_ai | followup_ai | ai_edited | manual | synthesis
    recommendation_item_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    evidence_snapshot_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_evidence_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_template_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    output_schema_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchTurn(id={self.id!r}, turn_number={self.turn_number!r}, "
            f"status={self.status!r})"
        )


class ResearchTurnContext(Base):
    """Explicit conclusion revision selections for a turn (max 20).

    Composite PK (turn_id, conclusion_revision_id) prevents duplicates.
    """

    __tablename__ = "research_turn_context"

    turn_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_turn.id", ondelete="CASCADE"), primary_key=True
    )
    conclusion_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_conclusion_revision.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchTurnContext(turn_id={self.turn_id!r}, "
            f"revision_id={self.conclusion_revision_id!r})"
        )


class ResearchTurnResult(Base):
    """Immutable result of a completed run (one per turn, one per run)."""

    __tablename__ = "research_turn_result"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    turn_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_turn.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    result_kind: Mapped[str] = mapped_column(
        sa.Text, nullable=False
    )  # analysis | synthesis | partial
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    structured_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    method_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    limitations: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchTurnResult(id={self.id!r}, turn_id={self.turn_id!r}, "
            f"result_kind={self.result_kind!r})"
        )


class CandidateExtractionJob(Base):
    """Async candidate extraction job (one per completed run).

    Status: queued → running → succeeded | failed | task_lost
    Survives page close; reconciler marks task_lost after 10 min no heartbeat.
    """

    __tablename__ = "research_candidate_extraction_job"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_workspace.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_turn.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'queued'"))
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"CandidateExtractionJob(id={self.id!r}, run_id={self.run_id!r}, "
            f"status={self.status!r})"
        )


class ResearchConclusionCandidate(Base):
    """AI-extracted candidate conclusion (not yet saved by user).

    Status: pending → saved | rejected
    Only saved candidates become ResearchConclusion.
    """

    __tablename__ = "research_conclusion_candidate"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    extraction_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_candidate_extraction_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_turn.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    statement: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    method_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    confidence_level: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'")
    )
    saved_conclusion_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchConclusionCandidate(id={self.id!r}, ordinal={self.ordinal!r}, "
            f"status={self.status!r})"
        )


class ResearchConclusion(Base):
    """User-saved logical conclusion with immutable revision history.

    source_type: ai_original | ai_edited | manual
    evidence_status: data_supported | manual_unverified
    status: active | archived
    """

    __tablename__ = "research_conclusion"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_workspace.id", ondelete="CASCADE"), nullable=False
    )
    current_revision_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    source_turn_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey(
            "research_turn.id", ondelete="SET NULL", deferrable=True, initially="DEFERRED"
        ),
        nullable=True,
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey(
            "research_analysis_run.id", ondelete="SET NULL", deferrable=True, initially="DEFERRED"
        ),
        nullable=True,
    )
    source_candidate_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    source_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False
    )  # ai_original | ai_edited | manual
    evidence_status: Mapped[str] = mapped_column(
        sa.Text, nullable=False
    )  # data_supported | manual_unverified
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'active'"))
    created_by: Mapped[UUID] = mapped_column(GUID, sa.ForeignKey("app_user.id"), nullable=False)
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchConclusion(id={self.id!r}, source_type={self.source_type!r}, "
            f"status={self.status!r})"
        )


class ResearchConclusionRevision(Base):
    """Immutable text version of a conclusion.

    Editing a conclusion creates a new revision; old revisions are never
    overwritten so that historical TurnContext references remain stable.
    """

    __tablename__ = "research_conclusion_revision"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    conclusion_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_conclusion.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    statement: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    limitations: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    editor: Mapped[UUID] = mapped_column(GUID, sa.ForeignKey("app_user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchConclusionRevision(id={self.id!r}, revision_number={self.revision_number!r})"
        )


class ResearchConclusionBarItem(Base):
    """A pushed report block aggregated under a workspace's conclusion bar.

    Each item is a data snapshot of a report block (echarts option, structured
    data, table, text) pushed by the user from a turn's analysis report.  The
    snapshot is locked at push time so it never depends on fact_samples again.
    Workspace is the aggregation dimension; ``turn_id`` is kept for provenance.
    """

    __tablename__ = "research_conclusion_bar_item"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_workspace.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_turn.id", ondelete="CASCADE"), nullable=False
    )
    block_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_info: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_by: Mapped[UUID] = mapped_column(GUID, sa.ForeignKey("app_user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchConclusionBarItem(id={self.id!r}, block_type={self.block_type!r}, "
            f"title={self.title!r})"
        )
