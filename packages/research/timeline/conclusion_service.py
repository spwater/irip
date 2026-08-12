"""Conclusion service: candidate saving, manual creation, revision and archive.

Key invariants:
  - Candidates are NOT conclusions until the user explicitly saves them.
  - Saving a candidate creates a Conclusion + ConclusionRevision (v1).
  - source_type: "ai_original" if text unchanged, "ai_edited" if modified.
  - Manual conclusions get evidence_status="manual_unverified".
  - Revising creates a new immutable revision (revision_number + 1).
  - Old revisions are never modified — historical TurnContext stays stable.
  - Archive only changes status to "archived", doesn't delete or break refs.
  - All operations use optimistic locking (expected_lock_version).
  - Cross-workspace ID mismatches return not_found (fail-closed).
"""

from __future__ import annotations

import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.timeline.conclusion_repository import (
    CandidateRepository,
    ConclusionRepository,
)
from packages.research.timeline.contracts import (
    CandidateSelection,
    ConclusionRef,
    CreateManualConclusionCommand,
    ReviseConclusionCommand,
    SaveCandidatesCommand,
)
from packages.research.timeline.entities import (
    ResearchConclusion,
    ResearchConclusionCandidate,
    ResearchConclusionRevision,
)

logger = logging.getLogger("research.conclusion_service")


