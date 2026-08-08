"""工作空间子仓库。

封装 ResearchWorkspace 的数据库操作：插入、查询（按 owner 过滤）、
列表（keyset 分页）、状态/名称/版本更新、删除。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 WorkspaceService 通过 ScopedSessionMixin 管理。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchWorkspace
from packages.research.repository._cursor import _decode_cursor, _encode_cursor


class WorkspaceRepository:
    """工作空间数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
