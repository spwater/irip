"""成果包搜索服务：ResultSearchService。

职责：
- 成果包列表（三种视图：全部成果/我发布的/我收藏的）
- 关键词搜索（ILIKE 模糊匹配 title/summary/tags）
- 筛选器（发布者/时间/来源任务/数据类型/标签）
- 权限过滤（动态，按 current_acl_type + 源数据当前权限）

参照架构设计 3.3 节。
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.models import SearchResultItem, SearchResultPage
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.search")


class ResultSearchService(ScopedSessionMixin):
    """成果包搜索服务。

    职责：
    - 成果包列表（三种视图：全部成果/我发布的/我收藏的）
    - 关键词搜索（ILIKE 模糊匹配 title/summary/tags）
    - 筛选器（发布者/时间/来源任务/数据类型/标签）
    - 权限过滤（动态，按 current_acl_type）

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前用户 ID。
        _rls_dept_id: RLS 部门 ID（可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
    ) -> None:
        """初始化搜索服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前用户 ID。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    async def search(
        self,
        query: str | None,
        filters: dict | None,
        view_mode: str,
        page: int,
        page_size: int,
    ) -> SearchResultPage:
        """关键词搜索成果包。

        流程：
        1. 获取全部已发布成果包（或按 view_mode 过滤）
        2. 权限过滤（动态 ACL 过滤）
        3. 关键词搜索（ILIKE 模糊匹配）
        4. 筛选器应用（发布者/时间/标签/数据类型/workspace_id）
        5. 分页返回

        Args:
            query: 关键词搜索（可选）。
            filters: 筛选条件字典（可选）。
            view_mode: 视图模式（all / mine / favorites）。
            page: 页码（从 1 开始）。
            page_size: 每页数量。

        Returns:
            SearchResultPage: 搜索结果分页。
        """
        actor_id = self._require_actor()
        effective_page = max(page, 1)
        effective_size = min(max(page_size, 1), 100)

        async with self._scoped_session() as session:
            # 1. 根据 view_mode 获取基础结果集
            if view_mode == "mine":
                # 我发布的
                results = await ResearchRepository.list_published_results(session)
                results = [r for r in results if r.owner_user_id == actor_id]
            elif view_mode == "favorites":
                # 我收藏的
                favorite_ids = await ResearchRepository.list_favorite_result_ids(session, actor_id)
                results = await ResearchRepository.list_published_results(session)
                results = [r for r in results if r.id in favorite_ids]
            else:
                # 全部成果
                results = await ResearchRepository.list_published_results(session)

            # 2. 权限过滤
            results = [r for r in results if self._check_result_visible(r, actor_id)]

            # 3. 获取每个成果包的当前版本（用于搜索和筛选）
            result_ids = [r.id for r in results]
            if not result_ids:
                return SearchResultPage(
                    items=[], total=0, page=effective_page, page_size=effective_size
                )

            # 查询版本信息
            versions_map: dict[UUID, object] = {}
            for result in results:
                latest = await ResearchRepository.get_latest_result_version(session, result.id)
                if latest is not None and latest.status == "active":
                    versions_map[result.id] = latest

            # 4. 关键词搜索过滤
            if query:
                filtered_ids = set()
                for rid, version in versions_map.items():
                    if self._match_query(version, query):
                        filtered_ids.add(rid)
                results = [r for r in results if r.id in filtered_ids]
                versions_map = {k: v for k, v in versions_map.items() if k in filtered_ids}

            # 5. 筛选器应用
            if filters:
                results, versions_map = await self._apply_filters(
                    session, results, versions_map, filters
                )

            # 6. 构建搜索结果
            items: list[SearchResultItem] = []
            for result in results:
                version = versions_map.get(result.id)
                if version is None:
                    continue

                # 统计产物数量
                dataset_count = len(version.dataset_version_refs or [])
                view_count = len(version.view_version_refs or [])
                insight_count = len(version.insight_version_refs or [])

                items.append(
                    SearchResultItem(
                        result_id=result.id,
                        name=result.name,
                        title=version.title,
                        summary=version.summary or "",
                        tags=list(version.tags or []),
                        publisher=version.publisher,
                        published_at=version.published_at,
                        current_version=result.current_version,
                        current_acl_type=result.current_acl_type,
                        dataset_count=dataset_count,
                        view_count=view_count,
                        insight_count=insight_count,
                        workspace_id=result.workspace_id,
                    )
                )

            # 7. 分页
            total = len(items)
            start = (effective_page - 1) * effective_size
            end = start + effective_size
            paged_items = items[start:end]

            return SearchResultPage(
                items=paged_items,
                total=total,
                page=effective_page,
                page_size=effective_size,
            )

    async def list_results(
        self,
        view_mode: str,
        page: int,
        page_size: int,
    ) -> SearchResultPage:
        """成果包列表（无关键词搜索）。

        Args:
            view_mode: 视图模式（all / mine / favorites）。
            page: 页码。
            page_size: 每页数量。

        Returns:
            SearchResultPage: 搜索结果分页。
        """
        return await self.search(
            query=None,
            filters=None,
            view_mode=view_mode,
            page=page,
            page_size=page_size,
        )

    def _check_result_visible(
        self,
        result: object,
        principal_id: UUID,
    ) -> bool:
        """校验当前用户是否有权查看成果包（基于 ACL）。

        Args:
            result: ResearchResult ORM 实体。
            principal_id: 当前用户 ID。

        Returns:
            bool: 是否有权查看。
        """
        if result.current_acl_type == "private":
            return result.owner_user_id == principal_id
        if result.current_acl_type == "tree":
            return True  # 首期简化：同部门用户可见（RLS 已过滤）
        if result.current_acl_type == "explicit":
            explicit_ids = result.current_explicit_user_ids or []
            return (
                str(principal_id) in [str(uid) for uid in explicit_ids]
                or result.owner_user_id == principal_id
            )
        if result.current_acl_type == "all":
            return True
        return False

    def _match_query(self, version: object, query: str) -> bool:
        """检查版本是否匹配关键词查询。

        匹配 title / summary / tags。

        Args:
            version: ResearchResultVersion ORM 实体。
            query: 关键词。

        Returns:
            bool: 是否匹配。
        """
        query_lower = query.lower()
        if version.title and query_lower in version.title.lower():
            return True
        if version.summary and query_lower in version.summary.lower():
            return True
        tags = version.tags or []
        for tag in tags:
            if isinstance(tag, str) and query_lower in tag.lower():
                return True
        return False

    async def _apply_filters(
        self,
        session: AsyncSession,
        results: list,
        versions_map: dict,
        filters: dict,
    ) -> tuple[list, dict]:
        """应用筛选器。

        支持的筛选条件：
        - publisher: 发布者 ID
        - date_from: 发布日期起
        - date_to: 发布日期止
        - tags: 标签列表（匹配任一）
        - data_type: 数据类型（dataset / view / insight 存在性）
        - workspace_id: 来源工作空间 ID

        Args:
            session: 异步会话。
            results: 成果包列表。
            versions_map: 版本映射。
            filters: 筛选条件。

        Returns:
            tuple[list, dict]: (过滤后结果列表, 过滤后版本映射)。
        """
        filtered_results: list = []
        filtered_versions: dict = {}

        publisher = filters.get("publisher")
        date_from = filters.get("date_from")
        date_to = filters.get("date_to")
        tags = filters.get("tags")
        data_type = filters.get("data_type")
        workspace_id = filters.get("workspace_id")

        for result in results:
            version = versions_map.get(result.id)
            if version is None:
                continue

            # publisher 过滤
            if publisher is not None:
                if str(version.publisher) != str(publisher):
                    continue

            # date_from 过滤
            if date_from is not None:
                if version.published_at is None:
                    continue
                try:
                    from datetime import datetime

                    fromisoformat = datetime.fromisoformat
                    filter_date = fromisoformat(str(date_from))
                    if version.published_at < filter_date:
                        continue
                except (ValueError, TypeError):
                    pass

            # date_to 过滤
            if date_to is not None:
                if version.published_at is None:
                    continue
                try:
                    from datetime import datetime

                    fromisoformat = datetime.fromisoformat
                    filter_date = fromisoformat(str(date_to))
                    if version.published_at > filter_date:
                        continue
                except (ValueError, TypeError):
                    pass

            # tags 过滤（匹配任一）
            if tags is not None and isinstance(tags, list) and tags:
                version_tags = set()
                for t in version.tags or []:
                    if isinstance(t, str):
                        version_tags.add(t.lower())
                filter_tags = {str(t).lower() for t in tags}
                if not version_tags.intersection(filter_tags):
                    continue

            # data_type 过滤（存在性）
            if data_type is not None:
                if data_type == "dataset":
                    if not (version.dataset_version_refs or []):
                        continue
                elif data_type == "view":
                    if not (version.view_version_refs or []):
                        continue
                elif data_type == "insight":
                    if not (version.insight_version_refs or []):
                        continue

            # workspace_id 过滤
            if workspace_id is not None:
                if str(result.workspace_id) != str(workspace_id):
                    continue

            filtered_results.append(result)
            filtered_versions[result.id] = version

        return filtered_results, filtered_versions
