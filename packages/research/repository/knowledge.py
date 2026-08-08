"""知识引用快照子仓库。

封装 ResearchKnowledgeReference（仅追加、不可变）的数据库操作：
插入、获取、按 Insight 查询、按 Run（和可选 Step）查询。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchKnowledgeReference


class KnowledgeReferenceRepository:
    """知识引用快照数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

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
