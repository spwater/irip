"""成果包收藏子仓库。

封装 ResearchResultFavorite 的数据库操作：插入、删除、检查、列表、
收藏成果包 ID 列表。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchResultFavorite


class FavoriteRepository:
    """成果包收藏数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
            sa.select(ResearchResultFavorite.result_id).where(
                ResearchResultFavorite.user_id == user_id
            )
        )
        return [row[0] for row in res.all()]
