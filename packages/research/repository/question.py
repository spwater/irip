"""研究问题版本子仓库。

封装 ResearchQuestionVersion 的数据库操作：插入、最新版本查询、版本列表。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchQuestionVersion


class QuestionRepository:
    """研究问题版本数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
