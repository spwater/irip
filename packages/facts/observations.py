"""L2 事实观察值对象（IRIP Task 15）。

定义创建事实时输入的观察值对象和服务返回的持久化观察值对象：
- RawObservationInput: 创建事实时输入的原始观察值。
- NormalizedObservationInput: 创建事实时输入的标准化观察值。
- RawObservation: 持久化的原始观察值（服务返回值）。
- NormalizedObservation: 持久化的标准化观察值（服务返回值）。
- FactRevisionRef: 事实修订引用（服务返回值）。

所有值对象均为 frozen dataclass，符合不可变值对象设计约定。
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RawObservationInput:
    """创建事实时输入的原始观察值。

    Attributes:
        source_path: 来源字段名/路径。
        source_value: 原始值（字符串形式）。
        source_unit: 原始单位（可选）。
        source_name: 原始列名/文件名（可选）。
        artifact_id: 工件 ID（可选，来自文件时关联）。
        id: 预生成 UUID（可选，用于标准化观察值在创建前引用）。
    """

    source_path: str
    source_value: str
    source_unit: str | None = None
    source_name: str | None = None
    artifact_id: UUID | None = None
    id: UUID | None = None


@dataclass(frozen=True)
class NormalizedObservationInput:
    """创建事实时输入的标准化观察值。

    raw_observation_id 必须非空——标准化观察值必须引用一个原始观察值。
    服务层会校验此不变量。

    Attributes:
        variable_version_id: 标准变量版本 ID（L1 标准）。
        raw_observation_id: 原始观察值 ID（必须非空，引用 raw_observation）。
        value: 标准化值（字符串形式）。
        unit: 标准化单位。
    """

    variable_version_id: UUID
    raw_observation_id: UUID | None
    value: str
    unit: str | None = None


@dataclass(frozen=True)
class RawObservation:
    """持久化的原始观察值（服务返回值）。

    Attributes:
        id: 观察 UUID。
        fact_revision_id: 事实修订 ID。
        source_path: 来源字段名/路径。
        source_value: 原始值。
        source_unit: 原始单位。
        source_name: 原始列名/文件名。
        artifact_id: 工件 ID。
    """

    id: UUID
    fact_revision_id: UUID
    source_path: str
    source_value: str
    source_unit: str | None
    source_name: str | None
    artifact_id: UUID | None


@dataclass(frozen=True)
class NormalizedObservation:
    """持久化的标准化观察值（服务返回值）。

    Attributes:
        id: 观察 UUID。
        fact_revision_id: 事实修订 ID。
        variable_version_id: 标准变量版本 ID。
        raw_observation_id: 原始观察值 ID。
        value: 标准化值。
        unit: 标准化单位。
    """

    id: UUID
    fact_revision_id: UUID
    variable_version_id: UUID
    raw_observation_id: UUID
    value: str
    unit: str | None


@dataclass(frozen=True)
class FactRevisionRef:
    """事实修订引用（服务返回值）。

    包含事实与修订的关键标识，供 API 层直接序列化。

    Attributes:
        fact_id: 事实 UUID。
        revision: 修订号。
        revision_id: 修订 UUID。
        fact_type: 事实类型。
        subject_id: 主体标识。
        status: 事实状态（active / superseded / withdrawn）。
    """

    fact_id: UUID
    revision: int
    revision_id: UUID
    fact_type: str
    subject_id: str
    status: str
