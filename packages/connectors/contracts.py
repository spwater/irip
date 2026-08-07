"""连接器协议与值类型。

定义外部数据源接入的核心契约（IRIP Task 13）：

- ConnectorSource: 连接器无关的数据源描述（kind + kind 特定配置）。
- PreviewTable: 连接器预览结果（列名 + 行 + 总行数）。
- SourceRecord: 单条源记录（字段名→值）。
- Connector: 连接器协议（preview 预览 / read 流式读取）。

映射相关值类型（MappingRule / MappingCandidate）已随标准层空表
清理（migration 0057）删除。

安全约定：
- 配置中的敏感凭据仅以 ``secret_id`` 引用，绝不在值类型中携带明文 DSN/token。
- 连接器在内部按 secret_id 解析凭据，解析结果不返回、不记录日志。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ConnectorSource:
    """数据源描述（连接器无关）。

    Attributes:
        kind: 数据源类型（file / postgres / rest）。
        config: 类型特定配置字典：
            - file -> ``{"artifact_id": str(uuid), "format": "csv"|"xlsx"|"json"}``
            - postgres -> ``{"secret_id": str(uuid), "query": str}``
            - rest -> ``{"secret_id": str(uuid), "path": str, "method": "GET"|"POST"}``
    """

    kind: Literal["file", "postgres", "rest"]
    config: dict[str, Any]


@dataclass(frozen=True)
class PreviewTable:
    """连接器预览结果。

    Attributes:
        columns: 列名元组（按源数据顺序）。
        rows: 行元组元组（最多 ``limit`` 行，每行为字段值元组）。
        row_count: 源数据可用总行数（可能超过预览行数）。
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int


@dataclass(frozen=True)
class SourceRecord:
    """单条源记录（字段名→值）。

    Attributes:
        fields: 字段名到值的映射；缺失字段值为 None。
    """

    fields: dict[str, str | None]


class Connector(Protocol):
    """连接器协议：预览和读取外部数据源。

    实现方（FileConnector / PostgresConnector / RestConnector）需提供：
    - preview(source, limit): 读取前 limit 行返回 PreviewTable；
    - read(source): 异步迭代器，逐条 yield SourceRecord。
    """

    async def preview(
        self, source: ConnectorSource, limit: int = 100
    ) -> PreviewTable:  # pragma: no cover - 协议声明
        ...

    async def read(
        self, source: ConnectorSource
    ) -> AsyncIterator[SourceRecord]:  # pragma: no cover - 协议声明
        ...
