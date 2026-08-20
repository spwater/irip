"""Research Timeline contracts: commands, refs, pages and AI structured output schemas.

All commands are frozen dataclasses with __post_init__ validation.
All refs are frozen dataclasses for safe inter-service passing.
AI schemas use Pydantic v2 for strict structured-output validation.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# ============================================================
# AI Structured Output Schemas (§3.4)
# ============================================================

MAX_CONCLUSION_REVISIONS = 20
MAX_SYNTHESIS_REVISIONS = 20
MIN_SYNTHESIS_REVISIONS = 2


class RecommendedQuestion(BaseModel):
    """A single AI-recommended research question."""

    question: str = Field(min_length=3, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_hints: list[str] = Field(default_factory=list, max_length=10)


class RecommendationOutput(BaseModel):
    """Structured output from the recommendation model.

    min_length=1 / max_length=4 enforces the 1-4 question range.
    """

    questions: list[RecommendedQuestion] = Field(min_length=1, max_length=4)


class SynthesisSection(BaseModel):
    """One section of a synthesis result.

    ``status="present"`` requires at least one item.
    ``status="not_applicable"`` requires empty items (no placeholder noise).
    """

    status: Literal["present", "not_applicable"]
    items: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_items(self) -> SynthesisSection:
        if self.status == "present" and not self.items:
            raise ValueError("present section requires at least one item")
        if self.status == "not_applicable" and self.items:
            raise ValueError("not_applicable section requires empty items")
        return self


class SynthesisResult(BaseModel):
    """Structured output from the synthesis model.

    ``summary`` is always required.  The other four sections may be
    ``not_applicable`` if there is genuinely no content.
    """

    summary: str = Field(min_length=1, max_length=12000)
    agreements: SynthesisSection
    conflicts: SynthesisSection
    limitations: SynthesisSection
    new_hypotheses: SynthesisSection


# ============================================================
# Commands (frozen dataclasses with validation)
# ============================================================


def _normalize_question(text: str) -> str:
    """NFKC normalize and strip question text."""
    return unicodedata.normalize("NFKC", text).strip()


@dataclass(frozen=True)
class CreateTurnCommand:
    """Command to create an analysis research turn.

    ``question_text`` is stripped and NFKC-normalized.
    ``selected_conclusion_revision_ids`` must have 0-20 unique IDs.
    ``idempotency_key`` must be 1-128 chars.
    """

    workspace_id: UUID
    question_text: str
    evidence_snapshot_id: UUID
    selected_conclusion_revision_ids: tuple[UUID, ...]
    recommendation_item_id: UUID | None
    idempotency_key: str

    def __post_init__(self) -> None:
        text = _normalize_question(self.question_text)
        object.__setattr__(self, "question_text", text)
        if not text:
            raise ValueError("question_text must not be empty after strip")
        if len(self.selected_conclusion_revision_ids) > MAX_CONCLUSION_REVISIONS:
            raise ValueError(
                f"selected_conclusion_revision_ids must have at most "
                f"{MAX_CONCLUSION_REVISIONS} items"
            )
        if len(set(self.selected_conclusion_revision_ids)) != len(
            self.selected_conclusion_revision_ids
        ):
            raise ValueError("selected_conclusion_revision_ids must be unique")
        if not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1-128 characters")


@dataclass(frozen=True)
class CreateSynthesisTurnCommand:
    """Command to create a synthesis research turn.

    ``selected_conclusion_revision_ids`` must have 2-20 unique IDs.
    """

    workspace_id: UUID
    evidence_snapshot_id: UUID
    selected_conclusion_revision_ids: tuple[UUID, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        count = len(self.selected_conclusion_revision_ids)
        if count < MIN_SYNTHESIS_REVISIONS or count > MAX_SYNTHESIS_REVISIONS:
            raise ValueError(
                f"selected_conclusion_revision_ids must have "
                f"{MIN_SYNTHESIS_REVISIONS}-{MAX_SYNTHESIS_REVISIONS} items, got {count}"
            )
        if len(set(self.selected_conclusion_revision_ids)) != count:
            raise ValueError("selected_conclusion_revision_ids must be unique")
        if not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1-128 characters")


@dataclass(frozen=True)
class SaveCandidatesCommand:
    """Command to save selected candidates as conclusions."""

    workspace_id: UUID
    turn_id: UUID
    selections: tuple[CandidateSelection, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.selections:
            raise ValueError("selections must not be empty")
        if len(self.selections) > 20:
            raise ValueError("selections must have at most 20 items")
        if not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1-128 characters")


@dataclass(frozen=True)
class CandidateSelection:
    """One candidate selection for saving."""

    candidate_id: UUID
    edited_statement: str | None = None
    edited_scope: str | None = None
    edited_limitations: str | None = None


@dataclass(frozen=True)
class CreateManualConclusionCommand:
    """Command to create a manual (no-evidence) conclusion."""

    workspace_id: UUID
    statement: str
    idempotency_key: str
    scope: str | None = None
    limitations: str | None = None

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("statement must not be empty")
        if not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1-128 characters")


@dataclass(frozen=True)
class ReviseConclusionCommand:
    """Command to revise an existing conclusion (creates new revision)."""

    workspace_id: UUID
    conclusion_id: UUID
    statement: str
    expected_lock_version: int
    scope: str | None = None
    limitations: str | None = None

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("statement must not be empty")


MAX_BAR_ITEM_IDS = 20
VALID_BAR_BLOCK_TYPES = ("echarts", "chart_ref", "structured", "table", "text")


@dataclass(frozen=True)
class PushBarItemCommand:
    """Command to push a report block snapshot to the conclusion bar.

    ``content_snapshot`` is locked at push time (full ECharts option for
    chart blocks, parsed JSON for structured, ``{columns, rows}`` for table,
    raw text for text).  ``source_info`` carries provenance:
    ``{turn_number, snapshot_number, question_text, block_index}``.
    """

    workspace_id: UUID
    turn_id: UUID
    block_type: str
    title: str
    content_snapshot: dict[str, Any]
    source_info: dict[str, Any]

    def __post_init__(self) -> None:
        if self.block_type not in VALID_BAR_BLOCK_TYPES:
            raise ValueError(
                f"block_type must be one of {VALID_BAR_BLOCK_TYPES}, got {self.block_type!r}"
            )
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if not isinstance(self.content_snapshot, dict):
            raise ValueError("content_snapshot must be a dict")
        if not isinstance(self.source_info, dict):
            raise ValueError("source_info must be a dict")


@dataclass(frozen=True)
class AssembleFinalConclusionCommand:
    """Command to assemble checked bar items into a final conclusion.

    ``item_ids`` must have 1-20 unique IDs.  ``idempotency_key`` must be
    1-128 chars.
    """

    workspace_id: UUID
    item_ids: tuple[UUID, ...]
    title: str
    idempotency_key: str

    def __post_init__(self) -> None:
        count = len(self.item_ids)
        if count < 1 or count > MAX_BAR_ITEM_IDS:
            raise ValueError(f"item_ids must have 1-{MAX_BAR_ITEM_IDS} items, got {count}")
        if len(set(self.item_ids)) != count:
            raise ValueError("item_ids must be unique")
        if not self.idempotency_key or len(self.idempotency_key) > 128:
            raise ValueError("idempotency_key must be 1-128 characters")


# ============================================================
# Refs (frozen dataclasses for inter-service passing)
# ============================================================


@dataclass(frozen=True)
class TurnRef:
    """Reference to a research turn."""

    turn_id: UUID
    workspace_id: UUID
    turn_number: int
    kind: str
    status: str
    question_text: str
    question_origin: str
    evidence_snapshot_id: UUID


@dataclass(frozen=True)
class PlanVersionRef:
    """Reference to a plan version."""

    plan_id: UUID
    turn_id: UUID
    version_number: int
    status: str


@dataclass(frozen=True)
class RecommendationBatchRef:
    """Reference to a recommendation batch."""

    batch_id: UUID
    workspace_id: UUID
    status: str
    item_count: int


@dataclass(frozen=True)
class CandidateExtractionRef:
    """Reference to a candidate extraction job."""

    extraction_id: UUID
    turn_id: UUID
    run_id: UUID
    status: str


@dataclass(frozen=True)
class ConclusionRef:
    """Reference to a saved conclusion."""

    conclusion_id: UUID
    workspace_id: UUID
    source_type: str
    evidence_status: str
    status: str
    revision_number: int
    statement: str
    current_revision_id: UUID | None = None


@dataclass(frozen=True)
class BarItemRef:
    """Reference to a conclusion-bar item.

    All ID/datetime fields are stringified for API serialisation.
    """

    id: str
    workspace_id: str
    turn_id: str
    block_type: str
    title: str
    content_snapshot: dict[str, Any]
    source_info: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON API responses."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "turn_id": self.turn_id,
            "block_type": self.block_type,
            "title": self.title,
            "content_snapshot": self.content_snapshot,
            "source_info": self.source_info,
            "created_at": self.created_at,
        }


# ============================================================
# Timeline page (cursor pagination)
# ============================================================


@dataclass(frozen=True)
class TimelineTurnCard:
    """Summary card for one turn in the timeline list."""

    turn_id: UUID
    turn_number: int
    kind: str
    status: str
    question_text: str
    question_origin: str
    snapshot_number: int
    selected_conclusion_count: int
    created_at: datetime
    has_result: bool
    has_candidates: bool


@dataclass(frozen=True)
class TimelinePage:
    """One page of timeline turns with cursor pagination."""

    items: list[TimelineTurnCard]
    next_cursor: str | None
    active_run_status: str | None


# ============================================================
# Turn detail (for recovery and expanded view)
# ============================================================


@dataclass(frozen=True)
class FixedTurnContext:
    """Immutable context fixed when planning starts."""

    turn_id: UUID
    question_text: str
    question_origin: str
    evidence_snapshot_id: UUID
    prompt_template_version: str | None
    output_schema_version: str | None


@dataclass(frozen=True)
class FixedConclusionInput:
    """One conclusion revision selected as turn context."""

    revision_id: UUID
    statement: str
    scope: str | None
    limitations: str | None
    source_type: str
    evidence_status: str
    source_turn_id: UUID | None
    source_run_id: UUID | None
    source_snapshot_id: UUID | None

    def to_model_text(self) -> str:
        """Render this conclusion as text for the model context."""
        prefix = ""
        if self.evidence_status == "manual_unverified":
            prefix = (
                "[manual_unverified] 用户保存的历史结论；未关联分析证据；尚未基于当前快照复核。\n"
            )
        return f"{prefix}{self.statement}"


@dataclass(frozen=True)
class TurnDetail:
    """Full detail of a turn for recovery and expanded view."""

    turn: TurnRef
    context: FixedTurnContext | None
    selected_conclusions: list[FixedConclusionInput]
    plan: PlanVersionRef | None
    run_status: str | None
    result: dict[str, Any] | None
    extraction_status: str | None
    candidates: list[dict[str, Any]]
    saved_conclusions: list[ConclusionRef]
    access_restricted: bool = False


# ============================================================
# Prompt / schema version constants
# ============================================================

RECOMMENDATION_PROMPT_VERSION = "research-recommendation-v1"
RECOMMENDATION_OUTPUT_SCHEMA_VERSION = "recommendation-output-v1"
SYNTHESIS_PROMPT_VERSION = "research-synthesis-v1"
SYNTHESIS_OUTPUT_SCHEMA_VERSION = "synthesis-result-v1"
CANDIDATE_EXTRACTION_PROMPT_VERSION = "research-candidate-extraction-v1"
CANDIDATE_EXTRACTION_SCHEMA_VERSION = "candidate-extraction-output-v1"
