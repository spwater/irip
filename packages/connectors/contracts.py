"""连接器协议与值类型。

定义外部数据源接入的核心契约（IRIP Task 13）：

- ConnectorSource: 连接器无关的数据源描述（kind + kind 特定配置）。
- PreviewTable: 连接器预览结果（列名 + 行 + 总行数）。
- SourceRecord: 单条源记录（字段名→值）。
- MappingRule: 单条映射规则（源路径→目标变量版本）。
- MappingCandidate: 映射评分候选（变量编码 + 版本 + 分数 + 命中理由）。
- Connector: 连接器协议（preview 预览 / read 流式读取）。

安全约定：
- 配置中的敏感凭据仅以 ``secret_id`` 引用，绝不在值类型中携带明文 DSN/token。
- 连接器在内部按 secret_id 解析凭据，解析结果不返回、不记录日志。
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class ConnectorSource:
    """数据源描述（连接器无关）。

    Attributes:
        kind: 数据源类型（file / postgres / rest）。
        config: 类型特定配置字典：
            - file → ``{"path": str, "format": "csv"|"xlsx"|"json"}``
            - postgres → ``{"secret_id": str(uuid), "query": str}``
            - rest → ``{"secret_id": str(uuid), "path": str, "method": "GET"|"POST"}``
    """

    kind: Literal["file", "postgres", "rest"]
    config: dict


@dataclass(frozen=True)
class PreviewTable:
    """连接器预览结果。

    Attributes:
        columns: 列名元组（按源数据顺序）。
        rows: 行元组元组（最多 ``limit`` 行，每行为字段值元组）。
        row_count: 源数据可用总行数（可能超过预览行数）。
    """

    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    row_count: int


@dataclass(frozen=True)
class SourceRecord:
    """单条源记录（字段名→值）。

    Attributes:
        fields: 字段名到值的映射；缺失字段值为 None。
    """

    fields: dict[str, str | None]


@dataclass(frozen=True)
class MappingRule:
    """单条映射规则。

    Attributes:
        source_path: 源字段路径（列名或 JSON 路径）。
        target_variable_version_id: 目标已发布标准变量版本 ID。
        source_unit: 源数据单位代码（可选）。
        missing_policy: 缺失值策略（reject / null / default）。
        default_value: 默认值（missing_policy=default 时使用）。
    """

    source_path: str
    target_variable_version_id: UUID
    source_unit: str | None
    missing_policy: Literal["reject", "null", "default"]
    default_value: str | None


@dataclass(frozen=True)
class MappingCandidate:
    """映射评分候选。

    Attributes:
        variable_code: 变量编码。
        variable_version_id: 已发布变量版本 ID。
        score: 归一化分数（0.0-1.0）。
        reasons: 命中的评分组件名元组（如 ("exact_code", "unit_dimension")）。
    """

    variable_code: str
    variable_version_id: UUID
    score: float
    reasons: tuple[str, ...]


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
