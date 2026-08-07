"""外部知识库只读检索接口合同 + 编排服务 + Mock 实现（阶段 5 新增）。

KnowledgeProvider 定义为 Python Protocol（PEP 544），
KnowledgeProviderService 编排多 Provider 并行检索 + 合并去重 + 降级处理，
MockKnowledgeProvider 用于测试。

参照架构设计 3.3 节 KnowledgeProvider / KnowledgeProviderService。
"""

import asyncio
import hashlib
import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.research.models import (
    KnowledgeDocument,
    KnowledgeSearchOptions,
    KnowledgeSearchResult,
)

logger = logging.getLogger("research.knowledge_provider")

#: 单个 Provider 默认超时（秒）。
DEFAULT_PROVIDER_TIMEOUT: int = 30


class KnowledgeProvider(Protocol):
    """外部知识库只读检索接口合同（Protocol）。

    研究模块不维护知识库内容，只消费只读接口。
    """

    async def search(
        self,
        query: str,
        options: KnowledgeSearchOptions | None = None,
    ) -> list[KnowledgeSearchResult]:
        """检索知识库。

        query 仅包含研究问题和用户确认的关键词（不发送 Fact 原始数据）。

        Args:
            query: 检索查询字符串（研究问题 + 关键词）。
            options: 检索选项。

        Returns:
            list[KnowledgeSearchResult]: 检索结果列表。
        """
        ...

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        """获取文档元数据（不含全文内容）。

        Args:
            document_id: 文档 ID。

        Returns:
            KnowledgeDocument | None: 文档元数据，不存在时返回 None。
        """
        ...

    async def health_check(self) -> bool:
        """健康检查。

        Returns:
            bool: 是否健康。
        """
        ...


