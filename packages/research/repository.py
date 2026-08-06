"""研究域数据访问层。

ResearchRepository 封装所有研究域相关的数据库操作：
- 工作空间的插入、查询（按 owner 过滤）、列表（keyset 分页）、状态更新、删除；
- 研究问题版本的插入、最新版本查询、版本列表；
- 证据引用的插入、列表、状态更新、计数；
- 证据快照的插入、列表、最新快照查询。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 WorkspaceService / EvidenceSnapshotService 通过 ScopedSessionMixin 管理。

参照 packages/facts/repository.py 的模式。
"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import (
    ResearchDerivedDataset,
    ResearchDerivedDatasetVersion,
    ResearchEvidenceSnapshot,
    ResearchInsight,
    ResearchInsightCandidate,
    ResearchInsightVersion,
    ResearchKnowledgeReference,
    ResearchLineageEdge,
    ResearchQuestionVersion,
    ResearchResult,
    ResearchResultAclRevision,
    ResearchResultFavorite,
    ResearchResultVersion,
    ResearchView,
    ResearchViewVersion,
    ResearchWorkspace,
    WorkspaceEvidenceRef,
)


def _encode_cursor(created_at: datetime, entity_id: UUID) -> str:
    """编码 keyset 分页游标。

    Args:
        created_at: 排序时间戳。
        entity_id: 唯一决胜键。

    Returns:
        str: base64url 编码的游标字符串。
    """
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(entity_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Args:
        cursor: base64url 编码的游标字符串。

    Returns:
        tuple[datetime, UUID]: (排序时间戳, 实体 ID)。

    Raises:
        ValueError: 当游标格式不合法时。
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"无效的游标编码: {cursor}") from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"无效的游标 JSON: {cursor}") from exc

    if not isinstance(payload, dict) or "v" not in payload or "id" not in payload:
        raise ValueError(f"游标缺少必要字段 v / id: {cursor}")

    try:
        created_at = datetime.fromisoformat(str(payload["v"]))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"游标 v 字段不是合法 ISO 时间: {payload['v']}") from exc

    try:
        entity_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"游标 id 字段不是合法 UUID: {payload['id']}") from exc

    return created_at, entity_id


class ResearchRepository:
    """研究域数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    # ---- 工作空间 ----

    @staticmethod
    async def insert_workspace(
        session: AsyncSession,
        *,
        owner_user_id: UUID,
        department_id: UUID,
        name: str,
        status: str = "draft",
        forked_from_id: UUID | None = None,
    ) -> ResearchWorkspace:
        """插入工作空间行，返回 ORM 实体。

        Args:
            session: 异步会话。
            owner_user_id: 所有者用户 ID。
            department_id: 部门 ID。
            name: 工作空间名称。
            status: 状态（默认 draft）。
            forked_from_id: 分叉来源 ID（可选）。

        Returns:
            ResearchWorkspace: 工作空间 ORM 实体。
        """
        workspace = ResearchWorkspace(
            id=new_id(),
            owner_user_id=owner_user_id,
            department_id=department_id,
            name=name,
            status=status,
            current_question_version=0,
            forked_from_id=forked_from_id,
            lock_version=0,
        )
        session.add(workspace)
        await session.flush()
        return workspace

    @staticmethod
    async def get_workspace(
        session: AsyncSession,
        workspace_id: UUID,
        owner_user_id: UUID,
    ) -> ResearchWorkspace | None:
        """获取工作空间并校验所有者归属。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID（用于过滤）。

        Returns:
            ResearchWorkspace | None: 工作空间实体，不存在或不属于该用户时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchWorkspace).where(
                ResearchWorkspace.id == workspace_id,
                ResearchWorkspace.owner_user_id == owner_user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_workspaces(
        session: AsyncSession,
        owner_user_id: UUID,
        status: str | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[ResearchWorkspace], str | None]:
        """分页列出工作空间（按 owner 过滤，keyset 分页）。

        Args:
            session: 异步会话。
            owner_user_id: 所有者用户 ID。
            status: 状态过滤（可选）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[ResearchWorkspace], str | None]:
            (工作空间列表, 下一页游标)。
        """
        effective_size = min(max(page_size, 1), 100)
        fetch_limit = effective_size + 1

        stmt = (
            sa.select(ResearchWorkspace)
            .where(ResearchWorkspace.owner_user_id == owner_user_id)
            .order_by(
                ResearchWorkspace.updated_at.desc(),
                ResearchWorkspace.id.desc(),
            )
            .limit(fetch_limit)
        )

        if status is not None:
            stmt = stmt.where(ResearchWorkspace.status == status)

        if cursor is not None:
            cursor_updated_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                sa.or_(
                    ResearchWorkspace.updated_at < cursor_updated_at,
                    sa.and_(
                        ResearchWorkspace.updated_at == cursor_updated_at,
                        ResearchWorkspace.id < cursor_id,
                    ),
                )
            )

        result = await session.execute(stmt)
        rows = result.scalars().all()

        items = list(rows[:effective_size])
        next_cursor: str | None = None
        if len(rows) > effective_size and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.updated_at, last.id)

        return items, next_cursor

    @staticmethod
    async def update_workspace_status(
        session: AsyncSession,
        workspace_id: UUID,
        status: str,
    ) -> None:
        """更新工作空间状态。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            status: 新状态。
        """
        await session.execute(
            sa.update(ResearchWorkspace)
            .where(ResearchWorkspace.id == workspace_id)
            .values(status=status, updated_at=sa.func.now())
        )

    @staticmethod
    async def update_workspace_name(
        session: AsyncSession,
        workspace_id: UUID,
        name: str,
    ) -> None:
        """更新工作空间名称。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            name: 新名称。
        """
        await session.execute(
            sa.update(ResearchWorkspace)
            .where(ResearchWorkspace.id == workspace_id)
            .values(name=name, updated_at=sa.func.now())
        )

    @staticmethod
    async def update_workspace_current_version(
        session: AsyncSession,
        workspace_id: UUID,
        version_number: int,
    ) -> None:
        """更新工作空间的当前问题版本号。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchWorkspace)
            .where(ResearchWorkspace.id == workspace_id)
            .values(current_question_version=version_number, updated_at=sa.func.now())
        )

    @staticmethod
    async def delete_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> None:
        """物理删除工作空间（CASCADE 级联删除子表）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
        """
        await session.execute(
            sa.delete(ResearchWorkspace).where(ResearchWorkspace.id == workspace_id)
        )

    # ---- 研究问题版本 ----

    @staticmethod
    async def insert_question_version(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        version_number: int,
        question_text: str,
        sub_questions: list[str] | None = None,
        created_by: UUID,
    ) -> ResearchQuestionVersion:
        """插入研究问题版本，返回 ORM 实体。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            version_number: 版本号。
            question_text: 主研究问题文本。
            sub_questions: 子问题列表。
            created_by: 创建人 ID。

        Returns:
            ResearchQuestionVersion: 问题版本 ORM 实体。
        """
        version = ResearchQuestionVersion(
            id=new_id(),
            workspace_id=workspace_id,
            version_number=version_number,
            question_text=question_text,
            sub_questions=sub_questions if sub_questions is not None else [],
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_latest_question_version(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> ResearchQuestionVersion | None:
        """获取工作空间的最新问题版本。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            ResearchQuestionVersion | None: 最新版本，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchQuestionVersion)
            .where(ResearchQuestionVersion.workspace_id == workspace_id)
            .order_by(ResearchQuestionVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_question_versions(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchQuestionVersion]:
        """列出工作空间的全部问题版本（按版本号降序）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchQuestionVersion]: 版本列表。
        """
        result = await session.execute(
            sa.select(ResearchQuestionVersion)
            .where(ResearchQuestionVersion.workspace_id == workspace_id)
            .order_by(ResearchQuestionVersion.version_number.desc())
        )
        return list(result.scalars().all())

    # ---- 证据引用 ----

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

    # ---- 证据快照 ----

    @staticmethod
    async def insert_snapshot(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        snapshot_number: int,
        content_hash: str,
        permission_envelope: dict,
        field_manifest: dict,
        source_refs: list,
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

    # ============================================================
    # 阶段 3：研究产物 CRUD
    # ============================================================

    # ---- DerivedDataset ----

    @staticmethod
    async def insert_dataset(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        name: str,
        summary: str | None = None,
        tags: list[str] | None = None,
        status: str = "confirmed",
        source_run_id: UUID,
        source_snapshot_id: UUID | None = None,
    ) -> ResearchDerivedDataset:
        """插入衍生数据集稳定身份行。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID。
            name: 名称。
            summary: 摘要（可选）。
            tags: 标签列表（可选）。
            status: 状态（默认 confirmed）。
            source_run_id: 来源 Run ID。
            source_snapshot_id: 来源快照 ID（可选）。

        Returns:
            ResearchDerivedDataset: 数据集 ORM 实体。
        """
        dataset = ResearchDerivedDataset(
            id=new_id(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=name,
            summary=summary,
            tags=tags if tags is not None else [],
            status=status,
            current_version=0,
            source_run_id=source_run_id,
            source_snapshot_id=source_snapshot_id,
            lock_version=0,
        )
        session.add(dataset)
        await session.flush()
        return dataset

    @staticmethod
    async def get_dataset(
        session: AsyncSession,
        dataset_id: UUID,
        workspace_id: UUID | None = None,
    ) -> ResearchDerivedDataset | None:
        """获取数据集（可选校验 workspace 归属）。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            workspace_id: 工作空间 ID（可选过滤）。

        Returns:
            ResearchDerivedDataset | None: 数据集实体。
        """
        stmt = sa.select(ResearchDerivedDataset).where(
            ResearchDerivedDataset.id == dataset_id
        )
        if workspace_id is not None:
            stmt = stmt.where(ResearchDerivedDataset.workspace_id == workspace_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_datasets(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchDerivedDataset]:
        """列出工作空间内的数据集。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchDerivedDataset]: 数据集列表。
        """
        result = await session.execute(
            sa.select(ResearchDerivedDataset)
            .where(ResearchDerivedDataset.workspace_id == workspace_id)
            .order_by(ResearchDerivedDataset.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_dataset_metadata(
        session: AsyncSession,
        dataset_id: UUID,
        *,
        name: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """更新数据集元数据（仅 stable identity 字段）。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            name: 新名称（可选，None 表示不更新）。
            summary: 新摘要（可选）。
            tags: 新标签列表（可选）。
        """
        values: dict = {"updated_at": sa.func.now()}
        if name is not None:
            values["name"] = name
        if summary is not None:
            values["summary"] = summary
        if tags is not None:
            values["tags"] = tags
        await session.execute(
            sa.update(ResearchDerivedDataset)
            .where(ResearchDerivedDataset.id == dataset_id)
            .values(**values)
        )

    @staticmethod
    async def update_dataset_current_version(
        session: AsyncSession,
        dataset_id: UUID,
        version_number: int,
    ) -> None:
        """更新数据集当前版本号。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchDerivedDataset)
            .where(ResearchDerivedDataset.id == dataset_id)
            .values(current_version=version_number, updated_at=sa.func.now())
        )

    @staticmethod
    async def search_derived_datasets(
        session: AsyncSession,
        owner_user_id: UUID,
        query: str | None = None,
        workspace_id: UUID | None = None,
    ) -> list[ResearchDerivedDataset]:
        """搜索当前用户已确认的 DerivedDataset（跨 Workspace）。

        Args:
            session: 异步会话。
            owner_user_id: 所有者用户 ID（过滤条件）。
            query: 关键词搜索（name ILIKE，可选）。
            workspace_id: 工作空间筛选（可选）。

        Returns:
            list[ResearchDerivedDataset]: 搜索结果列表。
        """
        stmt = sa.select(ResearchDerivedDataset).where(
            ResearchDerivedDataset.owner_user_id == owner_user_id,
            ResearchDerivedDataset.status == "confirmed",
        )
        if query:
            stmt = stmt.where(ResearchDerivedDataset.name.ilike(f"%{query}%"))
        if workspace_id is not None:
            stmt = stmt.where(ResearchDerivedDataset.workspace_id == workspace_id)
        stmt = stmt.order_by(ResearchDerivedDataset.updated_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ---- DerivedDatasetVersion（不可变）----

    @staticmethod
    async def insert_dataset_version(
        session: AsyncSession,
        *,
        dataset_id: UUID,
        version_number: int,
        metadata_content: dict,
        points_content: list,
        series_content: list,
        field_manifest: list,
        source_run_id: UUID,
        source_step_id: UUID | None = None,
        source_artifact_id: UUID | None = None,
        content_hash: str,
        created_by: UUID,
    ) -> ResearchDerivedDatasetVersion:
        """插入数据集版本（不可变）。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            version_number: 版本号。
            metadata_content: 报告级描述。
            points_content: 独立单值指标列表。
            series_content: 普通表格/时间序列列表。
            field_manifest: 字段清单。
            source_run_id: 来源 Run ID。
            source_step_id: 来源步骤 ID（可选）。
            source_artifact_id: 来源工件 ID（可选）。
            content_hash: 内容哈希。
            created_by: 创建人 ID。

        Returns:
            ResearchDerivedDatasetVersion: 版本 ORM 实体。
        """
        version = ResearchDerivedDatasetVersion(
            id=new_id(),
            dataset_id=dataset_id,
            version_number=version_number,
            metadata_content=metadata_content,
            points_content=points_content,
            series_content=series_content,
            field_manifest=field_manifest,
            source_run_id=source_run_id,
            source_step_id=source_step_id,
            source_artifact_id=source_artifact_id,
            content_hash=content_hash,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_dataset_version(
        session: AsyncSession,
        dataset_id: UUID,
        version_number: int,
    ) -> ResearchDerivedDatasetVersion | None:
        """获取数据集版本。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            version_number: 版本号。

        Returns:
            ResearchDerivedDatasetVersion | None: 版本实体。
        """
        result = await session.execute(
            sa.select(ResearchDerivedDatasetVersion).where(
                ResearchDerivedDatasetVersion.dataset_id == dataset_id,
                ResearchDerivedDatasetVersion.version_number == version_number,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_dataset_versions(
        session: AsyncSession,
        dataset_id: UUID,
    ) -> list[ResearchDerivedDatasetVersion]:
        """列出数据集的全部版本（按版本号降序）。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。

        Returns:
            list[ResearchDerivedDatasetVersion]: 版本列表。
        """
        result = await session.execute(
            sa.select(ResearchDerivedDatasetVersion)
            .where(ResearchDerivedDatasetVersion.dataset_id == dataset_id)
            .order_by(ResearchDerivedDatasetVersion.version_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_dataset_version(
        session: AsyncSession,
        dataset_id: UUID,
    ) -> ResearchDerivedDatasetVersion | None:
        """获取数据集的最新版本。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。

        Returns:
            ResearchDerivedDatasetVersion | None: 最新版本。
        """
        result = await session.execute(
            sa.select(ResearchDerivedDatasetVersion)
            .where(ResearchDerivedDatasetVersion.dataset_id == dataset_id)
            .order_by(ResearchDerivedDatasetVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ---- ResearchView ----

    @staticmethod
    async def insert_view(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        name: str,
        caption: str | None = None,
        display_order: int = 0,
        status: str = "confirmed",
        source_run_id: UUID,
    ) -> ResearchView:
        """插入研究视图稳定身份行。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID。
            name: 名称。
            caption: 图注（可选）。
            display_order: 展示顺序。
            status: 状态。
            source_run_id: 来源 Run ID。

        Returns:
            ResearchView: 视图 ORM 实体。
        """
        view = ResearchView(
            id=new_id(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=name,
            caption=caption,
            display_order=display_order,
            status=status,
            current_version=0,
            source_run_id=source_run_id,
            lock_version=0,
        )
        session.add(view)
        await session.flush()
        return view

    @staticmethod
    async def get_view(
        session: AsyncSession,
        view_id: UUID,
        workspace_id: UUID | None = None,
    ) -> ResearchView | None:
        """获取视图（可选校验 workspace 归属）。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            workspace_id: 工作空间 ID（可选过滤）。

        Returns:
            ResearchView | None: 视图实体。
        """
        stmt = sa.select(ResearchView).where(ResearchView.id == view_id)
        if workspace_id is not None:
            stmt = stmt.where(ResearchView.workspace_id == workspace_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_views(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchView]:
        """列出工作空间内的视图。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchView]: 视图列表。
        """
        result = await session.execute(
            sa.select(ResearchView)
            .where(ResearchView.workspace_id == workspace_id)
            .order_by(ResearchView.display_order.asc(), ResearchView.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_view_metadata(
        session: AsyncSession,
        view_id: UUID,
        *,
        name: str | None = None,
        caption: str | None = None,
        display_order: int | None = None,
    ) -> None:
        """更新视图元数据（仅 stable identity 字段）。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            name: 新名称（可选）。
            caption: 新图注（可选）。
            display_order: 新展示顺序（可选）。
        """
        values: dict = {"updated_at": sa.func.now()}
        if name is not None:
            values["name"] = name
        if caption is not None:
            values["caption"] = caption
        if display_order is not None:
            values["display_order"] = display_order
        await session.execute(
            sa.update(ResearchView).where(ResearchView.id == view_id).values(**values)
        )

    @staticmethod
    async def update_view_current_version(
        session: AsyncSession,
        view_id: UUID,
        version_number: int,
    ) -> None:
        """更新视图当前版本号。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchView)
            .where(ResearchView.id == view_id)
            .values(current_version=version_number, updated_at=sa.func.now())
        )

    # ---- ResearchViewVersion（不可变）----

    @staticmethod
    async def insert_view_version(
        session: AsyncSession,
        *,
        view_id: UUID,
        version_number: int,
        image_storage_path: str,
        image_format: str = "png",
        image_width: int | None = None,
        image_height: int | None = None,
        image_content_hash: str,
        chart_code_artifact_id: UUID | None = None,
        image_digest: str | None = None,
        source_run_id: UUID,
        source_step_id: UUID | None = None,
        source_artifact_id: UUID | None = None,
        bound_dataset_version_id: UUID | None = None,
        chart_description: str | None = None,
        created_by: UUID,
    ) -> ResearchViewVersion:
        """插入视图版本（不可变）。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            version_number: 版本号。
            image_storage_path: 图片存储路径。
            image_format: 图片格式（png/pdf）。
            image_width / image_height: 图片尺寸（可选）。
            image_content_hash: 图片内容哈希。
            chart_code_artifact_id: 绘图代码工件 ID（可选）。
            image_digest: 沙箱镜像 digest（可选）。
            source_run_id: 来源 Run ID。
            source_step_id: 来源步骤 ID（可选）。
            source_artifact_id: 来源工件 ID（可选）。
            bound_dataset_version_id: 绑定数据集版本 ID（可选）。
            chart_description: 图表说明（可选）。
            created_by: 创建人 ID。

        Returns:
            ResearchViewVersion: 版本 ORM 实体。
        """
        version = ResearchViewVersion(
            id=new_id(),
            view_id=view_id,
            version_number=version_number,
            image_storage_path=image_storage_path,
            image_format=image_format,
            image_width=image_width,
            image_height=image_height,
            image_content_hash=image_content_hash,
            chart_code_artifact_id=chart_code_artifact_id,
            image_digest=image_digest,
            source_run_id=source_run_id,
            source_step_id=source_step_id,
            source_artifact_id=source_artifact_id,
            bound_dataset_version_id=bound_dataset_version_id,
            chart_description=chart_description,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_view_version(
        session: AsyncSession,
        view_id: UUID,
        version_number: int,
    ) -> ResearchViewVersion | None:
        """获取视图版本。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            version_number: 版本号。

        Returns:
            ResearchViewVersion | None: 版本实体。
        """
        result = await session.execute(
            sa.select(ResearchViewVersion).where(
                ResearchViewVersion.view_id == view_id,
                ResearchViewVersion.version_number == version_number,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_view_versions(
        session: AsyncSession,
        view_id: UUID,
    ) -> list[ResearchViewVersion]:
        """列出视图的全部版本（按版本号降序）。

        Args:
            session: 异步会话。
            view_id: 视图 ID。

        Returns:
            list[ResearchViewVersion]: 版本列表。
        """
        result = await session.execute(
            sa.select(ResearchViewVersion)
            .where(ResearchViewVersion.view_id == view_id)
            .order_by(ResearchViewVersion.version_number.desc())
        )
        return list(result.scalars().all())

    # ---- ResearchInsight ----

    @staticmethod
    async def insert_insight(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        name: str,
        status: str = "confirmed",
        source_run_id: UUID | None = None,
    ) -> ResearchInsight:
        """插入 Insight 稳定身份行。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID。
            name: 名称。
            status: 状态。
            source_run_id: 来源 Run ID（可选）。

        Returns:
            ResearchInsight: Insight ORM 实体。
        """
        insight = ResearchInsight(
            id=new_id(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=name,
            status=status,
            current_version=0,
            source_run_id=source_run_id,
            lock_version=0,
        )
        session.add(insight)
        await session.flush()
        return insight

    @staticmethod
    async def get_insight(
        session: AsyncSession,
        insight_id: UUID,
        workspace_id: UUID | None = None,
    ) -> ResearchInsight | None:
        """获取 Insight（可选校验 workspace 归属）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            workspace_id: 工作空间 ID（可选过滤）。

        Returns:
            ResearchInsight | None: Insight 实体。
        """
        stmt = sa.select(ResearchInsight).where(ResearchInsight.id == insight_id)
        if workspace_id is not None:
            stmt = stmt.where(ResearchInsight.workspace_id == workspace_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_insights(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchInsight]:
        """列出工作空间内的 Insight。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchInsight]: Insight 列表。
        """
        result = await session.execute(
            sa.select(ResearchInsight)
            .where(ResearchInsight.workspace_id == workspace_id)
            .order_by(ResearchInsight.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_insight_metadata(
        session: AsyncSession,
        insight_id: UUID,
        *,
        name: str | None = None,
    ) -> None:
        """更新 Insight 元数据（仅 name）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            name: 新名称（可选）。
        """
        values: dict = {"updated_at": sa.func.now()}
        if name is not None:
            values["name"] = name
        await session.execute(
            sa.update(ResearchInsight).where(ResearchInsight.id == insight_id).values(**values)
        )

    @staticmethod
    async def update_insight_current_version(
        session: AsyncSession,
        insight_id: UUID,
        version_number: int,
    ) -> None:
        """更新 Insight 当前版本号。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchInsight)
            .where(ResearchInsight.id == insight_id)
            .values(current_version=version_number, updated_at=sa.func.now())
        )

    # ---- ResearchInsightVersion（不可变）----

    @staticmethod
    async def insert_insight_version(
        session: AsyncSession,
        *,
        insight_id: UUID,
        version_number: int,
        conclusion: str,
        scope: str,
        evidence_refs: list,
        method_refs: list,
        confidence_level: str,
        limitations: str,
        evidence_source_label: str,
        ai_original_text: str | None = None,
        is_modified: bool = False,
        modification_note: str | None = None,
        source_candidate_id: UUID | None = None,
        source_run_id: UUID | None = None,
        created_by: UUID,
    ) -> ResearchInsightVersion:
        """插入 Insight 版本（不可变）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 版本号。
            conclusion: 结论。
            scope: 适用范围。
            evidence_refs: 证据引用列表。
            method_refs: 方法引用列表。
            confidence_level: 置信说明。
            limitations: 限制条件。
            evidence_source_label: 证据来源标签。
            ai_original_text: AI 原稿（可选）。
            is_modified: 是否被修改。
            modification_note: 修改原因（可选）。
            source_candidate_id: 来源候选 ID（可选）。
            source_run_id: 来源 Run ID（可选）。
            created_by: 创建人 ID。

        Returns:
            ResearchInsightVersion: 版本 ORM 实体。
        """
        version = ResearchInsightVersion(
            id=new_id(),
            insight_id=insight_id,
            version_number=version_number,
            conclusion=conclusion,
            scope=scope,
            evidence_refs=evidence_refs,
            method_refs=method_refs,
            confidence_level=confidence_level,
            limitations=limitations,
            evidence_source_label=evidence_source_label,
            ai_original_text=ai_original_text,
            is_modified=is_modified,
            modification_note=modification_note,
            source_candidate_id=source_candidate_id,
            source_run_id=source_run_id,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_insight_version(
        session: AsyncSession,
        insight_id: UUID,
        version_number: int,
    ) -> ResearchInsightVersion | None:
        """获取 Insight 版本。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 版本号。

        Returns:
            ResearchInsightVersion | None: 版本实体。
        """
        result = await session.execute(
            sa.select(ResearchInsightVersion).where(
                ResearchInsightVersion.insight_id == insight_id,
                ResearchInsightVersion.version_number == version_number,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_insight_versions(
        session: AsyncSession,
        insight_id: UUID,
    ) -> list[ResearchInsightVersion]:
        """列出 Insight 的全部版本（按版本号降序）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。

        Returns:
            list[ResearchInsightVersion]: 版本列表。
        """
        result = await session.execute(
            sa.select(ResearchInsightVersion)
            .where(ResearchInsightVersion.insight_id == insight_id)
            .order_by(ResearchInsightVersion.version_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_insight_version(
        session: AsyncSession,
        insight_id: UUID,
    ) -> ResearchInsightVersion | None:
        """获取 Insight 的最新版本。

        Args:
            session: 异步会话。
            insight_id: Insight ID。

        Returns:
            ResearchInsightVersion | None: 最新版本。
        """
        result = await session.execute(
            sa.select(ResearchInsightVersion)
            .where(ResearchInsightVersion.insight_id == insight_id)
            .order_by(ResearchInsightVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ---- ResearchInsightCandidate ----

    @staticmethod
    async def insert_insight_candidate(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        run_id: UUID,
        step_id: UUID | None = None,
        conclusion: str,
        scope: str,
        evidence_refs: list,
        method_refs: list,
        confidence_level: str,
        limitations: str,
        evidence_source_label: str,
        ai_raw_text: str,
        status: str = "pending",
    ) -> ResearchInsightCandidate:
        """插入 Insight 候选。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            run_id: Run ID。
            step_id: 步骤 ID（可选）。
            conclusion: 结论。
            scope: 适用范围。
            evidence_refs: 证据引用列表。
            method_refs: 方法引用列表。
            confidence_level: 置信说明。
            limitations: 限制条件。
            evidence_source_label: 证据来源标签。
            ai_raw_text: AI 原始回答文本。
            status: 状态（默认 pending）。

        Returns:
            ResearchInsightCandidate: 候选 ORM 实体。
        """
        candidate = ResearchInsightCandidate(
            id=new_id(),
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            conclusion=conclusion,
            scope=scope,
            evidence_refs=evidence_refs,
            method_refs=method_refs,
            confidence_level=confidence_level,
            limitations=limitations,
            evidence_source_label=evidence_source_label,
            ai_raw_text=ai_raw_text,
            status=status,
        )
        session.add(candidate)
        await session.flush()
        return candidate

    @staticmethod
    async def get_insight_candidate(
        session: AsyncSession,
        candidate_id: UUID,
    ) -> ResearchInsightCandidate | None:
        """获取 Insight 候选。

        Args:
            session: 异步会话。
            candidate_id: 候选 ID。

        Returns:
            ResearchInsightCandidate | None: 候选实体。
        """
        result = await session.execute(
            sa.select(ResearchInsightCandidate).where(
                ResearchInsightCandidate.id == candidate_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_insight_candidates(
        session: AsyncSession,
        run_id: UUID,
        status: str | None = None,
    ) -> list[ResearchInsightCandidate]:
        """列出 Run 的 Insight 候选。

        Args:
            session: 异步会话。
            run_id: Run ID。
            status: 状态过滤（可选）。

        Returns:
            list[ResearchInsightCandidate]: 候选列表。
        """
        stmt = sa.select(ResearchInsightCandidate).where(
            ResearchInsightCandidate.run_id == run_id
        )
        if status is not None:
            stmt = stmt.where(ResearchInsightCandidate.status == status)
        stmt = stmt.order_by(ResearchInsightCandidate.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_insight_candidate_status(
        session: AsyncSession,
        candidate_id: UUID,
        status: str,
        *,
        accepted_insight_id: UUID | None = None,
        rejection_reason: str | None = None,
        reviewed_by: UUID | None = None,
    ) -> None:
        """更新 Insight 候选状态。

        Args:
            session: 异步会话。
            candidate_id: 候选 ID。
            status: 新状态。
            accepted_insight_id: 接受后创建的 Insight ID（可选）。
            rejection_reason: 拒绝原因（可选）。
            reviewed_by: 审核人 ID（可选）。
        """
        values: dict = {
            "status": status,
            "reviewed_at": sa.func.now(),
        }
        if accepted_insight_id is not None:
            values["accepted_insight_id"] = accepted_insight_id
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        if reviewed_by is not None:
            values["reviewed_by"] = reviewed_by
        await session.execute(
            sa.update(ResearchInsightCandidate)
            .where(ResearchInsightCandidate.id == candidate_id)
            .values(**values)
        )

    # ============================================================
    # 阶段 4：成果包 CRUD（research_result）
    # ============================================================

    # ---- ResearchResult ----

    @staticmethod
    async def insert_result(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        name: str,
        status: str = "published",
        current_version: int = 0,
        current_acl_type: str = "private",
        current_explicit_user_ids: list | None = None,
    ) -> ResearchResult:
        """插入成果包稳定身份行。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID。
            name: 成果包名称。
            status: 状态（默认 published）。
            current_version: 当前版本号（默认 0）。
            current_acl_type: 当前 ACL 类型（默认 private）。
            current_explicit_user_ids: 指定用户列表（可选）。

        Returns:
            ResearchResult: 成果包 ORM 实体。
        """
        result = ResearchResult(
            id=new_id(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=name,
            status=status,
            current_version=current_version,
            current_acl_type=current_acl_type,
            current_explicit_user_ids=current_explicit_user_ids
            if current_explicit_user_ids is not None
            else [],
            lock_version=0,
        )
        session.add(result)
        await session.flush()
        return result

    @staticmethod
    async def get_result(
        session: AsyncSession,
        result_id: UUID,
    ) -> ResearchResult | None:
        """获取成果包（无 owner 过滤，用于跨用户查询）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            ResearchResult | None: 成果包实体。
        """
        res = await session.execute(
            sa.select(ResearchResult).where(ResearchResult.id == result_id)
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def get_result_by_owner(
        session: AsyncSession,
        result_id: UUID,
        owner_user_id: UUID,
    ) -> ResearchResult | None:
        """获取成果包并校验所有者归属。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            owner_user_id: 所有者用户 ID。

        Returns:
            ResearchResult | None: 成果包实体。
        """
        res = await session.execute(
            sa.select(ResearchResult).where(
                ResearchResult.id == result_id,
                ResearchResult.owner_user_id == owner_user_id,
            )
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_results_by_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchResult]:
        """列出工作空间内的全部成果包。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchResult]: 成果包列表。
        """
        res = await session.execute(
            sa.select(ResearchResult)
            .where(ResearchResult.workspace_id == workspace_id)
            .order_by(ResearchResult.created_at.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def update_result_current_version(
        session: AsyncSession,
        result_id: UUID,
        version_number: int,
    ) -> None:
        """更新成果包当前版本号。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(
                current_version=version_number,
                updated_at=sa.func.now(),
            )
        )

    @staticmethod
    async def update_result_acl(
        session: AsyncSession,
        result_id: UUID,
        acl_type: str,
        explicit_user_ids: list,
    ) -> None:
        """更新成果包当前 ACL。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            acl_type: 新 ACL 类型。
            explicit_user_ids: 指定用户列表。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(
                current_acl_type=acl_type,
                current_explicit_user_ids=explicit_user_ids,
                updated_at=sa.func.now(),
            )
        )

    @staticmethod
    async def update_result_metadata(
        session: AsyncSession,
        result_id: UUID,
        name: str,
    ) -> None:
        """更新成果包元数据（仅 name）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            name: 新名称。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(name=name, updated_at=sa.func.now())
        )

    @staticmethod
    async def update_result_status(
        session: AsyncSession,
        result_id: UUID,
        status: str,
    ) -> None:
        """更新成果包状态。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            status: 新状态。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(status=status, updated_at=sa.func.now())
        )

    @staticmethod
    async def list_published_results(
        session: AsyncSession,
    ) -> list[ResearchResult]:
        """列出全部已发布成果包（跨用户，ACL 过滤由 Service 层处理）。

        Args:
            session: 异步会话。

        Returns:
            list[ResearchResult]: 已发布成果包列表。
        """
        res = await session.execute(
            sa.select(ResearchResult)
            .where(ResearchResult.status == "published")
            .order_by(ResearchResult.updated_at.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def count_published_results_by_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """统计工作空间内已发布成果包数量（用于删除检查）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            int: 已发布成果包数量。
        """
        res = await session.execute(
            sa.select(sa.func.count())
            .select_from(ResearchResult)
            .where(
                ResearchResult.workspace_id == workspace_id,
                ResearchResult.status == "published",
            )
        )
        return int(res.scalar() or 0)

    # ---- ResearchResultVersion（不可变）----

    @staticmethod
    async def insert_result_version(
        session: AsyncSession,
        *,
        result_id: UUID,
        version_number: int,
        title: str,
        summary: str | None,
        tags: list,
        release_notes: str | None,
        dataset_version_refs: list,
        view_version_refs: list,
        insight_version_refs: list,
        evidence_snapshot_ids: list,
        analysis_run_ids: list,
        source_run_statuses: dict,
        publisher: UUID,
        content_hash: str,
        published_permission_envelope: dict,
        status: str = "active",
    ) -> ResearchResultVersion:
        """插入成果包版本（不可变）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            version_number: 版本号。
            title: 标题。
            summary: 摘要。
            tags: 标签列表。
            release_notes: 发布说明。
            dataset_version_refs: DerivedDataset 版本引用列表。
            view_version_refs: ResearchView 版本引用列表。
            insight_version_refs: Insight 版本引用列表。
            evidence_snapshot_ids: Evidence Snapshot ID 列表。
            analysis_run_ids: Analysis Run ID 列表。
            source_run_statuses: Run 状态映射。
            publisher: 发布者 ID。
            content_hash: 内容哈希。
            published_permission_envelope: 权限包络快照。
            status: 版本状态（默认 active）。

        Returns:
            ResearchResultVersion: 版本 ORM 实体。
        """
        version = ResearchResultVersion(
            id=new_id(),
            result_id=result_id,
            version_number=version_number,
            title=title,
            summary=summary,
            tags=tags,
            release_notes=release_notes,
            dataset_version_refs=dataset_version_refs,
            view_version_refs=view_version_refs,
            insight_version_refs=insight_version_refs,
            evidence_snapshot_ids=evidence_snapshot_ids,
            analysis_run_ids=analysis_run_ids,
            source_run_statuses=source_run_statuses,
            publisher=publisher,
            content_hash=content_hash,
            published_permission_envelope=published_permission_envelope,
            status=status,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_result_version(
        session: AsyncSession,
        result_id: UUID,
        version_number: int,
    ) -> ResearchResultVersion | None:
        """获取成果包版本。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            version_number: 版本号。

        Returns:
            ResearchResultVersion | None: 版本实体。
        """
        res = await session.execute(
            sa.select(ResearchResultVersion).where(
                ResearchResultVersion.result_id == result_id,
                ResearchResultVersion.version_number == version_number,
            )
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def get_latest_result_version(
        session: AsyncSession,
        result_id: UUID,
    ) -> ResearchResultVersion | None:
        """获取成果包最新版本。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            ResearchResultVersion | None: 最新版本。
        """
        res = await session.execute(
            sa.select(ResearchResultVersion)
            .where(ResearchResultVersion.result_id == result_id)
            .order_by(ResearchResultVersion.version_number.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_result_versions(
        session: AsyncSession,
        result_id: UUID,
    ) -> list[ResearchResultVersion]:
        """列出成果包的全部版本（按版本号降序）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            list[ResearchResultVersion]: 版本列表。
        """
        res = await session.execute(
            sa.select(ResearchResultVersion)
            .where(ResearchResultVersion.result_id == result_id)
            .order_by(ResearchResultVersion.version_number.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def update_result_version_status(
        session: AsyncSession,
        version_id: UUID,
        status: str,
    ) -> None:
        """更新版本状态（仅 status 字段，其他字段不可变）。

        Args:
            session: 异步会话。
            version_id: 版本 ID。
            status: 新状态。
        """
        await session.execute(
            sa.update(ResearchResultVersion)
            .where(ResearchResultVersion.id == version_id)
            .values(status=status)
        )

    @staticmethod
    async def search_result_versions(
        session: AsyncSession,
        query: str | None,
        result_ids: list[UUID] | None = None,
    ) -> list[ResearchResultVersion]:
        """搜索成果包版本（关键词匹配 title / summary / tags）。

        Args:
            session: 异步会话。
            query: 关键词（ILIKE 模糊匹配）。
            result_ids: 成果包 ID 过滤列表（可选，用于 ACL 过滤后的二次查询）。

        Returns:
            list[ResearchResultVersion]: 搜索结果列表。
        """
        stmt = sa.select(ResearchResultVersion).where(
            ResearchResultVersion.status == "active"
        )
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                sa.or_(
                    ResearchResultVersion.title.ilike(pattern),
                    ResearchResultVersion.summary.ilike(pattern),
                    ResearchResultVersion.tags.cast(sa.Text).ilike(pattern),
                )
            )
        if result_ids is not None:
            stmt = stmt.where(ResearchResultVersion.result_id.in_(result_ids))
        stmt = stmt.order_by(ResearchResultVersion.published_at.desc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    # ---- ResearchResultAclRevision（仅追加）----

    @staticmethod
    async def insert_acl_revision(
        session: AsyncSession,
        *,
        result_id: UUID,
        revision_number: int,
        acl_type: str,
        explicit_user_ids: list,
        previous_acl_type: str | None = None,
        previous_explicit_user_ids: list | None = None,
        changed_by: UUID,
        change_reason: str | None = None,
        is_declassify: bool = False,
        declassify_reason: str | None = None,
    ) -> ResearchResultAclRevision:
        """插入 ACL 修订记录（仅追加，不可变）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            revision_number: 修订号。
            acl_type: ACL 类型。
            explicit_user_ids: 指定用户列表。
            previous_acl_type: 变更前 ACL 类型。
            previous_explicit_user_ids: 变更前指定用户列表。
            changed_by: 变更者 ID。
            change_reason: 变更原因。
            is_declassify: 是否为 declassify 操作。
            declassify_reason: declassify 理由。

        Returns:
            ResearchResultAclRevision: 修订记录 ORM 实体。
        """
        revision = ResearchResultAclRevision(
            id=new_id(),
            result_id=result_id,
            revision_number=revision_number,
            acl_type=acl_type,
            explicit_user_ids=explicit_user_ids,
            previous_acl_type=previous_acl_type,
            previous_explicit_user_ids=previous_explicit_user_ids,
            changed_by=changed_by,
            change_reason=change_reason,
            is_declassify=is_declassify,
            declassify_reason=declassify_reason,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def get_latest_acl_revision(
        session: AsyncSession,
        result_id: UUID,
    ) -> ResearchResultAclRevision | None:
        """获取成果包最新的 ACL 修订记录。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            ResearchResultAclRevision | None: 最新修订记录。
        """
        res = await session.execute(
            sa.select(ResearchResultAclRevision)
            .where(ResearchResultAclRevision.result_id == result_id)
            .order_by(ResearchResultAclRevision.revision_number.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_acl_revisions(
        session: AsyncSession,
        result_id: UUID,
    ) -> list[ResearchResultAclRevision]:
        """列出成果包的全部 ACL 修订记录（按修订号升序）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            list[ResearchResultAclRevision]: 修订记录列表。
        """
        res = await session.execute(
            sa.select(ResearchResultAclRevision)
            .where(ResearchResultAclRevision.result_id == result_id)
            .order_by(ResearchResultAclRevision.revision_number.asc())
        )
        return list(res.scalars().all())

    # ---- ResearchLineageEdge（仅追加）----

    @staticmethod
    async def insert_lineage_edge(
        session: AsyncSession,
        *,
        source_namespace: str,
        source_id: UUID,
        target_namespace: str,
        target_id: UUID,
        edge_type: str,
        source_version: int | None = None,
        target_version: int | None = None,
    ) -> ResearchLineageEdge:
        """插入溯源边（仅追加，不可变）。

        Args:
            session: 异步会话。
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。
            edge_type: 边类型。
            source_version: 源版本号（可选）。
            target_version: 目标版本号（可选）。

        Returns:
            ResearchLineageEdge: 溯源边 ORM 实体。
        """
        edge = ResearchLineageEdge(
            id=new_id(),
            source_namespace=source_namespace,
            source_id=source_id,
            source_version=source_version,
            target_namespace=target_namespace,
            target_id=target_id,
            target_version=target_version,
            edge_type=edge_type,
        )
        session.add(edge)
        await session.flush()
        return edge

    @staticmethod
    async def list_edges_by_source(
        session: AsyncSession,
        source_namespace: str,
        source_id: UUID,
    ) -> list[ResearchLineageEdge]:
        """按源节点查询溯源边。

        Args:
            session: 异步会话。
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。

        Returns:
            list[ResearchLineageEdge]: 溯源边列表。
        """
        res = await session.execute(
            sa.select(ResearchLineageEdge).where(
                ResearchLineageEdge.source_namespace == source_namespace,
                ResearchLineageEdge.source_id == source_id,
            )
        )
        return list(res.scalars().all())

    @staticmethod
    async def list_edges_by_target(
        session: AsyncSession,
        target_namespace: str,
        target_id: UUID,
    ) -> list[ResearchLineageEdge]:
        """按目标节点查询溯源边。

        Args:
            session: 异步会话。
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。

        Returns:
            list[ResearchLineageEdge]: 溯源边列表。
        """
        res = await session.execute(
            sa.select(ResearchLineageEdge).where(
                ResearchLineageEdge.target_namespace == target_namespace,
                ResearchLineageEdge.target_id == target_id,
            )
        )
        return list(res.scalars().all())

    # ---- ResearchResultFavorite ----

    @staticmethod
    async def insert_favorite(
        session: AsyncSession,
        *,
        result_id: UUID,
        user_id: UUID,
    ) -> ResearchResultFavorite:
        """插入收藏记录。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            user_id: 用户 ID。

        Returns:
            ResearchResultFavorite: 收藏 ORM 实体。
        """
        favorite = ResearchResultFavorite(
            id=new_id(),
            result_id=result_id,
            user_id=user_id,
        )
        session.add(favorite)
        await session.flush()
        return favorite

    @staticmethod
    async def delete_favorite(
        session: AsyncSession,
        result_id: UUID,
        user_id: UUID,
    ) -> None:
        """删除收藏记录。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            user_id: 用户 ID。
        """
        await session.execute(
            sa.delete(ResearchResultFavorite).where(
                ResearchResultFavorite.result_id == result_id,
                ResearchResultFavorite.user_id == user_id,
            )
        )

    @staticmethod
    async def check_favorite(
        session: AsyncSession,
        result_id: UUID,
        user_id: UUID,
    ) -> bool:
        """检查用户是否已收藏成果包。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            user_id: 用户 ID。

        Returns:
            bool: 是否已收藏。
        """
        res = await session.execute(
            sa.select(sa.func.count())
            .select_from(ResearchResultFavorite)
            .where(
                ResearchResultFavorite.result_id == result_id,
                ResearchResultFavorite.user_id == user_id,
            )
        )
        return int(res.scalar() or 0) > 0

    @staticmethod
    async def list_favorites(
        session: AsyncSession,
        user_id: UUID,
    ) -> list[ResearchResultFavorite]:
        """列出用户收藏的全部成果包。

        Args:
            session: 异步会话。
            user_id: 用户 ID。

        Returns:
            list[ResearchResultFavorite]: 收藏列表。
        """
        res = await session.execute(
            sa.select(ResearchResultFavorite)
            .where(ResearchResultFavorite.user_id == user_id)
            .order_by(ResearchResultFavorite.created_at.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def list_favorite_result_ids(
        session: AsyncSession,
        user_id: UUID,
    ) -> list[UUID]:
        """列出用户收藏的成果包 ID 列表。

        Args:
            session: 异步会话。
            user_id: 用户 ID。

        Returns:
            list[UUID]: 成果包 ID 列表。
        """
        res = await session.execute(
            sa.select(ResearchResultFavorite.result_id)
            .where(ResearchResultFavorite.user_id == user_id)
        )
        return [row[0] for row in res.all()]

    # ---- 已发布 DerivedDataset 跨用户搜索 ----

    @staticmethod
    async def search_published_datasets(
        session: AsyncSession,
        query: str | None = None,
        result_id: UUID | None = None,
    ) -> list[tuple[ResearchResultVersion, ResearchResult]]:
        """搜索已发布成果包中的 DerivedDataset（跨用户）。

        查询 status=active 的成果包版本，解析 dataset_version_refs。

        Args:
            session: 异步会话。
            query: 关键词搜索（匹配版本标题/摘要，可选）。
            result_id: 指定成果包 ID 过滤（可选）。

        Returns:
            list[tuple[ResearchResultVersion, ResearchResult]]:
                (版本, 成果包) 元组列表。
        """
        stmt = (
            sa.select(ResearchResultVersion, ResearchResult)
            .join(ResearchResult, ResearchResultVersion.result_id == ResearchResult.id)
            .where(
                ResearchResultVersion.status == "active",
                ResearchResult.status == "published",
            )
        )
        if result_id is not None:
            stmt = stmt.where(ResearchResult.id == result_id)
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                sa.or_(
                    ResearchResultVersion.title.ilike(pattern),
                    ResearchResultVersion.summary.ilike(pattern),
                )
            )
        stmt = stmt.order_by(ResearchResultVersion.published_at.desc())
        res = await session.execute(stmt)
        return list(res.all())

    # ============================================================
    # 阶段 5：知识引用快照 CRUD（research_knowledge_reference）
    # ============================================================

    @staticmethod
    async def insert_knowledge_reference(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        run_id: UUID,
        step_id: UUID | None = None,
        insight_id: UUID | None = None,
        document_id: str,
        document_version: str,
        title: str,
        section: str | None = None,
        page: int | None = None,
        chunk_id: str | None = None,
        snippet_text: str | None = None,
        snippet_storage_path: str | None = None,
        content_hash: str,
        source_uri: str,
        provider_name: str,
        research_question_context: str | None = None,
    ) -> ResearchKnowledgeReference:
        """插入知识引用快照（仅追加，不可变）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            run_id: Run ID。
            step_id: 步骤 ID（可选）。
            insight_id: Insight ID（可选，逻辑引用）。
            document_id: 文档 ID。
            document_version: 文档版本。
            title: 文档标题。
            section: 段落/章节（可选）。
            page: 页码（可选）。
            chunk_id: 分块 ID（可选）。
            snippet_text: 引用段落文本（≤4KB 直接存储，可选）。
            snippet_storage_path: MinIO 存储路径（>4KB 时存储，可选）。
            content_hash: 内容哈希。
            source_uri: 来源 URI。
            provider_name: Provider 名称。
            research_question_context: 研究问题上下文（可选）。

        Returns:
            ResearchKnowledgeReference: 知识引用快照 ORM 实体。
        """
        ref = ResearchKnowledgeReference(
            id=new_id(),
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            insight_id=insight_id,
            document_id=document_id,
            document_version=document_version,
            title=title,
            section=section,
            page=page,
            chunk_id=chunk_id,
            snippet_text=snippet_text,
            snippet_storage_path=snippet_storage_path,
            content_hash=content_hash,
            source_uri=source_uri,
            provider_name=provider_name,
            research_question_context=research_question_context,
        )
        session.add(ref)
        await session.flush()
        return ref

    @staticmethod
    async def get_knowledge_reference(
        session: AsyncSession,
        reference_id: UUID,
    ) -> ResearchKnowledgeReference | None:
        """获取单个知识引用快照。

        Args:
            session: 异步会话。
            reference_id: 引用快照 ID。

        Returns:
            ResearchKnowledgeReference | None: 引用快照实体。
        """
        res = await session.execute(
            sa.select(ResearchKnowledgeReference).where(
                ResearchKnowledgeReference.id == reference_id
            )
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_knowledge_references_by_insight(
        session: AsyncSession,
        insight_id: UUID,
    ) -> list[ResearchKnowledgeReference]:
        """列出 Insight 关联的知识引用快照（按检索时间升序）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。

        Returns:
            list[ResearchKnowledgeReference]: 引用快照列表。
        """
        res = await session.execute(
            sa.select(ResearchKnowledgeReference)
            .where(ResearchKnowledgeReference.insight_id == insight_id)
            .order_by(ResearchKnowledgeReference.retrieval_time.asc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def list_knowledge_references_by_run(
        session: AsyncSession,
        run_id: UUID,
        step_id: UUID | None = None,
    ) -> list[ResearchKnowledgeReference]:
        """按 Run（和可选 Step）查询知识引用快照列表。

        Args:
            session: 异步会话。
            run_id: Run ID。
            step_id: 步骤 ID（可选过滤）。

        Returns:
            list[ResearchKnowledgeReference]: 引用快照列表。
        """
        stmt = sa.select(ResearchKnowledgeReference).where(
            ResearchKnowledgeReference.run_id == run_id
        )
        if step_id is not None:
            stmt = stmt.where(ResearchKnowledgeReference.step_id == step_id)
        stmt = stmt.order_by(ResearchKnowledgeReference.retrieval_time.asc())
        res = await session.execute(stmt)
        return list(res.scalars().all())
