"""研究视图子仓库。

封装 ResearchView（稳定身份）与 ResearchViewVersion（不可变版本）的数据库操作：
插入、查询、列表、元数据/版本更新、版本插入/查询/列表。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchView, ResearchViewVersion


class ViewRepository:
    """研究视图数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
        values: dict[str, Any] = {"updated_at": sa.func.now()}
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
