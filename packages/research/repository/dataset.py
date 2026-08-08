"""衍生数据集子仓库。

封装 ResearchDerivedDataset（稳定身份）与 ResearchDerivedDatasetVersion
（不可变版本）的数据库操作：插入、查询、列表、元数据/版本更新、跨 Workspace 搜索、
版本插入/查询/列表。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchDerivedDataset, ResearchDerivedDatasetVersion


class DatasetRepository:
    """衍生数据集数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
        stmt = sa.select(ResearchDerivedDataset).where(ResearchDerivedDataset.id == dataset_id)
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
        values: dict[str, Any] = {"updated_at": sa.func.now()}
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
        metadata_content: dict[str, Any],
        points_content: list[Any],
        series_content: list[Any],
        field_manifest: list[Any],
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
