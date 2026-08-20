"""Conclusion bar service: push / list / remove / assemble final conclusion.

Mirrors ConclusionService (ScopedSessionMixin, session_factory + dept_id +
actor_id).  The assemble step merges checked bar items into a single
structured JSON ``{metadata, points, series, _tracing}`` and persists it as a
ResearchConclusion (source_type="assembled") with one ResearchConclusionRevision
whose ``statement`` holds the assembled JSON.

Key invariants:
  - push_item validates turn ownership before insert.
  - remove_item validates workspace ownership before delete.
  - assemble_final_conclusion loads checked items, normalises each to
    {metadata, points, series}, merges them, and records full tracing.
  - All mutations are audited via AuditRecorder.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.timeline.conclusion_bar_repository import ConclusionBarRepository
from packages.research.timeline.contracts import (
    AssembleFinalConclusionCommand,
    BarItemRef,
    PushBarItemCommand,
)
from packages.research.timeline.entities import (
    ResearchConclusion,
    ResearchConclusionBarItem,
    ResearchConclusionRevision,
    ResearchTurn,
)

logger = logging.getLogger("research.conclusion_bar_service")


class ConclusionBarService(ScopedSessionMixin):
    """Service for the conclusion bar: push / list / remove / assemble.

    Depends on session_factory, department_id, actor_id (same as
    ConclusionService).
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

    # ============================================================
    # Public API
    # ============================================================

    async def push_item(self, command: PushBarItemCommand) -> BarItemRef:
        """Push a report block snapshot to the conclusion bar.

        Validates that the turn belongs to the workspace, then inserts a
        bar item row and audits the action.

        Args:
            command: PushBarItemCommand with snapshot + source_info.

        Returns:
            BarItemRef for the inserted item.

        Raises:
            AppError: not_found if turn doesn't belong to workspace.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Verify turn belongs to workspace
            turn = await session.get(ResearchTurn, command.turn_id)
            if turn is None or turn.workspace_id != command.workspace_id:
                raise AppError(
                    code="not_found",
                    message="轮次不存在或不属于该工作空间",
                    retryable=False,
                    fields={"turn_id": str(command.turn_id)},
                )

            # 2. Insert bar item
            item = await ConclusionBarRepository.insert_item(
                session,
                workspace_id=command.workspace_id,
                turn_id=command.turn_id,
                block_type=command.block_type,
                title=command.title,
                content_snapshot=command.content_snapshot,
                source_info=command.source_info,
                created_by=actor_id,
            )

            # 3. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion_bar.push",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion_bar_item",
                    resource_id=item.id,
                    payload={
                        "block_type": command.block_type,
                        "turn_id": str(command.turn_id),
                    },
                ),
            )

            return self._to_ref(item)

    async def list_items(self, workspace_id: UUID) -> dict[str, Any]:
        """List all bar items for a workspace (newest first).

        Args:
            workspace_id: Workspace ID.

        Returns:
            Dict with "items" list of BarItemRef-shaped dicts.
        """
        async with self._scoped_session() as session:
            items = await ConclusionBarRepository.list_items(session, workspace_id)
            return {"items": [self._to_ref(item).to_dict() for item in items]}

    async def remove_item(
        self,
        workspace_id: UUID,
        item_id: UUID,
    ) -> dict[str, Any]:
        """Remove a bar item (hard delete).

        Validates workspace ownership before delete.

        Args:
            workspace_id: Workspace ID for ownership check.
            item_id: Bar item ID to remove.

        Returns:
            Dict with item_id and status.

        Raises:
            AppError: not_found if item doesn't exist or doesn't belong to ws.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            item = await ConclusionBarRepository.get_item(session, item_id)
            if item is None or item.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="结论栏条目不存在",
                    retryable=False,
                    fields={"item_id": str(item_id)},
                )

            await ConclusionBarRepository.delete_item(session, item_id)

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion_bar.remove",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion_bar_item",
                    resource_id=item_id,
                ),
            )

        return {"item_id": str(item_id), "status": "removed"}

    async def assemble_final_conclusion(
        self,
        command: AssembleFinalConclusionCommand,
    ) -> dict[str, Any]:
        """Assemble checked bar items into a final conclusion.

        Loads items by IDs, validates they all belong to the workspace,
        normalises each to {metadata, points, series}, merges them into a
        single structured JSON, and persists as a ResearchConclusion
        (source_type="assembled") with one ResearchConclusionRevision whose
        ``statement`` holds the assembled JSON.

        Args:
            command: AssembleFinalConclusionCommand.

        Returns:
            Dict with conclusion_id, statement, item_count.

        Raises:
            AppError: not_found if any item is missing or cross-workspace.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Load checked items (preserves input order)
            item_ids = list(command.item_ids)
            items = await ConclusionBarRepository.get_items_by_ids(session, item_ids)

            # 2. Validate count + workspace ownership
            if len(items) != len(item_ids):
                raise AppError(
                    code="not_found",
                    message="部分结论栏条目不存在",
                    retryable=False,
                    fields={},
                )
            for it in items:
                if it.workspace_id != command.workspace_id:
                    raise AppError(
                        code="not_found",
                        message="结论栏条目不属于该工作空间",
                        retryable=False,
                        fields={"item_id": str(it.id)},
                    )

            # 3. Normalise + merge
            assembled = self._merge_structured(items, command.title)

            # 4. Persist as ResearchConclusion + Revision
            statement = json.dumps(assembled, ensure_ascii=False)
            conclusion = ResearchConclusion(
                workspace_id=command.workspace_id,
                source_turn_id=None,
                source_type="assembled",
                evidence_status="data_supported",
                status="active",
                created_by=actor_id,
                lock_version=0,
            )
            session.add(conclusion)
            await session.flush()

            revision = ResearchConclusionRevision(
                conclusion_id=conclusion.id,
                revision_number=1,
                statement=statement,
                editor=actor_id,
            )
            session.add(revision)
            await session.flush()

            await session.execute(
                sa.update(ResearchConclusion)
                .where(ResearchConclusion.id == conclusion.id)
                .values(current_revision_id=revision.id, updated_at=sa.func.now())
            )

            # 5. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.assemble",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion",
                    resource_id=conclusion.id,
                    payload={
                        "item_count": len(items),
                        "idempotency_key": command.idempotency_key,
                    },
                ),
            )

        return {
            "conclusion_id": str(conclusion.id),
            "statement": statement,
            "item_count": len(items),
        }

    # ============================================================
    # Internal helpers
    # ============================================================

    @staticmethod
    def _to_ref(item: ResearchConclusionBarItem) -> BarItemRef:
        """Convert an ORM row to a BarItemRef (stringified fields)."""
        created_at = item.created_at
        created_str = created_at.isoformat() if created_at else ""
        return BarItemRef(
            id=str(item.id),
            workspace_id=str(item.workspace_id),
            turn_id=str(item.turn_id),
            block_type=item.block_type,
            title=item.title,
            content_snapshot=dict(item.content_snapshot or {}),
            source_info=dict(item.source_info or {}),
            created_at=created_str,
        )

    def _extract_structured(
        self,
        item: ResearchConclusionBarItem,
    ) -> dict[str, Any]:
        """Normalise a bar item to a {metadata, points, series} structure.

        - echarts/chart_ref: pull series[].data into the ``series`` array,
          each entry named after the series name with columns/data.
        - structured: already {metadata, points, series} — pass through.
        - table: {columns, rows} → a single series entry.
        - text: stored as a metadata note.
        """
        snapshot: dict[str, Any] = dict(item.content_snapshot or {})
        block_type = item.block_type
        result: dict[str, Any] = {"metadata": {}, "points": [], "series": []}

        if block_type in ("echarts", "chart_ref"):
            series_list = snapshot.get("series")
            if isinstance(series_list, list):
                for s in series_list:
                    if not isinstance(s, dict):
                        continue
                    name = s.get("name") or item.title
                    data = s.get("data")
                    # echarts series data is typically [value] or [[x, y], ...]
                    columns: list[str] = []
                    rows: list[Any] = []
                    if isinstance(data, list):
                        if data and isinstance(data[0], list):
                            # [[x, y], ...]
                            columns = ["x", "y"]
                            rows = [list(r) for r in data if isinstance(r, list)]
                        else:
                            # [v1, v2, ...]
                            columns = ["index", "value"]
                            rows = [[i + 1, v] for i, v in enumerate(data)]
                    result["series"].append({"name": str(name), "columns": columns, "rows": rows})
            # chart title as metadata
            title = snapshot.get("title")
            if isinstance(title, dict) and title.get("text"):
                result["metadata"]["chart_title"] = title["text"]
            return result

        if block_type == "structured":
            metadata = snapshot.get("metadata")
            if isinstance(metadata, dict):
                result["metadata"] = metadata
            points = snapshot.get("points")
            if isinstance(points, list):
                result["points"] = points
            series = snapshot.get("series")
            if isinstance(series, list):
                result["series"] = series
            return result

        if block_type == "table":
            columns = snapshot.get("columns")
            rows = snapshot.get("rows")
            result["series"].append(
                {
                    "name": item.title,
                    "columns": list(columns) if isinstance(columns, list) else [],
                    "rows": list(rows) if isinstance(rows, list) else [],
                }
            )
            return result

        if block_type == "text":
            # text snapshot is wrapped as {"text": "..."}; extract the note
            if isinstance(snapshot, dict) and "text" in snapshot:
                text_val = snapshot["text"]
            elif isinstance(snapshot, str):
                text_val = snapshot
            else:
                text_val = json.dumps(snapshot, ensure_ascii=False)
            result["metadata"]["note"] = text_val
            return result

        # Fallback: keep snapshot as-is in metadata
        result["metadata"]["raw"] = snapshot
        return result

    def _merge_structured(
        self,
        items: list[ResearchConclusionBarItem],
        title: str,
    ) -> dict[str, Any]:
        """Merge multiple bar items into one assembled structure.

        Combines metadata (deduped keys), concatenates points and series,
        and builds a ``_tracing`` array with each item's provenance.
        """
        merged_metadata: dict[str, Any] = {"title": title}
        merged_points: list[Any] = []
        merged_series: list[Any] = []
        tracing: list[dict[str, Any]] = []

        for item in items:
            extracted = self._extract_structured(item)
            # Merge metadata (later items don't overwrite earlier title)
            for k, v in extracted["metadata"].items():
                if k == "title":
                    continue
                merged_metadata.setdefault(k, v)
            merged_points.extend(extracted["points"])
            merged_series.extend(extracted["series"])

            source = dict(item.source_info or {})
            tracing.append(
                {
                    "bar_item_id": str(item.id),
                    "turn_number": source.get("turn_number"),
                    "block_type": item.block_type,
                    "title": item.title,
                }
            )

        merged_metadata["source_count"] = len(items)
        merged_metadata["assembled_at"] = datetime.now(UTC).isoformat()

        return {
            "metadata": merged_metadata,
            "points": merged_points,
            "series": merged_series,
            "_tracing": tracing,
        }
