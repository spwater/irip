"""ResearchCatalog 接口与实现。

搜索已确认衍生数据的只读接口。
- ResearchCatalog: Protocol 接口定义（阶段 1 占位）。
- ResearchCatalogStub: 阶段 1 占位实现，返回空列表。
- ResearchCatalogImpl: 阶段 3 实现，搜索当前用户已确认 DerivedDataset（跨 Workspace）。

阶段 3 的 ResearchCatalogImpl 替换 Stub，接口签名不变。
Composition Root 中条件替换注册。

参照架构设计 3.3 节。
"""

from typing import Protocol
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
        filters: dict | None = None,
    ) -> list[dict]:
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
        filters: dict | None = None,
    ) -> list[dict]:
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
        filters: dict | None = None,
    ) -> list[dict]:
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
