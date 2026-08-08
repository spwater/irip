"""证据引用与证据快照子仓库。

封装两类数据库操作：
- WorkspaceEvidenceRef：证据引用的插入、列表、状态更新、计数；
- ResearchEvidenceSnapshot：证据快照的插入、列表、最新快照查询。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 EvidenceSnapshotService 通过 ScopedSessionMixin 管理。
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchEvidenceSnapshot, WorkspaceEvidenceRef


class EvidenceRefRepository:
    """证据引用数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    @staticmethod
    async def insert_evidence_ref(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        source_namespace: str,
        source_id: UUID,
        source_version: str | None = None,
        source_name: str | None = None,
        added_by: UUID,
    ) -> WorkspaceEvidenceRef:
        """插入证据引用，返回 ORM 实体。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            source_namespace: 源命名空间（如 "core:fact"）。
            source_id: 源对象 ID。
            source_version: 源版本快照（可选）。
            source_name: 源名称快照（可选）。
            added_by: 加入人 ID。

        Returns:
            WorkspaceEvidenceRef: 证据引用 ORM 实体。
        """
        ref = WorkspaceEvidenceRef(
            id=new_id(),
            workspace_id=workspace_id,
            source_namespace=source_namespace,
            source_id=source_id,
            source_version=source_version,
            source_name=source_name,
            added_by=added_by,
            status="active",
        )
        session.add(ref)
        await session.flush()
        return ref

    @staticmethod
    async def list_evidence_refs(
        session: AsyncSession,
        workspace_id: UUID,
        status: str | None = None,
    ) -> list[WorkspaceEvidenceRef]:
        """列出工作空间的证据引用。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            status: 状态过滤（可选，如 "active"）。

        Returns:
            list[WorkspaceEvidenceRef]: 证据引用列表。
        """
        stmt = sa.select(WorkspaceEvidenceRef).where(
            WorkspaceEvidenceRef.workspace_id == workspace_id
        )
        if status is not None:
            stmt = stmt.where(WorkspaceEvidenceRef.status == status)
        stmt = stmt.order_by(WorkspaceEvidenceRef.added_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_evidence_ref(
        session: AsyncSession,
        ref_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceEvidenceRef | None:
        """获取单个证据引用（校验 workspace 归属）。

        Args:
            session: 异步会话。
            ref_id: 引用 ID。
            workspace_id: 工作空间 ID。

        Returns:
            WorkspaceEvidenceRef | None: 证据引用，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(WorkspaceEvidenceRef).where(
                WorkspaceEvidenceRef.id == ref_id,
                WorkspaceEvidenceRef.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_evidence_ref_status(
        session: AsyncSession,
        ref_id: UUID,
        status: str,
    ) -> None:
        """更新证据引用状态（软删除）。

        Args:
            session: 异步会话。
            ref_id: 引用 ID。
            status: 新状态（如 "removed"）。
        """
        await session.execute(
            sa.update(WorkspaceEvidenceRef)
            .where(WorkspaceEvidenceRef.id == ref_id)
            .values(status=status)
        )

    @staticmethod
    async def count_active_evidence_refs(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """统计工作空间的活跃证据引用数。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            int: 活跃证据引用数。
        """
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(WorkspaceEvidenceRef)
            .where(
                WorkspaceEvidenceRef.workspace_id == workspace_id,
                WorkspaceEvidenceRef.status == "active",
            )
        )
        return int(result.scalar() or 0)


class SnapshotRepository:
    """证据快照数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    @staticmethod
    async def insert_snapshot(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        snapshot_number: int,
        content_hash: str,
        permission_envelope: dict[str, Any],
        field_manifest: dict[str, Any],
        source_refs: list[Any],
        created_by: UUID,
    ) -> ResearchEvidenceSnapshot:
        """插入证据快照，返回 ORM 实体。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            snapshot_number: 快照编号。
            content_hash: 内容哈希（SHA-256）。
            permission_envelope: 权限包络。
            field_manifest: 字段清单。
            source_refs: 源引用列表。
            created_by: 创建人 ID。

        Returns:
            ResearchEvidenceSnapshot: 快照 ORM 实体。
        """
        snapshot = ResearchEvidenceSnapshot(
            id=new_id(),
            workspace_id=workspace_id,
            snapshot_number=snapshot_number,
            content_hash=content_hash,
            permission_envelope=permission_envelope,
            field_manifest=field_manifest,
            source_refs=source_refs,
            created_by=created_by,
        )
        session.add(snapshot)
        await session.flush()
        return snapshot

    @staticmethod
    async def list_snapshots(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchEvidenceSnapshot]:
        """列出工作空间的全部快照（按编号降序）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchEvidenceSnapshot]: 快照列表。
        """
        result = await session.execute(
            sa.select(ResearchEvidenceSnapshot)
            .where(ResearchEvidenceSnapshot.workspace_id == workspace_id)
            .order_by(ResearchEvidenceSnapshot.snapshot_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_snapshot(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> ResearchEvidenceSnapshot | None:
        """获取工作空间的最新快照。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            ResearchEvidenceSnapshot | None: 最新快照，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchEvidenceSnapshot)
            .where(ResearchEvidenceSnapshot.workspace_id == workspace_id)
            .order_by(ResearchEvidenceSnapshot.snapshot_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
