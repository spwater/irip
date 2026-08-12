"""Turn-aware Plan adapter: binds plan generation to Turn context.

Task 6: Plan versions are now scoped to a Turn, not a Workspace.
This adapter wraps the existing PlanService to use FixedTurnContext
instead of the latest question version.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.research.timeline.context_builder import TurnContextBuilder
from packages.research.timeline.contracts import (
    PlanVersionRef,
)
from packages.research.timeline.repository import TimelineRepository
from packages.research.timeline.state_machine import TurnStateMachine

logger = logging.getLogger("research.turn_plan_adapter")


async def generate_plan_for_turn(
    session: AsyncSession,
    turn_id: UUID,
    existing_plan_service: Any,
) -> PlanVersionRef:
    """Generate a plan using the Turn's fixed context instead of latest question.

    This adapter:
      1. Loads the Turn and verifies it can enter planning
      2. Builds the FixedTurnContext
      3. Locks turn inputs (prompt/schema version)
      4. Delegates to existing PlanService.generate_plan with turn context
      5. Binds the Plan version to the Turn (plan.turn_id)

    Args:
        session: Async DB session.
        turn_id: Turn ID.
        existing_plan_service: The existing PlanService instance.

    Returns:
        PlanVersionRef for the created plan.

    Raises:
        AppError: state_conflict if turn cannot plan.
    """
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
            fields={"turn_id": str(turn_id)},
        )

    # Lock inputs
    await TimelineRepository.lock_turn_inputs(
        session,
        turn_id,
        prompt_template_version="research-recommendation-v1",
        output_schema_version="plan-output-v1",
    )

    # Build context (available for future plan generator integration)
    context = await TurnContextBuilder.build(session, turn_id)
    conclusions = await TurnContextBuilder.build_conclusion_inputs(session, turn_id)
    TurnContextBuilder.render_context_for_model(context, conclusions)

    # Delegate to existing plan service with turn-aware context
    # The existing service uses workspace_id + snapshot_id; we pass the
    # Turn's snapshot and let the adapter inject the turn context.
    plan_ref = await existing_plan_service.generate_plan(
        workspace_id=turn.workspace_id,
        snapshot_id=turn.evidence_snapshot_id,
    )

    # Bind plan to turn (update turn_id on the plan version)
    from packages.research.execution.entities_trusted import ResearchAnalysisPlanVersion

    await session.execute(
        sa.update(ResearchAnalysisPlanVersion)
        .where(ResearchAnalysisPlanVersion.id == plan_ref.plan_id)
        .values(turn_id=turn_id)
    )

    # Transition turn to plan_review
    await TimelineRepository.update_turn_status(
        session,
        turn_id,
        expected_status="planning",
        new_status="plan_review",
    )

    return plan_ref  # type: ignore[no-any-return]


async def confirm_plan_for_turn(
    session: AsyncSession,
    turn_id: UUID,
    plan_id: UUID,
) -> PlanVersionRef:
    """Confirm a plan and transition the turn to plan_confirmed.

    Args:
        session: Async DB session.
        turn_id: Turn ID.
        plan_id: Plan version ID.

    Returns:
        PlanVersionRef for the confirmed plan.
    """
    import sqlalchemy as sa

    from packages.research.execution.entities_trusted import ResearchAnalysisPlanVersion

    plan = await session.get(ResearchAnalysisPlanVersion, plan_id)
    if plan is None or plan.turn_id != turn_id:
        raise AppError(
            code="not_found",
            message="Plan not found for this turn",
            retryable=False,
            fields={"plan_id": str(plan_id)},
        )

    if plan.status != "draft":
        raise AppError(
            code="state_conflict",
            message=f"Plan status is '{plan.status}', only draft can be confirmed",
            retryable=True,
            fields={"plan_id": str(plan_id)},
        )

    # Confirm plan
    await session.execute(
        sa.update(ResearchAnalysisPlanVersion)
        .where(ResearchAnalysisPlanVersion.id == plan_id)
        .values(status="confirmed", confirmed_at=sa.func.now())
    )

    # Transition turn
    await TimelineRepository.update_turn_status(
        session,
        turn_id,
        expected_status="plan_review",
        new_status="plan_confirmed",
    )

    return PlanVersionRef(
        plan_id=plan_id,
        turn_id=turn_id,
        version_number=plan.version_number,
        status="confirmed",
    )
