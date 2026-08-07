"""知识引用快照管理服务（阶段 5 新增）。

KnowledgeReferenceService 保存 AI 引用知识库时的段落快照、文档版本和哈希。
快照创建后不可变。

短文本(≤4KB)直接存 PostgreSQL，长文本(>4KB)存 MinIO。
单条快照限制 64KB（超出截断并标注"[已截断]"）。

参照架构设计 3.3 节 KnowledgeReferenceService。
"""

import hashlib
import json
import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.research.models import (
    KnowledgeReferenceDetail,
    KnowledgeReferenceRef,
    KnowledgeSearchResult,
)
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.knowledge_reference")

#: 短文本直接存储的阈值（4KB）。
SNIPPET_INLINE_THRESHOLD: int = 4 * 1024

#: 单条快照最大长度（64KB）。
SNIPPET_MAX_SIZE: int = 64 * 1024

#: 截断标注后缀。
TRUNCATION_SUFFIX: str = "\n\n[已截断]"


class KnowledgeReferenceService(ScopedSessionMixin):
    """知识引用快照管理服务。

    保存 AI 引用知识库时的段落快照、文档版本和哈希。
    快照创建后不可变。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _lineage_writer: 溯源边写入服务。
        _s3: S3 / MinIO 存储客户端。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        lineage_writer: object,
        s3: object,
    ) -> None:
        """初始化知识引用快照管理服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            lineage_writer: LineageWriterService 实例。
            s3: S3Repository 实例。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._lineage_writer = lineage_writer
        self._s3 = s3
        self._rls_dept_id: UUID | None = None

    async def save_reference(
        self,
        workspace_id: UUID,
        run_id: UUID,
        step_id: UUID | None,
        search_result: KnowledgeSearchResult,
        research_question_context: str | None = None,
        provider_name: str = "mock",
    ) -> KnowledgeReferenceRef:
        """保存知识引用快照。

        1. 判断 snippet_text 长度：≤4KB 直接存 → >4KB 存 MinIO → >64KB 截断标注
        2. 计算 content_hash（snippet_text SHA-256）
        3. 创建 research_knowledge_reference 记录（仅追加）
        4. 调用 LineageWriterService.on_knowledge_referenced() 创建溯源边
        5. 审计 research.knowledge.reference_saved
        6. 返回 KnowledgeReferenceRef

        Args:
            workspace_id: 工作空间 ID。
            run_id: Run ID。
            step_id: 步骤 ID（可空）。
            search_result: 知识库检索结果。
            research_question_context: 研究问题上下文（可空）。
            provider_name: Provider 名称。

        Returns:
            KnowledgeReferenceRef: 引用快照引用。
        """
        actor_id = self._actor_id or UUID(int=0)
        snippet_text = search_result.snippet

        # 1. 截断处理
        snippet_text = self._truncate_snippet(snippet_text)

        # 2. 计算 content_hash
        content_hash = hashlib.sha256(snippet_text.encode("utf-8")).hexdigest()

        # 3. 存储长文本到 MinIO（>4KB）
        snippet_storage_path: str | None = None
        inline_snippet: str | None = None

        if len(snippet_text.encode("utf-8")) <= SNIPPET_INLINE_THRESHOLD:
            inline_snippet = snippet_text
        else:
            # 长文本存 MinIO（reference_id 尚未生成，先使用临时 ID）
            # 先创建记录获取 ID，再存储到 MinIO
            pass

        async with self._scoped_session() as session:
            # 4. 创建 research_knowledge_reference 记录
            ref = await ResearchRepository.insert_knowledge_reference(
                session,
                workspace_id=workspace_id,
                run_id=run_id,
                step_id=step_id,
                insight_id=None,  # insight_id 在知识引用关联时更新
                document_id=search_result.document_id,
                document_version=search_result.document_version,
                title=search_result.title,
                section=search_result.section or None,
                page=search_result.page or None,
                chunk_id=search_result.chunk_id or None,
                snippet_text=inline_snippet,
                snippet_storage_path=snippet_storage_path,
                content_hash=content_hash,
                source_uri=search_result.source_uri,
                provider_name=provider_name,
                research_question_context=research_question_context,
            )

            # 如果长文本需要存 MinIO，此时已有 reference_id
            if inline_snippet is None:
                storage_path = self._store_snippet(ref.id, snippet_text, workspace_id, run_id)
                if storage_path is not None:
                    # 更新 snippet_storage_path（仅追加策略下，此处为初始化写入，允许）
                    await session.execute(
                        sa.text(
                            "UPDATE research_knowledge_reference "
                            "SET snippet_storage_path = :path WHERE id = :rid"
                        ),
                        {"path": storage_path, "rid": str(ref.id)},
                    )
                    ref.snippet_storage_path = storage_path

            # 5. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.knowledge.reference_saved",
                    actor_user_id=actor_id,
                    resource_type="research_knowledge_reference",
                    resource_id=ref.id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "run_id": str(run_id),
                        "document_id": search_result.document_id,
                        "title": search_result.title[:100],
                    },
                ),
            )

        # 6. 调用溯源边写入 Hook（不阻断主流程）
        try:
            await self._lineage_writer.on_knowledge_referenced(ref.id, None)
        except Exception as exc:
            logger.warning("on_knowledge_referenced hook failed: %s", exc)

        return KnowledgeReferenceRef(
            reference_id=ref.id,
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            insight_id=None,
            document_id=search_result.document_id,
            document_version=search_result.document_version,
            title=search_result.title,
            content_hash=content_hash,
            source_uri=search_result.source_uri,
            retrieval_time=ref.retrieval_time,
            provider_name=provider_name,
        )

    async def list_references_by_insight(
        self,
        insight_id: UUID,
        include_full_content: bool = False,
    ) -> list[KnowledgeReferenceDetail]:
        """查看 Insight 关联的知识引用快照列表。

        include_full_content=True 需要 research:manage 权限（返回完整 snippet_text）。
        include_full_content=False 仅返回文档标题和来源链接（普通用户可见）。

        Args:
            insight_id: Insight ID。
            include_full_content: 是否包含完整段落文本。

        Returns:
            list[KnowledgeReferenceDetail]: 引用快照详情列表。
        """
        async with self._scoped_session() as session:
            refs = await ResearchRepository.list_knowledge_references_by_insight(
                session, insight_id
            )

            details: list[KnowledgeReferenceDetail] = []
            for ref in refs:
                snippet_text = ""
                if include_full_content:
                    if ref.snippet_storage_path:
                        snippet_text = self._retrieve_snippet(ref.snippet_storage_path)
                    else:
                        snippet_text = ref.snippet_text or ""

                ref_dto = KnowledgeReferenceRef(
                    reference_id=ref.id,
                    workspace_id=ref.workspace_id,
                    run_id=ref.run_id,
                    step_id=ref.step_id,
                    insight_id=ref.insight_id,
                    document_id=ref.document_id,
                    document_version=ref.document_version,
                    title=ref.title,
                    content_hash=ref.content_hash,
                    source_uri=ref.source_uri,
                    retrieval_time=ref.retrieval_time,
                    provider_name=ref.provider_name,
                )
                details.append(
                    KnowledgeReferenceDetail(
                        ref=ref_dto,
                        snippet_text=snippet_text,
                        section=ref.section or "",
                        page=ref.page or 0,
                        chunk_id=ref.chunk_id or "",
                        research_question_context=ref.research_question_context or "",
                    )
                )

            # 审计查看
            actor_id = self._actor_id or UUID(int=0)
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.knowledge.reference.view",
                    actor_user_id=actor_id,
                    resource_type="research_knowledge_reference",
                    resource_id=insight_id,
                    payload={
                        "include_full_content": include_full_content,
                        "count": len(details),
                    },
                ),
            )

        return details

    async def list_references_by_run(
        self,
        run_id: UUID,
        step_id: UUID | None = None,
    ) -> list[KnowledgeReferenceRef]:
        """按 Run（和可选 Step）查询知识引用快照列表。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID（可选过滤）。

        Returns:
            list[KnowledgeReferenceRef]: 引用快照引用列表。
        """
        async with self._scoped_session() as session:
            refs = await ResearchRepository.list_knowledge_references_by_run(
                session, run_id, step_id
            )
            return [
                KnowledgeReferenceRef(
                    reference_id=ref.id,
                    workspace_id=ref.workspace_id,
                    run_id=ref.run_id,
                    step_id=ref.step_id,
                    insight_id=ref.insight_id,
                    document_id=ref.document_id,
                    document_version=ref.document_version,
                    title=ref.title,
                    content_hash=ref.content_hash,
                    source_uri=ref.source_uri,
                    retrieval_time=ref.retrieval_time,
                    provider_name=ref.provider_name,
                )
                for ref in refs
            ]

    async def get_reference(
        self,
        reference_id: UUID,
        include_full_content: bool = False,
    ) -> KnowledgeReferenceDetail | None:
        """查看单个知识引用快照详情。

        include_full_content=True 需要 research:manage 权限。

        Args:
            reference_id: 引用快照 ID。
            include_full_content: 是否包含完整段落文本。

        Returns:
            KnowledgeReferenceDetail | None: 引用快照详情，不存在时返回 None。
        """
        async with self._scoped_session() as session:
            ref = await ResearchRepository.get_knowledge_reference(session, reference_id)
            if ref is None:
                return None

            snippet_text = ""
            if include_full_content:
                if ref.snippet_storage_path:
                    snippet_text = self._retrieve_snippet(ref.snippet_storage_path)
                else:
                    snippet_text = ref.snippet_text or ""

            ref_dto = KnowledgeReferenceRef(
                reference_id=ref.id,
                workspace_id=ref.workspace_id,
                run_id=ref.run_id,
                step_id=ref.step_id,
                insight_id=ref.insight_id,
                document_id=ref.document_id,
                document_version=ref.document_version,
                title=ref.title,
                content_hash=ref.content_hash,
                source_uri=ref.source_uri,
                retrieval_time=ref.retrieval_time,
                provider_name=ref.provider_name,
            )

            return KnowledgeReferenceDetail(
                ref=ref_dto,
                snippet_text=snippet_text,
                section=ref.section or "",
                page=ref.page or 0,
                chunk_id=ref.chunk_id or "",
                research_question_context=ref.research_question_context or "",
            )

    def _store_snippet(
        self,
        reference_id: UUID,
        snippet_text: str,
        workspace_id: UUID,
        run_id: UUID,
    ) -> str | None:
        """存储长文本快照到 MinIO，返回路径。

        路径格式：research/knowledge_refs/{workspace_id}/{run_id}/{reference_id}.json

        Args:
            reference_id: 引用快照 ID。
            snippet_text: 段落文本。
            workspace_id: 工作空间 ID。
            run_id: Run ID。

        Returns:
            str | None: MinIO 存储路径，存储失败时返回 None。
        """
        path = f"research/knowledge_refs/{workspace_id}/{run_id}/{reference_id}.json"
        try:
            content = json.dumps(
                {"snippet_text": snippet_text},
                ensure_ascii=False,
            ).encode("utf-8")
            # S3Repository 方法为同步，直接调用（在事务内执行）
            self._s3.put_object(path, content)
            return path
        except Exception as exc:
            logger.error("Failed to store snippet to MinIO: %s", exc)
            return None

    def _retrieve_snippet(self, snippet_storage_path: str) -> str:
        """从 MinIO 读取长文本快照。

        Args:
            snippet_storage_path: MinIO 存储路径。

        Returns:
            str: 段落文本，读取失败时返回空字符串。
        """
        try:
            # S3Repository 方法为同步，直接调用
            content = self._s3.get_object(snippet_storage_path)
            data = json.loads(content.decode("utf-8"))
            return data.get("snippet_text", "")
        except Exception as exc:
            logger.error("Failed to retrieve snippet from MinIO: %s", exc)
            return ""

    def _truncate_snippet(self, snippet_text: str) -> str:
        """截断至 64KB 并标注。

        Args:
            snippet_text: 原始段落文本。

        Returns:
            str: 截断后的文本（可能含 "[已截断]" 后缀）。
        """
        encoded = snippet_text.encode("utf-8")
        if len(encoded) <= SNIPPET_MAX_SIZE:
            return snippet_text

        # 截断到 64KB - len(TRUNCATION_SUFFIX)
        max_bytes = SNIPPET_MAX_SIZE - len(TRUNCATION_SUFFIX.encode("utf-8"))
        truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return truncated + TRUNCATION_SUFFIX
