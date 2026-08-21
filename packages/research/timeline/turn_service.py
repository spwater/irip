"""Turn service: creates research turns with explicit conclusion selection.

Key invariants:
  - Turn number is atomically allocated via Workspace row lock.
  - Only explicitly selected conclusion revisions enter the turn context.
  - Idempotency key prevents duplicate turn creation.
  - question_origin is derived from recommendation_item_id and question
    text — clients cannot self-report it.
  - start_planning locks the turn inputs (question, snapshot, context).
  - After planning starts, inputs cannot be changed — a new Turn is required.
  - Cross-workspace/cross-user ID mismatches return not_found (fail-closed).
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.entities import ResearchEvidenceSnapshot
from packages.research.repository.workspace import WorkspaceRepository
from packages.research.timeline.contracts import (
    RECOMMENDATION_PROMPT_VERSION,
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
    PlanVersionRef,
    TurnRef,
)
from packages.research.timeline.entities import (
    ResearchConclusion,
    ResearchConclusionRevision,
)
from packages.research.timeline.repository import TimelineRepository
from packages.research.timeline.state_machine import TurnStateMachine

logger = logging.getLogger("research.turn_service")

#: Synthesis turn generates a fixed question text.
SYNTHESIS_QUESTION_TEMPLATE = "综合所选的 {n} 条结论，识别一致、冲突、限制并提出可检验的新假设"


class TurnService(ScopedSessionMixin):
    """Service for creating and managing research turns.

    Depends on session_factory, department_id, actor_id.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
    ) -> None:
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._rls_dept_id: UUID | None = None

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._factory

    def _require_actor(self) -> UUID:
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    async def create_analysis_turn(self, command: CreateTurnCommand) -> TurnRef:
        """Create an analysis research turn.

        Flow:
          1. Verify workspace ownership
          2. Verify snapshot belongs to workspace
          3. Verify selected conclusions belong to workspace
          4. Atomically allocate turn number
          5. Derive question_origin from recommendation_item_id + question text
          6. Insert turn (status=question_draft)
          7. Insert turn_context rows

        Args:
            command: CreateTurnCommand with question, snapshot, revisions.

        Returns:
            TurnRef for the created turn.

        Raises:
            AppError: not_found if workspace/snapshot/conclusions don't belong.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Verify workspace ownership
            workspace = await WorkspaceRepository.get_workspace(
                session, command.workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(command.workspace_id)},
                )

            # 2. Verify snapshot belongs to workspace
            snapshot = await session.get(ResearchEvidenceSnapshot, command.evidence_snapshot_id)
            if snapshot is None or snapshot.workspace_id != command.workspace_id:
                raise AppError(
                    code="not_found",
                    message="数据快照不存在或不属于此工作空间",
                    retryable=False,
                    fields={"snapshot_id": str(command.evidence_snapshot_id)},
                )

            # 3. Verify selected conclusions belong to workspace
            for revision_id in command.selected_conclusion_revision_ids:
                revision = await session.get(ResearchConclusionRevision, revision_id)
                if revision is None:
                    raise AppError(
                        code="not_found",
                        message="结论修订不存在",
                        retryable=False,
                        fields={"revision_id": str(revision_id)},
                    )
                conclusion = await session.get(ResearchConclusion, revision.conclusion_id)
                if conclusion is None or conclusion.workspace_id != command.workspace_id:
                    raise AppError(
                        code="not_found",
                        message="结论不属于此工作空间",
                        retryable=False,
                        fields={"revision_id": str(revision_id)},
                    )

            # 4. Check idempotency
            existing = await TimelineRepository.get_turn_by_idempotency(
                session, command.workspace_id, command.idempotency_key
            )
            if existing is not None:
                return TurnRef(
                    turn_id=existing.id,
                    workspace_id=existing.workspace_id,
                    turn_number=existing.turn_number,
                    kind=existing.kind,
                    status=existing.status,
                    question_text=existing.question_text_snapshot,
                    question_origin=existing.question_origin,
                    evidence_snapshot_id=existing.evidence_snapshot_id,
                )

            # 5. Allocate turn number atomically
            turn_number = await WorkspaceRepository.allocate_turn_number(
                session, command.workspace_id
            )

            # 6. Derive question_origin
            question_origin = self._derive_origin(
                command.recommendation_item_id,
                command.question_text,
            )

            # 7. Insert turn
            turn = await TimelineRepository.insert_turn(
                session,
                workspace_id=command.workspace_id,
                turn_number=turn_number,
                kind="analysis",
                status="question_draft",
                question_text=command.question_text,
                question_origin=question_origin,
                evidence_snapshot_id=command.evidence_snapshot_id,
                recommendation_item_id=command.recommendation_item_id,
                idempotency_key=command.idempotency_key,
            )

            # 8. Insert turn_context rows
            if command.selected_conclusion_revision_ids:
                context_pairs = [
                    (rid, i) for i, rid in enumerate(command.selected_conclusion_revision_ids)
                ]
                await TimelineRepository.insert_turn_context(
                    session,
                    turn_id=turn.id,
                    conclusion_revision_ids=context_pairs,
                )

            # 9. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.turn.create",
                    actor_user_id=actor_id,
                    resource_type="research_turn",
                    resource_id=turn.id,
                    payload={
                        "turn_number": turn_number,
                        "kind": "analysis",
                        "context_count": len(command.selected_conclusion_revision_ids),
                    },
                ),
            )

            return TurnRef(
                turn_id=turn.id,
                workspace_id=command.workspace_id,
                turn_number=turn_number,
                kind="analysis",
                status="question_draft",
                question_text=command.question_text,
                question_origin=question_origin,
                evidence_snapshot_id=command.evidence_snapshot_id,
            )

    async def create_synthesis_turn(self, command: CreateSynthesisTurnCommand) -> TurnRef:
        """Create a synthesis research turn.

        Generates a fixed question text from the template.
        Uses the latest snapshot if not provided.

        Args:
            command: CreateSynthesisTurnCommand with snapshot and 2-20 revisions.

        Returns:
            TurnRef for the created turn.

        Raises:
            AppError: not_found if workspace/snapshot/conclusions don't belong.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Verify workspace ownership
            workspace = await WorkspaceRepository.get_workspace(
                session, command.workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(command.workspace_id)},
                )

            # 2. Verify snapshot
            snapshot = await session.get(ResearchEvidenceSnapshot, command.evidence_snapshot_id)
            if snapshot is None or snapshot.workspace_id != command.workspace_id:
                raise AppError(
                    code="not_found",
                    message="数据快照不存在或不属于此工作空间",
                    retryable=False,
                    fields={"snapshot_id": str(command.evidence_snapshot_id)},
                )

            # 3. Verify selected conclusions
            for revision_id in command.selected_conclusion_revision_ids:
                revision = await session.get(ResearchConclusionRevision, revision_id)
                if revision is None:
                    raise AppError(
                        code="not_found",
                        message="结论修订不存在",
                        retryable=False,
                        fields={"revision_id": str(revision_id)},
                    )
                conclusion = await session.get(ResearchConclusion, revision.conclusion_id)
                if conclusion is None or conclusion.workspace_id != command.workspace_id:
                    raise AppError(
                        code="not_found",
                        message="结论不属于此工作空间",
                        retryable=False,
                        fields={"revision_id": str(revision_id)},
                    )

            # 4. Check idempotency
            existing = await TimelineRepository.get_turn_by_idempotency(
                session, command.workspace_id, command.idempotency_key
            )
            if existing is not None:
                return TurnRef(
                    turn_id=existing.id,
                    workspace_id=existing.workspace_id,
                    turn_number=existing.turn_number,
                    kind=existing.kind,
                    status=existing.status,
                    question_text=existing.question_text_snapshot,
                    question_origin=existing.question_origin,
                    evidence_snapshot_id=existing.evidence_snapshot_id,
                )

            # 5. Allocate turn number
            turn_number = await WorkspaceRepository.allocate_turn_number(
                session, command.workspace_id
            )

            # 6. Generate question text
            question_text = SYNTHESIS_QUESTION_TEMPLATE.format(
                n=len(command.selected_conclusion_revision_ids)
            )

            # 7. Insert turn
            turn = await TimelineRepository.insert_turn(
                session,
                workspace_id=command.workspace_id,
                turn_number=turn_number,
                kind="synthesis",
                status="question_draft",
                question_text=question_text,
                question_origin="synthesis",
                evidence_snapshot_id=command.evidence_snapshot_id,
                recommendation_item_id=None,
                idempotency_key=command.idempotency_key,
            )

            # 8. Insert context rows
            context_pairs = [
                (rid, i) for i, rid in enumerate(command.selected_conclusion_revision_ids)
            ]
            await TimelineRepository.insert_turn_context(
                session,
                turn_id=turn.id,
                conclusion_revision_ids=context_pairs,
            )

            # 9. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.turn.create_synthesis",
                    actor_user_id=actor_id,
                    resource_type="research_turn",
                    resource_id=turn.id,
                    payload={
                        "turn_number": turn_number,
                        "kind": "synthesis",
                        "context_count": len(command.selected_conclusion_revision_ids),
                    },
                ),
            )

            return TurnRef(
                turn_id=turn.id,
                workspace_id=command.workspace_id,
                turn_number=turn_number,
                kind="synthesis",
                status="question_draft",
                question_text=question_text,
                question_origin="synthesis",
                evidence_snapshot_id=command.evidence_snapshot_id,
            )

    async def start_planning(self, turn_id: UUID) -> PlanVersionRef:
        """Lock turn inputs and transition to planning state.

        This writes prompt_template_version and output_schema_version,
        then transitions the turn status to "planning". After this,
        question, snapshot and context cannot be changed.

        Args:
            turn_id: The turn ID.

        Returns:
            PlanVersionRef stub (actual plan generation is in Task 6).

        Raises:
            AppError: state_conflict if the turn cannot enter planning.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            turn = await TimelineRepository.get_turn(session, turn_id)
            if turn is None:
                raise AppError(
                    code="not_found",
                    message="Turn not found",
                    retryable=False,
                    fields={"turn_id": str(turn_id)},
                )

            if not TurnStateMachine.can_plan(turn.status):
                raise AppError(
                    code="state_conflict",
                    message=f"Turn in status '{turn.status}' cannot start planning",
                    retryable=True,
                    fields={"turn_id": str(turn_id), "status": turn.status},
                )

            # Lock inputs
            await TimelineRepository.lock_turn_inputs(
                session,
                turn_id,
                prompt_template_version=RECOMMENDATION_PROMPT_VERSION,
                output_schema_version="plan-output-v1",
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.turn.start_planning",
                    actor_user_id=actor_id,
                    resource_type="research_turn",
                    resource_id=turn_id,
                    payload={"turn_number": turn.turn_number},
                ),
            )

            # Return a stub — Task 6 will implement actual plan generation
            return PlanVersionRef(
                plan_id=turn_id,  # placeholder
                turn_id=turn_id,
                version_number=0,
                status="planning",
            )

    async def delete_turn(self, workspace_id: UUID, turn_id: UUID) -> None:
        """Delete a research turn and its related data (CASCADE).

        Args:
            workspace_id: Workspace ID (ownership check).
            turn_id: Turn ID to delete.

        Raises:
            AppError: not_found if turn doesn't exist or doesn't belong to workspace.
        """
        from packages.research.timeline.access import require_owned_turn

        async with self._scoped_session() as session:
            turn = await require_owned_turn(
                session, workspace_id, turn_id, self._actor_id
            )
            await session.delete(turn)

    @staticmethod
    def _derive_origin(
        recommendation_item_id: UUID | None,
        question_text: str,
    ) -> str:
        """Derive question_origin from recommendation_item_id and question text.

        Rules:
          - recommendation_item_id present + text unchanged → initial_ai or followup_ai
          - recommendation_item_id present + text changed → ai_edited
          - no recommendation_item_id → manual

        Note: We can't distinguish initial_ai from followup_ai here without
        querying the batch. The caller (API layer) should set this based on
        the batch mode. For now, we use 'initial_ai' as default when a
        recommendation_item_id is present and text is unchanged.

        This is a simplified derivation — the API layer will refine it.
        """
        if recommendation_item_id is not None:
            # Without comparing original text, we can't determine ai_edited.
            # The API layer will compare and override if needed.
            return "initial_ai"
        return "manual"
