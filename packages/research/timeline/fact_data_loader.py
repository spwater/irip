"""Fact data loader: shared utility for loading fact_data from evidence refs.

Used by:
  - RecommendationService (initial + followup prompt context)
  - TimelineQueryService (turn detail → fact_context for chart-ref rendering)
  - AnalysisService (analyze endpoint → data context for PlanService)

Extracted from inline code in research_timeline.py to avoid triplicate logic.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import build_session_factory
from packages.research.entities import WorkspaceEvidenceRef

logger = logging.getLogger("research.fact_data")


class FactDataLoader:
    """Loads fact_data from WorkspaceEvidenceRef via FactQueryService.

    Encapsulates the three-step flow:
      1. Query WorkspaceEvidenceRef for active refs
      2. Resolve admin user for FactQueryService
      3. Call CoreFactProviderImpl.get_fact_data for each ref
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def load_fact_rows(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[dict[str, Any]]:
        """Load fact_data rows for a workspace.

        Returns a list of dicts, each containing:
          - source_name: str
          - metadata: dict
          - points: list
          - series: list (with name, columns, rows_sample)

        Args:
            session: Any DB session (for querying WorkspaceEvidenceRef).
            workspace_id: Workspace ID.

        Returns:
            List of fact_data dicts (empty if loading fails).
        """
        refs_result = await session.execute(
            sa.select(WorkspaceEvidenceRef)
            .where(
                WorkspaceEvidenceRef.workspace_id == workspace_id,
                WorkspaceEvidenceRef.status == "active",
            )
        )
        refs = refs_result.scalars().all()
        if not refs:
            return []

        # Build FactQueryService + CoreFactProviderImpl
        try:
            from apps.api.main import _build_s3_repo
            from packages.facts.query_service import FactQueryService
            from packages.research.lineage.core_adapter import CoreFactProviderImpl
        except ImportError:
            logger.warning("FactQueryService or CoreFactProviderImpl not available")
            return []

        analysis_db_url = os.environ.get(
            "IRIP_ALEMBIC_DATABASE_URL",
            "postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip",
        )
        analysis_factory = build_session_factory(analysis_db_url)

        async with analysis_factory() as fact_session:
            user_result = await fact_session.execute(
                sa.text(
                    "SELECT id, department_id FROM app_user "
                    "WHERE email = 'admin@irip.local' LIMIT 1"
                )
            )
            user_row = user_result.first()
            if not user_row:
                return []

            s3_repo = _build_s3_repo()
            fact_query = FactQueryService(
                session_factory=analysis_factory,
                department_id=user_row[1],
                actor_id=user_row[0],
                s3_repo=s3_repo,
            )
            fact_provider = CoreFactProviderImpl(query_service=fact_query)

            fact_rows: list[dict[str, Any]] = []
            for ref in refs:
                fact_info: dict[str, Any] = {"source_name": ref.source_name or ""}
                try:
                    data = await fact_provider.get_fact_data(ref.source_id)
                    if isinstance(data, dict):
                        fact_info["metadata"] = data.get("metadata", {})
                        fact_info["points"] = data.get("points", [])
                        series_full = data.get("series", [])
                        fact_info["series"] = [
                            {
                                "name": s.get("name", ""),
                                "columns": s.get("columns", []),
                                "rows_sample": (s.get("rows", []) or [])[:5],
                            }
                            for s in series_full if isinstance(s, dict)
                        ]
                except Exception as exc:
                    logger.warning(
                        "Failed to load fact data for %s: %s", ref.source_id, exc
                    )
                fact_rows.append(fact_info)

            return fact_rows

    async def load_fact_context_string(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> str | None:
        """Load fact_data and format as systemContext string for ChartRefBlock.

        Format: "### 样品: XXX\\n```json\\n{...}\\n```"

        Returns None if no data or loading fails.
        """
        samples = await self.load_fact_samples(session, workspace_id)
        if not samples:
            return None

        context_parts: list[str] = []
        for s in samples:
            context_parts.append(
                f"### 样品: {s['label']}\n"
                f"```json\n{json.dumps(s['data'], ensure_ascii=False)}\n```"
            )
        return "\n\n".join(context_parts)

    async def load_fact_samples(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[dict[str, Any]] | None:
        """Load fact_data as structured sample list for ChartRefBlock.

        Returns a list of dicts, each containing:
          - label: str (sample name)
          - data: dict (full fact_data: metadata/points/series)

        Returns None if no data or loading fails.
        This is the structured alternative to load_fact_context_string —
        no text parsing needed on the frontend.
        """
        try:
            refs_result = await session.execute(
                sa.select(WorkspaceEvidenceRef)
                .where(
                    WorkspaceEvidenceRef.workspace_id == workspace_id,
                    WorkspaceEvidenceRef.status == "active",
                )
            )
            refs = refs_result.scalars().all()
            if not refs:
                return None

            from apps.api.main import _build_s3_repo
            from packages.facts.query_service import FactQueryService
            from packages.research.lineage.core_adapter import CoreFactProviderImpl

            analysis_db_url = os.environ.get(
                "IRIP_ALEMBIC_DATABASE_URL",
                "postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip",
            )
            analysis_factory = build_session_factory(analysis_db_url)

            async with analysis_factory() as fact_session:
                user_result = await fact_session.execute(
                    sa.text(
                        "SELECT id, department_id FROM app_user "
                        "WHERE email = 'admin@irip.local' LIMIT 1"
                    )
                )
                user_row = user_result.first()
                if not user_row:
                    return None

                s3_repo = _build_s3_repo()
                fact_query = FactQueryService(
                    session_factory=analysis_factory,
                    department_id=user_row[1],
                    actor_id=user_row[0],
                    s3_repo=s3_repo,
                )
                fact_provider = CoreFactProviderImpl(query_service=fact_query)

                samples: list[dict[str, Any]] = []
                for ref in refs:
                    data = await fact_provider.get_fact_data(ref.source_id)
                    if isinstance(data, dict):
                        label = ref.source_name or str(ref.source_id)
                        samples.append({"label": label, "data": data})
                return samples if samples else None
        except Exception as exc:
            logger.warning("fact_samples loading failed: %s", exc)
        return None
