"""Analysis service: orchestrates the full analysis flow for a research turn.

Extracted from the inline run_analysis endpoint in research_timeline.py.

Flow:
  1. Load turn + snapshot
  2. Build AI provider + model gateway
  3. Call PlanService (generate_plan → confirm_plan → analyze_data)
  4. Persist result to ResearchTurnResult
  5. Auto-trigger followup recommendations
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import build_session_factory
from packages.research.timeline.entities import (
    ResearchTurn,
    ResearchTurnResult,
)

logger = logging.getLogger("research.analysis_service")


class AnalysisService:
    """Orchestrates the full analysis flow for a research turn.

    Data is loaded through CoreFactProvider (injected into PlanService),
    keeping the experiment data access read-only and department-scoped.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._factory = session_factory

    async def run_analysis(
        self,
        workspace_id: UUID,
        turn_id: UUID,
    ) -> dict[str, Any]:
        """Run the full analysis flow for a turn.

        Args:
            workspace_id: Workspace ID.
            turn_id: Turn ID to analyze.

        Returns:
            Dict with turn_id, status, and result_summary.

        Raises:
            AppError: not_found if turn or AI config missing.
            AppError: analysis_failed if analysis fails.
        """
        from packages.common.errors import AppError

        db_url = os.environ.get(
            "IRIP_DATABASE_URL",
            "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
        )
        factory = build_session_factory(db_url)

        # 1. Load turn
        async with factory() as session:
            turn = await session.get(ResearchTurn, turn_id)
            if turn is None or turn.workspace_id != workspace_id:
                raise AppError(code="not_found", message="Turn not found", retryable=False)
            if turn.status not in ("question_draft", "run_failed", "succeeded"):
                raise AppError(
                    code="state_conflict",
                    message=f"Turn in status '{turn.status}' cannot be analyzed",
                    retryable=True,
                )
            turn.status = "planning"
            await session.commit()
            question_text = turn.question_text_snapshot
            snapshot_id = turn.evidence_snapshot_id

        # 2. Load AI config
        ai_config = await self._load_ai_config()
        if not ai_config or not ai_config.get("base_url") or not ai_config.get("api_key"):
            raise AppError(
                code="ai_config_missing",
                message="AI not configured",
                retryable=False,
            )

        # 3. Build AI provider + model gateway + PlanService
        from packages.ai.openai_compatible import OpenAICompatibleProvider
        from packages.research.execution.models_trusted import ModelConfig, TaskType
        from packages.research.planning.context_router import ContextRouter
        from packages.research.planning.model_gateway import ModelGateway
        from packages.research.planning.plan_core import PlanService

        research_model_name = ai_config.get("research_model_name") or ai_config.get(
            "model_name", ""
        )
        thinking = ai_config.get("research_thinking_enabled", False)
        ai_provider = OpenAICompatibleProvider(
            api_key=ai_config["api_key"],
            base_url=ai_config["base_url"],
            model=research_model_name,
            thinking_enabled=thinking,
        )

        model_registry = {
            task: ModelConfig(
                provider="openai_compatible",
                model=research_model_name,
                version="custom",
                context_limit=128000,
            )
            for task in TaskType
        }
        model_gateway = ModelGateway(
            provider=ai_provider,
            audit_recorder=None,
            model_registry=model_registry,
        )
        context_router = ContextRouter()

        analysis_db_url = os.environ.get(
            "IRIP_ALEMBIC_DATABASE_URL",
            "postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip",
        )
        analysis_factory = build_session_factory(analysis_db_url)

        async with analysis_factory() as session:
            user_result = await session.execute(
                sa.text(
                    "SELECT id, department_id FROM app_user "
                    "WHERE email = 'admin@irip.local' LIMIT 1"
                )
            )
            user_row = user_result.first()
            if not user_row:
                raise AppError(code="not_found", message="Admin user not found", retryable=False)

            from apps.api.main import _build_s3_repo
            from packages.facts.query_service import FactQueryService
            from packages.research.lineage.core_adapter import CoreFactProviderImpl

            s3_repo = _build_s3_repo()
            fact_query = FactQueryService(
                session_factory=analysis_factory,
                department_id=user_row[1],
                actor_id=user_row[0],
                s3_repo=s3_repo,
            )
            fact_provider = CoreFactProviderImpl(query_service=fact_query)

            plan_service = PlanService(
                session_factory=analysis_factory,
                department_id=user_row[1],
                actor_id=user_row[0],
                fact_provider=fact_provider,
                model_gateway=model_gateway,
                context_router=context_router,
            )

            # 6. Generate plan → auto-confirm → analyze
            try:
                plan = await plan_service.generate_plan(
                    workspace_id=workspace_id,
                    snapshot_id=snapshot_id,
                )

                plan = await plan_service.confirm_plan(
                    workspace_id=workspace_id,
                    plan_id=plan.plan_id,
                )

                analysis_result = await plan_service.analyze_data(
                    workspace_id=workspace_id,
                    plan_id=plan.plan_id,
                    snapshot_id=snapshot_id,
                    turn_id=turn_id,
                )

                # 7. Persist result
                # analyze_data returns a dict with "analysis_result" and "data_context"
                if isinstance(analysis_result, dict):
                    analysis_text = analysis_result.get("analysis_result", "")
                elif isinstance(analysis_result, str):
                    analysis_text = analysis_result
                else:
                    analysis_text = str(analysis_result)
                # Fix missing ``` code fences for chart-ref/echarts/data blocks
                import re as _re

                for _tag in ("chart-ref", "echarts", "data"):
                    _pattern = _re.compile(
                        r"(?m)^(" + _tag + r")\s*\n(\{[\s\S]*?\})\s*(?:\n\n|\n(?!\s*[}\]])|$)"
                    )

                    def _repl(m: _re.Match[str], _t: str = _tag) -> str:
                        return "```" + _t + "\n" + m.group(2) + "\n```"

                    analysis_text = _pattern.sub(_repl, analysis_text)

                async with factory() as session:
                    turn = await session.get(ResearchTurn, turn_id)
                    if turn:
                        turn.status = "succeeded"

                    result_row = await session.execute(
                        sa.select(ResearchTurnResult).where(ResearchTurnResult.turn_id == turn_id)
                    )
                    tr = result_row.scalar_one_or_none()
                    if tr is None:
                        tr = ResearchTurnResult(
                            id=uuid.uuid4(),
                            turn_id=turn_id,
                            result_kind="analysis",
                        )
                        session.add(tr)
                    tr.summary = analysis_text[:500]
                    tr.structured_output = {
                        "analysis_markdown": analysis_text,
                    }
                    await session.commit()

            except Exception as e:
                logger.exception(
                    "Analysis failed for turn %s (workspace %s)",
                    turn_id,
                    workspace_id,
                )
                async with factory() as session:
                    turn = await session.get(ResearchTurn, turn_id)
                    if turn:
                        turn.status = "run_failed"
                        await session.commit()
                raise AppError(
                    code="analysis_failed",
                    message=(
                        e.message
                        if isinstance(e, AppError) and e.message
                        else f"Analysis failed: {e}"
                    ),
                    retryable=True,
                ) from e

            # 8. Auto-trigger followup recommendations
            try:
                from packages.jobs.outbox import OutboxDispatcher
                from packages.research.timeline.contracts import (
                    RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
                    RECOMMENDATION_PROMPT_VERSION,
                )
                from packages.research.timeline.repository import TimelineRepository

                followup_key = f"followup:{turn_id}"
                async with factory() as session:
                    existing = await TimelineRepository.get_batch_by_idempotency(
                        session, workspace_id, followup_key
                    )
                    if existing is None:
                        batch = await TimelineRepository.insert_batch(
                            session,
                            workspace_id=workspace_id,
                            snapshot_id=snapshot_id,
                            mode="followup",
                            prompt_template_version=RECOMMENDATION_PROMPT_VERSION,
                            output_schema_version=RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
                            idempotency_key=followup_key,
                        )

                        followup_context = (
                            f"上一轮分析问题: {question_text}\n分析摘要: {analysis_text[:2000]}"
                        )

                        await OutboxDispatcher.enqueue(
                            session,
                            aggregate_type="research_recommendation_batch",
                            aggregate_id=batch.id,
                            event_type="research.recommendation.requested",
                            payload={
                                "batch_id": str(batch.id),
                                "mode": "followup",
                                "followup_context": followup_context[:4000],
                            },
                        )
                        await session.commit()
                        logger.info("enqueued followup recommendation batch %s", batch.id)
            except Exception as exc:
                logger.warning("Failed to enqueue followup recommendations: %s", exc)

        return {
            "turn_id": str(turn_id),
            "status": "succeeded",
            "result_summary": analysis_text[:500] if analysis_result else "",
        }

    async def _load_ai_config(self) -> dict[str, Any] | None:
        """Load the active AI configuration from the database."""
        db_url = os.environ.get(
            "IRIP_DATABASE_URL",
            "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
        )
        factory = build_session_factory(db_url)
        async with factory() as session:
            result = await session.execute(
                sa.text(
                    "SELECT base_url, api_key, model_name, "
                    "research_model_name, research_thinking_enabled "
                    "FROM ai_config WHERE enabled = true "
                    "ORDER BY updated_at DESC LIMIT 1"
                )
            )
            row = result.first()
            if row is None:
                return None
            from packages.common.crypto import EnvelopeCrypto

            crypto = EnvelopeCrypto.from_env()
            decrypted_key = crypto.decrypt(row[1])
            return {
                "base_url": row[0],
                "api_key": decrypted_key,
                "model_name": row[2],
                "research_model_name": row[3],
                "research_thinking_enabled": row[4],
            }
