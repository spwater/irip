"""QA edge-case tests for Task 18 (IRIP).

Additional edge cases not covered by the engineer's test suite:
- Approve a non-pending candidate → candidate_not_pending
- Reject by same user (submitter) → self_approval_forbidden
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.parameters.service import ParameterService
from tests.integration.parameters.conftest import _create_derivation_chain


class TestQAEdgeCases:
    """QA edge-case verification tests."""

    @pytest.mark.asyncio
    async def test_approve_non_pending_candidate_raises(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Approving an already-approved candidate → candidate_not_pending.

        Flow:
        1. Create derivation chain;
        2. Create parameter + candidate;
        3. Approve with different reviewer → success;
        4. Try to approve the same candidate again → candidate_not_pending.
        """
        org_id = param_setup["organization_id"]
        actor_id = param_setup["actor_id"]
        reviewer_id = new_id()

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=2)
        run_ref = chain["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )

        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        output = run_ref.outputs[0]
        candidate_result = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run_ref.id,
            value=str(output.value),
            unit=output.unit,
            confidence=str(output.confidence),
        )
        candidate_id = candidate_result["candidate_id"]

        # First approval → success
        await param_service.approve(
            candidate_id=candidate_id,
            reviewer=reviewer_id,
        )

        # Second approval on same candidate → candidate_not_pending
        with pytest.raises(AppError) as exc_info:
            await param_service.approve(
                candidate_id=candidate_id,
                reviewer=reviewer_id,
            )
        assert exc_info.value.code == "candidate_not_pending"

    @pytest.mark.asyncio
    async def test_reject_by_same_user_raises_self_approval(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Reject by same user (submitter) → self_approval_forbidden.

        Separation of duty applies to reject as well.
        """
        org_id = param_setup["organization_id"]
        actor_id = param_setup["actor_id"]

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=2)
        run_ref = chain["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            organization_id=org_id,
            actor_id=actor_id,
        )

        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        output = run_ref.outputs[0]
        candidate_result = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run_ref.id,
            value=str(output.value),
            unit=output.unit,
            confidence=str(output.confidence),
        )
        candidate_id = candidate_result["candidate_id"]

        # Reject by same user (submitter) → self_approval_forbidden
        with pytest.raises(AppError) as exc_info:
            await param_service.reject(
                candidate_id=candidate_id,
                reviewer=actor_id,
                comment="self reject",
            )
        assert exc_info.value.code == "self_approval_forbidden"
