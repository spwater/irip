"""ResearchCatalog 接口与实现。

搜索已确认衍生数据的只读接口。
- ResearchCatalog: Protocol 接口定义（阶段 1 占位）。
- ResearchCatalogStub: 阶段 1 占位实现，返回空列表。
- ResearchCatalogImpl: 阶段 3 实现，搜索当前用户已确认 DerivedDataset（跨 Workspace）。

阶段 3 的 ResearchCatalogImpl 替换 Stub，接口签名不变。
Composition Root 中条件替换注册。

参照架构设计 3.3 节。
"""

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ResearchCatalog(Protocol):
    """搜索已确认衍生数据的只读接口。

    后续子项目实现已发布衍生数据搜索时，只需实现此接口，
    无需修改研究域其他代码。
    """

    async def search_derived_data(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """搜索已确认的衍生数据。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典。

        Returns:
            list[dict]: 衍生数据列表。
        """
        ...


class ResearchCatalogStub:
    """ResearchCatalog 占位实现。

    返回空列表。阶段 3 中 Composition Root 替换为 ResearchCatalogImpl。
    """

    async def search_derived_data(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """搜索已确认的衍生数据（占位返回空列表）。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典。

        Returns:
            list[dict]: 空列表。
        """
        return []


class ResearchCatalogImpl:
    """ResearchCatalog 实现：搜索当前用户已确认 DerivedDataset。

    从阶段 1 的空占位升级为可搜索（PRD P0-13）。
    搜索范围：当前用户拥有的全部 Workspace 中的已确认 DerivedDataset（跨 Workspace）。

    阶段 4 升级：新增 search_published_derived_data() 方法，搜索已发布成果包中的
    DerivedDataset（跨用户，ACL 过滤）。

    Attributes:
        _factory: 异步会话工厂。
        _actor_id: 当前用户 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        actor_id: UUID,
    ) -> None:
        """初始化 ResearchCatalog 实现。

        Args:
            session_factory: 异步会话工厂。
            actor_id: 当前用户 ID（用于 owner_user_id 过滤）。
        """
        self._factory = session_factory
        self._actor_id = actor_id

    async def search_derived_data(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """搜索已确认 DerivedDataset。

        返回 [{id, name, current_version, workspace_id, owner_user_id, summary, tags}]
        仅返回 owner_user_id = 当前用户的已确认 DerivedDataset。

        支持的 filters：
        - workspace_id: 按工作空间筛选
        - dataset_id: 按特定数据集 ID 筛选（用于 add_evidence 校验）

        Args:
            query: 关键词搜索（name ILIKE）。
            filters: 过滤条件。

        Returns:
            list[dict]: 搜索结果列表。
        """
        from packages.research.repository import ResearchRepository

        workspace_id: UUID | None = None
        dataset_id: UUID | None = None
        if filters is not None:
            ws_id_str = filters.get("workspace_id")
            if ws_id_str is not None:
                try:
                    workspace_id = UUID(str(ws_id_str))
                except (ValueError, AttributeError):
                    pass
            ds_id_str = filters.get("dataset_id")
            if ds_id_str is not None:
                try:
                    dataset_id = UUID(str(ds_id_str))
                except (ValueError, AttributeError):
                    pass

        async with self._factory() as session:
            datasets = await ResearchRepository.search_derived_datasets(
                session,
                owner_user_id=self._actor_id,
                query=query if query else None,
                workspace_id=workspace_id,
            )
            # 按 dataset_id 过滤（如果指定）
            if dataset_id is not None:
                datasets = [ds for ds in datasets if ds.id == dataset_id]
            return [
                {
                    "id": str(ds.id),
                    "name": ds.name,
                    "current_version": ds.current_version,
                    "workspace_id": str(ds.workspace_id),
                    "owner_user_id": str(ds.owner_user_id),
                    "summary": ds.summary or "",
                    "tags": list(ds.tags or []),
                }
                for ds in datasets
            ]

    async def search_published_derived_data(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """搜索已发布成果包中的 DerivedDataset（跨用户，ACL 过滤）。

        阶段 4 新增：搜索范围从当前用户已确认 DerivedDataset 扩展为已发布成果包
        中的 DerivedDataset（跨用户）。搜索结果按当前用户权限动态过滤。

        支持的 filters：
        - result_id: 指定成果包 ID 筛选

        Args:
            query: 关键词搜索（匹配版本标题/摘要/数据集名称）。
            filters: 过滤条件。

        Returns:
            list[dict]: 搜索结果列表，包含：
                [{result_id, result_version_number, dataset_id, dataset_version_number,
                  dataset_name, result_title, publisher, published_at, current_acl_type}]
        """
        from packages.research.repository import ResearchRepository

        result_id: UUID | None = None
        if filters is not None:
            rid_str = filters.get("result_id")
            if rid_str is not None:
                try:
                    result_id = UUID(str(rid_str))
                except (ValueError, AttributeError):
                    pass

        async with self._factory() as session:
            pairs = await ResearchRepository.search_published_datasets(
                session,
                query=query if query else None,
                result_id=result_id,
            )

            results: list[dict[str, Any]] = []
            for version, result_entity in pairs:
                # ACL 过滤
                if not self._check_visible(result_entity):
                    continue

                # 解析 dataset_version_refs
                for ref in version.dataset_version_refs or []:
                    dataset_id = ref.get("dataset_id", "")
                    dataset_version = ref.get("version_number", 0)
                    dataset_name = ref.get("name", "")
                    results.append(
                        {
                            "result_id": str(result_entity.id),
                            "result_version_number": version.version_number,
                            "dataset_id": dataset_id,
                            "dataset_version_number": dataset_version,
                            "dataset_name": dataset_name,
                            "result_title": version.title,
                            "publisher": str(version.publisher),
                            "published_at": version.published_at.isoformat()
                            if version.published_at
                            else "",
                            "current_acl_type": result_entity.current_acl_type,
                        }
                    )

            return results

    def _check_visible(self, result_entity: object) -> bool:
        """校验当前用户是否有权查看成果包（基于 ACL）。

        Args:
            result_entity: ResearchResult ORM 实体。

        Returns:
            bool: 是否有权查看。
        """
        if result_entity.current_acl_type == "private":  # type: ignore[attr-defined]
            return result_entity.owner_user_id == self._actor_id  # type: ignore[no-any-return, attr-defined]
        if result_entity.current_acl_type == "tree":  # type: ignore[attr-defined]
            return True  # 首期简化：同部门用户可见（RLS 已过滤）
        if result_entity.current_acl_type == "explicit":  # type: ignore[attr-defined]
            explicit_ids = result_entity.current_explicit_user_ids or []  # type: ignore[attr-defined]
            return (
                str(self._actor_id) in [str(uid) for uid in explicit_ids]
                or result_entity.owner_user_id == self._actor_id  # type: ignore[attr-defined]
            )
        if result_entity.current_acl_type == "all":  # type: ignore[attr-defined]
            return True
        return False
