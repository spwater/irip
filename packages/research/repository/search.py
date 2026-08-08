"""已发布产物跨用户搜索子仓库。

封装跨用户搜索已发布成果包中 DerivedDataset 的数据库操作。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.research.entities import ResearchResult, ResearchResultVersion


class SearchRepository:
    """已发布产物搜索数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
        return list(res.all())  # type: ignore[arg-type]
