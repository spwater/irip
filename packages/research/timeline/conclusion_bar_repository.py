"""Conclusion bar item repository: persistence and queries.

All methods are @staticmethod async, accept AsyncSession, and do not
manage transactions.  Mirrors the ConclusionRepository pattern.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.timeline.entities import ResearchConclusionBarItem


class ConclusionBarRepository:
    """Bar item persistence: insert / list / get / delete / batch-get."""

    @staticmethod
    async def insert_item(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        turn_id: UUID,
        block_type: str,
        title: str,
        content_snapshot: dict[str, Any],
        source_info: dict[str, Any],
        created_by: UUID,
    ) -> ResearchConclusionBarItem:
        """Insert a new bar item row."""
        item = ResearchConclusionBarItem(
            id=new_id(),
            workspace_id=workspace_id,
            turn_id=turn_id,
            block_type=block_type,
            title=title,
            content_snapshot=content_snapshot,
            source_info=source_info,
            created_by=created_by,
        )
        session.add(item)
        await session.flush()
        return item

    @staticmethod
    async def list_items(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchConclusionBarItem]:
        """List all bar items for a workspace, newest first."""
        result = await session.execute(
            sa.select(ResearchConclusionBarItem)
            .where(ResearchConclusionBarItem.workspace_id == workspace_id)
            .order_by(ResearchConclusionBarItem.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_item(
        session: AsyncSession,
        item_id: UUID,
    ) -> ResearchConclusionBarItem | None:
        """Get a bar item by ID."""
        result = await session.execute(
            sa.select(ResearchConclusionBarItem).where(ResearchConclusionBarItem.id == item_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def delete_item(
        session: AsyncSession,
        item_id: UUID,
    ) -> bool:
        """Hard-delete a bar item by ID.

        Returns True if a row was deleted, False otherwise.
        """
        result = await session.execute(
            sa.delete(ResearchConclusionBarItem)
            .where(ResearchConclusionBarItem.id == item_id)
            .returning(ResearchConclusionBarItem.id)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def get_items_by_ids(
        session: AsyncSession,
        item_ids: list[UUID],
    ) -> list[ResearchConclusionBarItem]:
        """Get multiple bar items by ID (preserves input order)."""
        if not item_ids:
            return []
        result = await session.execute(
            sa.select(ResearchConclusionBarItem).where(ResearchConclusionBarItem.id.in_(item_ids))
        )
        rows_by_id: dict[UUID, ResearchConclusionBarItem] = {
            row.id: row for row in result.scalars().all()
        }
        return [rows_by_id[i] for i in item_ids if i in rows_by_id]