class ConclusionService(ScopedSessionMixin):
    """Service for managing conclusions: save candidates, create manual,
    revise, and archive.

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

    @staticmethod
    def _determine_source_type(
        candidate: ResearchConclusionCandidate,
        selection: CandidateSelection,
    ) -> str:
        """Determine if the saved conclusion is ai_original or ai_edited.

        If any edited field is provided and differs from the candidate's
        original content, it's "ai_edited". Otherwise "ai_original".
        """
        if selection.edited_statement is not None:
            if selection.edited_statement.strip() != candidate.statement.strip():
                return "ai_edited"
        if selection.edited_scope is not None and candidate.scope is not None:
            if selection.edited_scope.strip() != candidate.scope.strip():
                return "ai_edited"
        if selection.edited_limitations is not None and candidate.limitations is not None:
            if selection.edited_limitations.strip() != candidate.limitations.strip():
                return "ai_edited"
        return "ai_original"

    async def save_candidates(
        self,
        command: SaveCandidatesCommand,
    ) -> tuple[ConclusionRef, ...]:
        """Save selected candidates as conclusions.

        Each selection can optionally provide edited statement/scope/limitations.
        A candidate can only be saved once (saved_conclusion_id check).
        Duplicate requests with the same idempotency key return existing results.

        Args:
            command: SaveCandidatesCommand with selections.

        Returns:
            Tuple of ConclusionRef for each saved conclusion.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            results: list[ConclusionRef] = []

            for selection in command.selections:
                # 1. Load candidate
                candidate = await CandidateRepository.get_candidate(session, selection.candidate_id)
                if candidate is None:
                    raise AppError(
                        code="not_found",
                        message="候选结论不存在",
                        retryable=False,
                        fields={"candidate_id": str(selection.candidate_id)},
                    )

                # 2. Check if already saved (idempotent)
                if candidate.status == "saved" and candidate.saved_conclusion_id is not None:
                    # Return existing conclusion
                    conclusion = await ConclusionRepository.get_conclusion(
                        session, candidate.saved_conclusion_id
                    )
                    if conclusion is not None:
                        revision = await ConclusionRepository.get_latest_revision(
                            session, conclusion.id
                        )
                        if revision is not None:
                            results.append(
                                ConclusionRef(
                                    conclusion_id=conclusion.id,
                                    workspace_id=conclusion.workspace_id,
                                    source_type=conclusion.source_type,
                                    evidence_status=conclusion.evidence_status,
                                    status=conclusion.status,
                                    revision_number=revision.revision_number,
                                    statement=revision.statement,
                                )
                            )
                            continue

                # 3. Reject if already rejected
                if candidate.status == "rejected":
                    raise AppError(
                        code="state_conflict",
                        message="候选已被拒绝，无法保存",
                        retryable=True,
                        fields={"candidate_id": str(selection.candidate_id)},
                    )

                # 4. Determine source type
                source_type = self._determine_source_type(candidate, selection)

                # 5. Use edited or original text
                statement = selection.edited_statement or candidate.statement
                scope = (
                    selection.edited_scope
                    if selection.edited_scope is not None
                    else candidate.scope
                )
                limitations = (
                    selection.edited_limitations
                    if selection.edited_limitations is not None
                    else candidate.limitations
                )

                # 6. Create conclusion
                conclusion = await ConclusionRepository.insert_conclusion(
                    session,
                    workspace_id=command.workspace_id,
                    source_turn_id=candidate.turn_id,
                    source_run_id=None,  # Will be set from extraction job
                    source_candidate_id=candidate.id,
                    source_type=source_type,
                    evidence_status="data_supported",
                    created_by=actor_id,
                )

                # 7. Create revision v1
                revision = await ConclusionRepository.insert_revision(
                    session,
                    conclusion_id=conclusion.id,
                    revision_number=1,
                    statement=statement,
                    scope=scope,
                    evidence_refs=list(candidate.evidence_refs),
                    limitations=limitations,
                    editor=actor_id,
                )

                # 8. Set current revision
                await ConclusionRepository.set_current_revision(session, conclusion.id, revision.id)

                # 9. Mark candidate as saved
                await CandidateRepository.update_candidate_status(
                    session,
                    candidate.id,
                    status="saved",
                    saved_conclusion_id=conclusion.id,
                )

                # 10. Audit
                await AuditRecorder.record(
                    session,
                    AuditEventData(
                        department_id=self._dept_id,
                        action="research.conclusion.save",
                        actor_user_id=actor_id,
                        resource_type="research_conclusion",
                        resource_id=conclusion.id,
                        payload={
                            "source_type": source_type,
                            "candidate_id": str(candidate.id),
                        },
                    ),
                )

                results.append(
                    ConclusionRef(
                        conclusion_id=conclusion.id,
                        workspace_id=conclusion.workspace_id,
                        source_type=source_type,
                        evidence_status="data_supported",
                        status="active",
                        revision_number=1,
                        statement=statement,
                    )
                )

            return tuple(results)

    async def reject_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> None:
        """Reject a candidate (status -> rejected, no conclusion created).

        Args:
            workspace_id: Workspace ID for ownership check.
            candidate_id: Candidate ID to reject.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            candidate = await CandidateRepository.get_candidate(session, candidate_id)
            if candidate is None:
                raise AppError(
                    code="not_found",
                    message="候选结论不存在",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )

            if candidate.status != "pending":
                raise AppError(
                    code="state_conflict",
                    message=f"候选状态为 '{candidate.status}'，无法拒绝",
                    retryable=True,
                    fields={"candidate_id": str(candidate_id)},
                )

            await CandidateRepository.update_candidate_status(
                session, candidate_id, status="rejected"
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.reject",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion_candidate",
                    resource_id=candidate_id,
                ),
            )

    async def create_manual(
        self,
        command: CreateManualConclusionCommand,
    ) -> ConclusionRef:
        """Create a manual conclusion (no evidence).

        Manual conclusions get evidence_status="manual_unverified".
        The UI and model context must label these clearly.

        Args:
            command: CreateManualConclusionCommand.

        Returns:
            ConclusionRef for the created conclusion.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Create conclusion with manual_unverified
            conclusion = await ConclusionRepository.insert_conclusion(
                session,
                workspace_id=command.workspace_id,
                source_turn_id=None,
                source_run_id=None,
                source_candidate_id=None,
                source_type="manual",
                evidence_status="manual_unverified",
                created_by=actor_id,
            )

            # 2. Create revision v1
            revision = await ConclusionRepository.insert_revision(
                session,
                conclusion_id=conclusion.id,
                revision_number=1,
                statement=command.statement,
                scope=command.scope,
                evidence_refs=[],
                limitations=command.limitations,
                editor=actor_id,
            )

            # 3. Set current revision
            await ConclusionRepository.set_current_revision(session, conclusion.id, revision.id)

            # 4. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.create_manual",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion",
                    resource_id=conclusion.id,
                    payload={"source_type": "manual"},
                ),
            )

            return ConclusionRef(
                conclusion_id=conclusion.id,
                workspace_id=conclusion.workspace_id,
                source_type="manual",
                evidence_status="manual_unverified",
                status="active",
                revision_number=1,
                statement=command.statement,
            )

    async def revise(
        self,
        command: ReviseConclusionCommand,
    ) -> ConclusionRef:
        """Revise a conclusion (create a new immutable revision).

        Creates revision_number + 1, updates current_revision_id and
        increments lock_version. Old revisions remain unchanged so that
        historical TurnContext references stay stable.

        Args:
            command: ReviseConclusionCommand with expected_lock_version.

        Returns:
            Updated ConclusionRef.

        Raises:
            AppError: state_conflict if lock version doesn't match.
            AppError: not_found if conclusion doesn't exist.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Load conclusion
            conclusion = await ConclusionRepository.get_conclusion(session, command.conclusion_id)
            if conclusion is None:
                raise AppError(
                    code="not_found",
                    message="结论不存在",
                    retryable=False,
                    fields={"conclusion_id": str(command.conclusion_id)},
                )

            # 2. Verify workspace ownership
            if conclusion.workspace_id != command.workspace_id:
                raise AppError(
                    code="not_found",
                    message="结论不存在",
                    retryable=False,
                    fields={"conclusion_id": str(command.conclusion_id)},
                )

            # 3. Check lock version (optimistic concurrency)
            await ConclusionRepository.update_conclusion_lock(
                session,
                command.conclusion_id,
                command.expected_lock_version,
            )

            # 4. Get latest revision number
            latest = await ConclusionRepository.get_latest_revision(session, command.conclusion_id)
            new_revision_number = (latest.revision_number + 1) if latest else 1

            # 5. Create new revision (carrying forward evidence_refs from latest)
            old_evidence_refs = list(latest.evidence_refs) if latest else []
            revision = await ConclusionRepository.insert_revision(
                session,
                conclusion_id=command.conclusion_id,
                revision_number=new_revision_number,
                statement=command.statement,
                scope=command.scope,
                evidence_refs=old_evidence_refs,
                limitations=command.limitations,
                editor=actor_id,
            )

            # 6. Update current revision pointer
            await ConclusionRepository.set_current_revision(
                session, command.conclusion_id, revision.id
            )

            # 7. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.revise",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion_revision",
                    resource_id=revision.id,
                    payload={"revision_number": new_revision_number},
                ),
            )

            return ConclusionRef(
                conclusion_id=conclusion.id,
                workspace_id=conclusion.workspace_id,
                source_type=conclusion.source_type,
                evidence_status=conclusion.evidence_status,
                status=conclusion.status,
                revision_number=new_revision_number,
                statement=command.statement,
            )

    async def archive(
        self,
        workspace_id: UUID,
        conclusion_id: UUID,
        expected_lock_version: int,
    ) -> None:
        """Archive a conclusion.

        Archived conclusions are hidden from the default library view
        but historical TurnContext references remain valid.

        Args:
            workspace_id: Workspace ID for ownership check.
            conclusion_id: Conclusion ID to archive.
            expected_lock_version: Current lock version for optimistic locking.

        Raises:
            AppError: state_conflict if lock version doesn't match.
            AppError: not_found if conclusion doesn't exist.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            conclusion = await ConclusionRepository.get_conclusion(session, conclusion_id)
            if conclusion is None or conclusion.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="结论不存在",
                    retryable=False,
                    fields={"conclusion_id": str(conclusion_id)},
                )

            await ConclusionRepository.archive_conclusion(
                session,
                conclusion_id,
                expected_lock_version,
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.archive",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion",
                    resource_id=conclusion_id,
                ),
            )

    async def save_from_block(
        self,
        workspace_id: UUID,
        turn_id: UUID,
        statement: str,
        block_type: str = "table",
    ) -> dict:
        """Save a table/chart/structured data block as a conclusion.

        Creates a ResearchConclusion + first ResearchConclusionRevision.

        Args:
            workspace_id: Workspace ID.
            turn_id: Source turn ID.
            statement: Conclusion statement (may be JSON for structured data).
            block_type: Block type (table | chart | structured).

        Returns:
            Dict with conclusion_id, statement, and status.

        Raises:
            AppError: validation_failed if statement is empty.
            AppError: not_found if turn doesn't belong to workspace.
        """
        import uuid as _uuid

        from packages.research.timeline.entities import ResearchTurn

        if not statement.strip():
            from packages.common.errors import AppError

            raise AppError(
                code="validation_failed", message="结论内容不能为空"
            )

        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # Verify turn belongs to workspace
            turn = await session.get(ResearchTurn, turn_id)
            if turn is None or turn.workspace_id != workspace_id:
                from packages.common.errors import AppError

                raise AppError(
                    code="not_found", message="Turn not found", retryable=False
                )

            concl_id = _uuid.uuid4()
            rev_id = _uuid.uuid4()

            conclusion = ResearchConclusion(
                id=concl_id,
                workspace_id=workspace_id,
                source_turn_id=turn_id,
                source_type="ai_original",
                evidence_status="data_supported",
                status="active",
                created_by=actor_id,
                lock_version=0,
            )
            session.add(conclusion)
            await session.flush()

            revision = ResearchConclusionRevision(
                id=rev_id,
                conclusion_id=concl_id,
                revision_number=1,
                statement=statement,
            )
            session.add(revision)
            await session.flush()

            await session.execute(
                sa.update(ResearchConclusion)
                .where(ResearchConclusion.id == concl_id)
                .values(current_revision_id=rev_id)
            )
            await session.commit()

        return {
            "conclusion_id": str(concl_id),
            "statement": statement,
            "status": "saved",
        }

    async def list_conclusions(
        self,
        workspace_id: UUID,
    ) -> dict:
        """List all active conclusions for a workspace.

        Returns:
            Dict with "items" list of conclusion dicts.
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchConclusion).where(
                    ResearchConclusion.workspace_id == workspace_id,
                    ResearchConclusion.status == "active",
                )
            )
            items = []
            for concl in result.scalars():
                rev = None
                if concl.current_revision_id:
                    rev = await session.get(
                        ResearchConclusionRevision, concl.current_revision_id
                    )
                items.append({
                    "conclusion_id": str(concl.id),
                    "workspace_id": str(concl.workspace_id),
                    "source_type": concl.source_type,
                    "evidence_status": concl.evidence_status,
                    "status": concl.status,
                    "revision_number": rev.revision_number if rev else 0,
                    "statement": rev.statement if rev else "",
                })

        return {"items": items}

    async def delete_conclusion(
        self,
        workspace_id: UUID,
        conclusion_id: UUID,
    ) -> dict:
        """Delete a conclusion (mark as archived).

        Args:
            workspace_id: Workspace ID for ownership check.
            conclusion_id: Conclusion ID to delete.

        Returns:
            Dict with conclusion_id and status.

        Raises:
            AppError: not_found if conclusion doesn't exist.
        """
        from packages.common.errors import AppError

        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchConclusion).where(
                    ResearchConclusion.id == conclusion_id,
                    ResearchConclusion.workspace_id == workspace_id,
                )
            )
            concl = result.scalar_one_or_none()
            if concl is None:
                raise AppError(
                    code="not_found",
                    message="Conclusion not found",
                    retryable=False,
                )
            concl.status = "archived"
            await session.commit()

        return {"conclusion_id": str(conclusion_id), "status": "archived"}
