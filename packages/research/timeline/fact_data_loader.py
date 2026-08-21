"""Fact data loader: shared utility for loading fact_data from evidence refs.

Used by:
  - RecommendationService (initial + followup prompt context)
  - TimelineQueryService (turn detail -> fact_context for chart-ref rendering)
  - AnalysisService (analyze endpoint -> data context for PlanService)

Identity-aware: uses department_id and actor_id from the calling Service
instead of reading IRIP_ALEMBIC_DATABASE_URL or admin@irip.local.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.entities import WorkspaceEvidenceRef

logger = logging.getLogger("research.fact_data")


class FactDataLoader:
    """Loads fact_data from WorkspaceEvidenceRef via FactQueryService.

    Identity-aware: constructed with (session_factory, department_id, actor_id).
    Does NOT read IRIP_ALEMBIC_DATABASE_URL or admin@irip.local.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> None:
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id

    def _build_fact_provider(self) -> Any:
        """Build CoreFactProviderImpl using the injected session factory."""
        try:
            from apps.api.main import _build_s3_repo
            from packages.facts.query_service import FactQueryService
            from packages.research.lineage.core_adapter import (
                CoreFactProviderImpl,
            )
        except ImportError:
            logger.warning("FactQueryService or CoreFactProviderImpl not available")
            return None

        s3_repo = _build_s3_repo()
        fact_query = FactQueryService(
            session_factory=self._factory,
            department_id=self._dept_id,  # type: ignore[arg-type]
            actor_id=self._actor_id,
            s3_repo=s3_repo,
        )
        return CoreFactProviderImpl(query_service=fact_query)

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
        """
        refs_result = await session.execute(
            sa.select(WorkspaceEvidenceRef).where(
                WorkspaceEvidenceRef.workspace_id == workspace_id,
                WorkspaceEvidenceRef.status == "active",
            )
        )
        refs = refs_result.scalars().all()
        if not refs:
            return []

        fact_provider = self._build_fact_provider()
        if fact_provider is None:
            return []

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
                        for s in series_full
                        if isinstance(s, dict)
                    ]
            except Exception as exc:
                logger.warning("Failed to load fact data for %s: %s", ref.source_id, exc)
            fact_rows.append(fact_info)

        return fact_rows

    async def load_fact_context_string(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> str | None:
        """Load fact_data and format as systemContext string for ChartRefBlock.

        Format: "### sample: XXX\\n```json\\n{...}\\n```"

        Returns None if no data or loading fails.
        """
        samples = await self.load_fact_samples(session, workspace_id)
        if not samples:
            return None

        context_parts: list[str] = []
        for s in samples:
            context_parts.append(
                f"### \u6837\u54c1: {s['label']}\n```json\n"
                f"{json.dumps(s['data'], ensure_ascii=False)}\n```"
            )
        return "\n\n".join(context_parts)

    async def load_fact_samples(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[dict[str, Any]] | None:
        """Load fact_data as structured sample list for ChartRefBlock."""
        try:
            refs_result = await session.execute(
                sa.select(WorkspaceEvidenceRef).where(
                    WorkspaceEvidenceRef.workspace_id == workspace_id,
                    WorkspaceEvidenceRef.status == "active",
                )
            )
            refs = refs_result.scalars().all()
            if not refs:
                return None

            fact_provider = self._build_fact_provider()
            if fact_provider is None:
                return None

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
