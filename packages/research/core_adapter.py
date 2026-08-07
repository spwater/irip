"""CoreFactProvider 只读适配器。

研究域通过 CoreFactProvider 接口只读访问 Fact 数据，不暴露核心数据库会话。

CoreFactProvider（Protocol）：接口定义；
CoreFactProviderImpl：实现类，内部封装 FactQueryService 的只读方法，
将结果转换为研究域定义的 FactSummary 数据类。

调用方无法获得核心 session 引用。

注意：研究模块的搜索使用 ILIKE 模糊匹配 subject_id，不依赖 tsvector
（tsvector 的 simple 分词器对中文不友好，"拉曼"匹配不到"拉曼样品"）。
"""

from typing import Protocol
from uuid import UUID

import sqlalchemy as sa

from packages.common.database import scoped_session
from packages.research.models import FactSummary


class CoreFactProvider(Protocol):
    """只读访问 Fact 数据的适配器接口。

    研究域通过此接口搜索和获取 Fact 数据，不暴露核心数据库会话。

    权限校验（Q4 两层校验）：
    - 第二层：fact:read + 可见性，通过 FactQueryService 的 RLS 隔离自动过滤；
    - 调用方无需手动校验数据级权限，CoreFactProvider 内部已保证。
    """

    async def search_facts(
        self,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactSummary], str | None]:
        """搜索当前用户有权访问的 Fact。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactSummary], str | None]:
            (Fact 摘要列表, 下一页游标)。
        """
        ...

    async def get_fact_summary(self, fact_id: UUID) -> FactSummary:
        """获取 Fact 摘要（不含完整数据内容）。

        无权访问时抛出 AppError(forbidden)，不泄露内容。

        Args:
            fact_id: Fact UUID。

        Returns:
            FactSummary: Fact 摘要。
        """
        ...

    async def get_fact_fields(self, fact_id: UUID) -> list[str]:
        """获取 Fact 的字段清单（用于快照字段清单记录）。

        Args:
            fact_id: Fact UUID。

        Returns:
            list[str]: 字段名列表。
        """
        ...


class CoreFactProviderImpl:
    """CoreFactProvider 实现类。

    内部封装 FactQueryService 的只读方法，将结果转换为研究域定义的
    FactSummary 数据类。不暴露 query_service 的 session 引用给调用方。

    Attributes:
        _query_service: FactQueryService 实例（只读访问 Fact 数据）。
    """

    def __init__(self, query_service: object) -> None:
        """初始化 CoreFactProvider 实现。

        Args:
            query_service: FactQueryService 实例（类型为 object 避免循环导入）。
        """
        self._query_service = query_service

    async def search_facts(
        self,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactSummary], str | None]:
        """搜索 Fact（使用 ILIKE 模糊匹配 subject_id）。

        不依赖 tsvector 全文搜索（simple 分词器对中文不友好），
        直接用 ILIKE '%query%' 匹配 subject_id 字段。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactSummary], str | None]:
            (Fact 摘要列表, 下一页游标)。
        """
        from packages.facts.entities import Fact

        rls_dept_id: object | None = getattr(self._query_service, "_rls_dept_id", None)
        dept_id = rls_dept_id if rls_dept_id is not None else self._query_service._dept_id
        user_id = self._query_service._actor_id

        async with scoped_session(self._query_service._factory, dept_id, user_id) as session:
            effective_size = min(max(page_size, 1), 100)
            fetch_limit = effective_size + 1

            stmt = (
                sa.select(
                    Fact.id.label("fact_id"),
                    Fact.fact_type.label("fact_type"),
                    Fact.subject_id.label("subject_id"),
                    Fact.status.label("status"),
                    Fact.department_id,
                )
                .where(Fact.status == "active")
                .where(Fact.subject_id.ilike(f"%{query}%"))
                .order_by(Fact.created_at.desc(), Fact.id.desc())
                .limit(fetch_limit)
            )

            result = await session.execute(stmt)
            rows = result.mappings().all()

        items = list(rows)
        next_cursor = None
        if len(items) > effective_size:
            items = items[:effective_size]
            last = items[-1]
            import base64
            import json

            cursor_str = base64.b64encode(
                json.dumps(
                    {
                        "created_at": str(last["fact_id"]),
                        "id": str(last["fact_id"]),
                    },
                    ensure_ascii=False,
                ).encode()
            ).decode()
            next_cursor = cursor_str

        summaries = [
            FactSummary(
                fact_id=row["fact_id"],
                fact_type=row["fact_type"],
                subject_id=row["subject_id"],
                status=row["status"],
                department_name=None,
            )
            for row in items
        ]
        return summaries, next_cursor

    async def get_fact_summary(self, fact_id: UUID) -> FactSummary:
        """获取 Fact 摘要（委托 FactQueryService.get_fact_detail）。

        无权访问时 FactQueryService 内部 RLS 隔离会抛出 not_found，
        此处转换为 forbidden（不泄露 Fact 是否存在）。

        Args:
            fact_id: Fact UUID。

        Returns:
            FactSummary: Fact 摘要。

        Raises:
            AppError: code="forbidden"，当无权访问时。
        """
        from packages.common.errors import AppError

        try:
            row = await self._query_service.get_fact_detail(fact_id)
        except AppError as exc:
            if exc.code == "not_found":
                raise AppError(
                    code="forbidden",
                    message="无权访问该 Fact 数据",
                    retryable=False,
                    fields={"fact_id": str(fact_id)},
                ) from exc
            raise

        return FactSummary(
            fact_id=row.fact_id,
            fact_type=row.fact_type,
            subject_id=row.subject_id,
            status=row.status,
            department_name=row.department_name,
        )

    async def get_fact_fields(self, fact_id: UUID) -> list[str]:
        """获取 Fact 的字段清单。

        调用 FactQueryService.get_fact_data() 获取 metadata/points/series，
        提取字段名列表。

        Args:
            fact_id: Fact UUID。

        Returns:
            list[str]: 字段名列表。
        """
        from packages.common.errors import AppError

        try:
            data = await self._query_service.get_fact_data(fact_id)
        except AppError as exc:
            if exc.code == "not_found":
                raise AppError(
                    code="forbidden",
                    message="无权访问该 Fact 数据",
                    retryable=False,
                    fields={"fact_id": str(fact_id)},
                ) from exc
            raise

        fields: list[str] = []

        # 从 metadata 中提取字段名
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            fields.extend(str(k) for k in metadata.keys())

        # 从 points 中提取字段名（points 为 [{name, value, ...}] 格式）
        points = data.get("points", [])
        if isinstance(points, list):
            for point in points:
                if isinstance(point, dict):
                    name = point.get("name")
                    if name is not None and str(name) not in fields:
                        fields.append(str(name))

        return fields

    async def get_fact_data(self, fact_id: UUID) -> dict:
        """获取 Fact 完整数据（用于快照哈希计算）。

        此方法不暴露在 Protocol 接口中，仅由 EvidenceSnapshotService
        通过 duck typing 调用。

        Args:
            fact_id: Fact UUID。

        Returns:
            dict: Fact 数据字典（metadata/points/series）。
        """
        from packages.common.errors import AppError

        try:
            return await self._query_service.get_fact_data(fact_id)
        except AppError as exc:
            if exc.code == "not_found":
                raise AppError(
                    code="forbidden",
                    message="无权访问该 Fact 数据",
                    retryable=False,
                    fields={"fact_id": str(fact_id)},
                ) from exc
            raise