class KnowledgeProviderService:
    """知识库检索编排服务。

    管理多个 KnowledgeProvider 实例，并行检索 + 合并去重 + 降级处理。

    Attributes:
        _factory: 异步会话工厂。
        _providers: Provider 名称 → Provider 实例映射。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        providers: dict[str, KnowledgeProvider],
    ) -> None:
        """初始化知识库检索编排服务。

        Args:
            session_factory: 异步会话工厂。
            providers: {provider_name: provider_instance} 映射。
        """
        self._factory = session_factory
        self._providers = providers

    async def search(
        self,
        query: str,
        options: KnowledgeSearchOptions | None = None,
        provider_names: list[str] | None = None,
    ) -> list[KnowledgeSearchResult]:
        """检索知识库（支持指定 provider 或全部 provider 并行检索）。

        1. 确定参与的 providers（指定 provider_names 或全部 enabled providers）
        2. 并行调用各 provider.search()（超时独立控制）
        3. 合并结果：按 relevance_score 排序，按 content_hash 去重
        4. 返回合并后的结果列表

        Args:
            query: 检索查询字符串。
            options: 检索选项。
            provider_names: 指定 provider 名称列表（None 表示全部）。

        Returns:
            list[KnowledgeSearchResult]: 合并去重后的结果列表。
        """
        if provider_names is not None:
            active_providers = {
                name: p for name, p in self._providers.items() if name in provider_names
            }
        else:
            active_providers = dict(self._providers)

        if not active_providers:
            return []

        opts = options or KnowledgeSearchOptions()
        timeout = opts.timeout if opts.timeout > 0 else DEFAULT_PROVIDER_TIMEOUT

        # 并行检索
        tasks: list[tuple[str, asyncio.Task]] = []
        for name, provider in active_providers.items():
            task = asyncio.create_task(
                asyncio.wait_for(
                    provider.search(query, opts),
                    timeout=timeout,
                )
            )
            tasks.append((name, task))

        results: list[list[KnowledgeSearchResult]] = []
        for name, task in tasks:
            try:
                result = await task
                results.append(result)
            except TimeoutError:
                logger.warning("Provider '%s' timed out after %ds", name, timeout)
                self._handle_provider_error(name, TimeoutError(), is_required=False)
            except Exception as exc:
                logger.warning("Provider '%s' search failed: %s", name, exc)
                self._handle_provider_error(name, exc, is_required=False)

        return self._merge_and_deduplicate(results)

    async def search_all(
        self,
        query: str,
        options: KnowledgeSearchOptions | None = None,
    ) -> list[KnowledgeSearchResult]:
        """全部 enabled providers 并行检索。

        Args:
            query: 检索查询字符串。
            options: 检索选项。

        Returns:
            list[KnowledgeSearchResult]: 合并去重后的结果列表。
        """
        return await self.search(query, options, provider_names=None)

    def _merge_and_deduplicate(
        self,
        results: list[list[KnowledgeSearchResult]],
    ) -> list[KnowledgeSearchResult]:
        """合并去重：按 relevance_score 排序，按 content_hash 去重。

        Args:
            results: 多个 Provider 的检索结果列表。

        Returns:
            list[KnowledgeSearchResult]: 合并去重后的结果列表。
        """
        all_results: list[KnowledgeSearchResult] = []
        for provider_results in results:
            all_results.extend(provider_results)

        # 按 content_hash 去重（保留 relevance_score 最高的）
        seen: dict[str, KnowledgeSearchResult] = {}
        for r in all_results:
            hash_key = r.content_hash or f"{r.document_id}:{r.chunk_id}"
            if hash_key not in seen or r.relevance_score > seen[hash_key].relevance_score:
                seen[hash_key] = r

        # 按 relevance_score 降序排序
        merged = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
        return merged

    def _handle_provider_error(
        self,
        provider_name: str,
        error: Exception,
        is_required: bool,
    ) -> None:
        """Provider 错误处理：非必要降级标注，必要步骤失败。

        Args:
            provider_name: Provider 名称。
            error: 异常。
            is_required: 是否为必要步骤。
        """
        if is_required:
            logger.error(
                "Required provider '%s' failed: %s — step will be marked failed",
                provider_name,
                error,
            )
        else:
            logger.warning(
                "Provider '%s' failed: %s — degraded to data-only analysis",
                provider_name,
                error,
            )

        # 审计降级事件
        try:
            asyncio.ensure_future(self._audit_provider_degraded(provider_name, str(error)))
        except Exception:
            pass

    async def _audit_provider_degraded(self, provider_name: str, error_msg: str) -> None:
        """审计知识库 Provider 降级事件。

        Args:
            provider_name: Provider 名称。
            error_msg: 错误消息。
        """
        try:
            async with self._factory() as session:
                async with session.begin():
                    await AuditRecorder.record(
                        session,
                        AuditEventData(
                            department_id=UUID(int=0),
                            actor_user_id=UUID(int=0),
                            action="research.knowledge.provider_degraded",
                            resource_type="knowledge_provider",
                            resource_id=UUID(int=0),
                            payload={
                                "provider_name": provider_name,
                                "error": error_msg[:200],
                            },
                        ),
                    )
        except Exception as exc:
            logger.warning("Failed to audit provider degradation: %s", exc)


class MockKnowledgeProvider:
    """测试用 Mock 知识库 Provider。

    预置 Mock 文档列表，按关键词匹配返回 Mock 结果。

    Attributes:
        provider_name: Provider 名称。
    """

    # 预置 Mock 文档列表
    _MOCK_DOCUMENTS: list[dict] = [
        {
            "document_id": "mock_doc_001",
            "document_version": "2024.03",
            "title": "铝合金热处理工艺规范",
            "section": "第3章 退火工艺",
            "page": 45,
            "source_uri": "https://knowledge.example.com/docs/mock_doc_001",
            "snippet": "铝合金退火温度通常在 350-450°C 范围内，保温时间根据材料厚度确定。",
        },
        {
            "document_id": "mock_doc_002",
            "document_version": "2024.01",
            "title": "材料力学性能手册",
            "section": "第5章 疲劳性能",
            "page": 120,
            "source_uri": "https://knowledge.example.com/docs/mock_doc_002",
            "snippet": (
                "材料的疲劳极限与抗拉强度存在经验关系，通常疲劳极限约为抗拉强度的 0.4-0.5 倍。"
            ),
        },
        {
            "document_id": "mock_doc_003",
            "document_version": "2023.12",
            "title": "表面处理技术手册",
            "section": "第2章 阳极氧化",
            "page": 78,
            "source_uri": "https://knowledge.example.com/docs/mock_doc_003",
            "snippet": "阳极氧化膜厚度对耐腐蚀性能有显著影响，推荐膜厚度为 15-25μm。",
        },
    ]

    def __init__(self, provider_name: str = "mock") -> None:
        """初始化 Mock 知识库 Provider。

        Args:
            provider_name: Provider 名称。
        """
        self.provider_name = provider_name

    async def search(
        self,
        query: str,
        options: KnowledgeSearchOptions | None = None,
    ) -> list[KnowledgeSearchResult]:
        """按关键词匹配返回 Mock 结果。

        Args:
            query: 检索查询字符串。
            options: 检索选项。

        Returns:
            list[KnowledgeSearchResult]: Mock 检索结果列表。
        """
        opts = options or KnowledgeSearchOptions()
        max_results = opts.max_results if opts.max_results > 0 else 10

        results: list[KnowledgeSearchResult] = []
        query_lower = query.lower()

        for doc in self._MOCK_DOCUMENTS:
            # 简单关键词匹配
            title = doc["title"].lower()
            snippet = doc["snippet"].lower()
            section = doc.get("section", "").lower()

            if any(kw in title or kw in snippet or kw in section for kw in query_lower.split()):
                content_hash = hashlib.sha256(doc["snippet"].encode("utf-8")).hexdigest()
                results.append(
                    KnowledgeSearchResult(
                        document_id=doc["document_id"],
                        document_version=doc["document_version"],
                        title=doc["title"],
                        section=doc.get("section", ""),
                        page=doc.get("page", 0),
                        chunk_id=f"{doc['document_id']}_chunk_1",
                        relevance_score=0.85,
                        source_uri=doc["source_uri"],
                        content_hash=content_hash,
                        snippet=doc["snippet"],
                    )
                )

        # 如果没有匹配，返回全部 Mock 文档（方便测试）
        if not results:
            for doc in self._MOCK_DOCUMENTS[:max_results]:
                content_hash = hashlib.sha256(doc["snippet"].encode("utf-8")).hexdigest()
                results.append(
                    KnowledgeSearchResult(
                        document_id=doc["document_id"],
                        document_version=doc["document_version"],
                        title=doc["title"],
                        section=doc.get("section", ""),
                        page=doc.get("page", 0),
                        chunk_id=f"{doc['document_id']}_chunk_1",
                        relevance_score=0.5,
                        source_uri=doc["source_uri"],
                        content_hash=content_hash,
                        snippet=doc["snippet"],
                    )
                )

        return results[:max_results]

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        """获取文档元数据。

        Args:
            document_id: 文档 ID。

        Returns:
            KnowledgeDocument | None: 文档元数据。
        """
        for doc in self._MOCK_DOCUMENTS:
            if doc["document_id"] == document_id:
                return KnowledgeDocument(
                    document_id=doc["document_id"],
                    document_version=doc["document_version"],
                    title=doc["title"],
                    source_uri=doc["source_uri"],
                )
        return None

    async def health_check(self) -> bool:
        """健康检查。

        Returns:
            bool: Mock 始终返回 True。
        """
        return True
