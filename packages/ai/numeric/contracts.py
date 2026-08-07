"""AI 数值计算工具 — 契约与内部数据类型。

定义数值子包的全部内部数据类型、canonical schema 常量、资源限制配置。
作为所有数值计算模块的共享基石。

设计文档: docs/superpowers/specs/2026-08-07-ai-numeric-tools-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

#: 数值引擎版本，写入审计和 citation_params。
NUMERIC_ENGINE_VERSION = "numeric-v1"

#: 变量名合法字符正则。
VARIABLE_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"

#: describe_series 默认统计项（服务端固定顺序）。
DEFAULT_STATISTICS: tuple[str, ...] = (
    "count",
    "missing_count",
    "sum",
    "mean",
    "variance",
    "std",
    "min",
    "max",
    "median",
    "quantile",
    "skewness",
    "kurtosis",
)

#: 全部合法统计项名称集合。
ALL_STATISTIC_NAMES: frozenset[str] = frozenset(DEFAULT_STATISTICS) | {"valid_count"}

#: 默认分位数列表。
DEFAULT_QUANTILES: tuple[float, ...] = (0.25, 0.5, 0.75)


# =============================================================================
# 资源限制配置
# =============================================================================


@dataclass(frozen=True)
class NumericLimits:
    """数值计算资源限制（不可由模型覆盖）。

    Attributes:
        max_expression_length: 表达式最大字符数。
        max_ast_nodes: AST 最大节点数。
        max_ast_depth: AST 最大深度。
        max_variables: 单次请求最大变量数。
        max_inline_series_length: 内联序列每变量最大长度。
        max_platform_series_length: 平台序列每变量最大长度。
        vector_preview_threshold: 向量完整返回阈值，超过则只返回预览。
        computation_timeout_seconds: 单次 CPU 计算超时秒数。
        max_pow_exponent_abs: 幂运算指数绝对值上限。
        max_round_digits: round 函数小数位数绝对值上限。
    """

    max_expression_length: int = 512
    max_ast_nodes: int = 128
    max_ast_depth: int = 16
    max_variables: int = 16
    max_inline_series_length: int = 10_000
    max_platform_series_length: int = 100_000
    vector_preview_threshold: int = 1_000
    computation_timeout_seconds: float = 3.0
    max_pow_exponent_abs: int = 100
    max_round_digits: int = 15


# =============================================================================
# 主体与来源
# =============================================================================


@dataclass(frozen=True)
class NumericPrincipal:
    """数值工具调用主体（从已认证请求上下文构造，不从工具参数构造）。

    Attributes:
        user_id: 已认证用户 UUID。
        department_id: 当前部门 UUID（RLS 作用域）。
        roles: 用户角色代码元组。
    """

    user_id: UUID
    department_id: UUID
    roles: tuple[str, ...]


@dataclass(frozen=True)
class NumericSourceProvenance:
    """数据来源溯源信息（只包含元数据，不含原始数组）。

    Attributes:
        source_type: 来源类型（scalar/inline/fact_series/artifact_series）。
        fact_id: Fact UUID（仅 fact_series）。
        artifact_id: Artifact UUID（fact_series/artifact_series）。
        artifact_sha256: 权威 artifact 内容 SHA-256。
        series_index: 序列索引。
        column_name: 列名。
        row_count: 行数。
    """

    source_type: str
    fact_id: UUID | None = None
    artifact_id: UUID | None = None
    artifact_sha256: str | None = None
    series_index: int | None = None
    column_name: str | None = None
    row_count: int = 0


@dataclass(frozen=True)
class ResolvedNumericInput:
    """解析后的数值输入（标量或一维序列）。

    对于标量来源，``values`` 为 0 维数组，``null_mask`` 为 0 维 False。
    对于序列来源，``values`` 和 ``null_mask`` 为同长度 1 维数组。

    Attributes:
        name: 变量名。
        values: float64 数组（标量为 0 维，序列为 1 维）。
        null_mask: 布尔掩码（与 values 同形状）。
        unit: 单位标签（None 表示未知，"1" 表示无量纲）。
        source_provenance: 来源溯源信息。
        input_digest: 输入规范二进制表示的 SHA-256。
    """

    name: str
    values: NDArray[np.float64]
    null_mask: NDArray[np.bool_]
    unit: str | None
    source_provenance: NumericSourceProvenance
    input_digest: str

    @property
    def is_scalar(self) -> bool:
        """是否为标量来源（0 维数组）。"""
        return self.values.ndim == 0

    @property
    def length(self) -> int:
        """序列长度（标量返回 1）。"""
        if self.values.ndim == 0:
            return 1
        return int(self.values.shape[0])


@dataclass(frozen=True)
class NumericSource:
    """数据来源规格（输入解析类型，支持四种来源）。

    Attributes:
        name: 变量名。
        source_type: 来源类型（scalar/inline/fact_series/artifact_series）。
        value: 标量值（仅 scalar）。
        values: 内联序列值列表（仅 inline，元素可为 None）。
        unit: 单位标签（仅 scalar/inline，None 表示未知，"1" 表示无量纲）。
        fact_id: Fact UUID 字符串（仅 fact_series）。
        artifact_id: Artifact UUID 字符串（仅 artifact_series）。
        series_index: 序列索引（fact_series/artifact_series）。
        column_name: 列名（fact_series/artifact_series）。
    """

    name: str
    source_type: str
    value: float | None = None
    values: list[float | None] | None = None
    unit: str | None = None
    fact_id: str | None = None
    artifact_id: str | None = None
    series_index: int | None = None
    column_name: str | None = None


# =============================================================================
# 选项与请求
# =============================================================================


@dataclass(frozen=True)
class ExpressionOptions:
    """表达式求值选项。

    Attributes:
        angle_unit: 角度单位（radian/degree）。
        null_policy: 空值策略（fail/propagate）。
        numeric_coercion: 数值转换策略（仅 strict）。
        broadcast_policy: 广播策略（仅 scalar_only）。
        domain_error: 定义域错误策略（仅 fail）。
        numeric_type: 数值类型（仅 float64）。
    """

    angle_unit: str = "radian"
    null_policy: str = "fail"
    numeric_coercion: str = "strict"
    broadcast_policy: str = "scalar_only"
    domain_error: str = "fail"
    numeric_type: str = "float64"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ExpressionOptions:
        """从字典构造选项，使用严格默认值。

        传入尚未支持的枚举值时抛出 NumericError。
        """
        if data is None:
            return cls()

        angle_unit = data.get("angle_unit", "radian")
        null_policy = data.get("null_policy", "fail")
        numeric_coercion = data.get("numeric_coercion", "strict")
        broadcast_policy = data.get("broadcast_policy", "scalar_only")
        domain_error = data.get("domain_error", "fail")
        numeric_type = data.get("numeric_type", "float64")

        # 验证枚举值（传入尚未支持的值必须报错）
        if angle_unit not in ("radian", "degree"):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unsupported angle_unit: {angle_unit}",
                path="options.angle_unit",
            )
        if null_policy not in ("fail", "propagate"):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unsupported null_policy: {null_policy}",
                path="options.null_policy",
            )
        if numeric_coercion != "strict":
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unsupported numeric_coercion: {numeric_coercion}",
                path="options.numeric_coercion",
            )
        if broadcast_policy != "scalar_only":
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unsupported broadcast_policy: {broadcast_policy}",
                path="options.broadcast_policy",
            )
        if domain_error != "fail":
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unsupported domain_error: {domain_error}",
                path="options.domain_error",
            )
        if numeric_type != "float64":
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unsupported numeric_type: {numeric_type}",
                path="options.numeric_type",
            )

        return cls(
            angle_unit=angle_unit,
            null_policy=null_policy,
            numeric_coercion=numeric_coercion,
            broadcast_policy=broadcast_policy,
            domain_error=domain_error,
            numeric_type=numeric_type,
        )

    def to_audit_dict(self) -> dict[str, str]:
        """返回审计用策略字典。"""
        return {
            "null_policy": self.null_policy,
            "numeric_type": self.numeric_type,
            "broadcast_policy": self.broadcast_policy,
            "angle_unit": self.angle_unit,
        }


@dataclass(frozen=True)
class DescribeSeriesRequest:
    """describe_series 请求参数。

    Attributes:
        statistics: 请求的统计项列表（None 表示全部默认项）。
        quantiles: 分位数列表。
        variance_mode: 方差口径（population/sample/both）。
        null_policy: 空值策略（fail/omit/propagate）。
    """

    statistics: tuple[str, ...] | None = None
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES
    variance_mode: str = "both"
    null_policy: str = "fail"

    @property
    def effective_statistics(self) -> tuple[str, ...]:
        """实际计算的统计项（去重后按服务端固定顺序排列）。"""
        if self.statistics is None:
            return DEFAULT_STATISTICS
        seen: set[str] = set()
        ordered: list[str] = []
        for stat in DEFAULT_STATISTICS:
            if stat in self.statistics and stat not in seen:
                seen.add(stat)
                ordered.append(stat)
        return tuple(ordered)

    def to_audit_dict(self) -> dict[str, Any]:
        """返回审计用策略字典。"""
        return {
            "null_policy": self.null_policy,
            "variance_mode": self.variance_mode,
        }


# =============================================================================
# 结果类型
# =============================================================================


@dataclass
class NumericValue:
    """表达式求值结果 — 标量或向量。

    Attributes:
        kind: 结果类型（"scalar" 或 "vector"）。
        scalar: 标量值（kind="scalar" 时有效，None 表示 null 标量）。
        vector: 向量值数组（kind="vector" 时有效）。
        null_mask: 向量空值掩码（kind="vector" 时有效）。
        unit: 单位标签（None 表示未知，"1" 表示无量纲）。
        warnings: 警告代码列表。
        is_null_scalar: 标量是否为 null（propagate 策略下聚合可能产生）。
    """

    kind: str
    scalar: float | None = None
    vector: NDArray[np.float64] | None = None
    null_mask: NDArray[np.bool_] | None = None
    unit: str | None = None
    warnings: list[str] = field(default_factory=list)
    is_null_scalar: bool = False


@dataclass
class StatisticsResult:
    """描述统计结果。

    Attributes:
        values: 统计项名 → 值字典（值可为 float 或 None）。
        warnings: 警告代码列表。
        result_digest: 结果规范二进制表示的 SHA-256。
    """

    values: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    result_digest: str = ""


@dataclass(frozen=True)
class NumericExecutionResult:
    """数值工具执行结果（三路分流：LLM 数据 / 审计数据 / 引用参数）。

    Attributes:
        summary: 人类可读摘要。
        llm_data: 第二轮 LLM tool message 数据（不含原始大型数组）。
        audit_data: 持久化审计数据（压缩，不含原始数组）。
        citation_params: 签名引用参数（净化、稳定排序）。
    """

    summary: str
    llm_data: dict[str, Any]
    audit_data: dict[str, Any]
    citation_params: dict[str, Any]


# =============================================================================
# 错误类型
# =============================================================================


@dataclass
class NumericError(Exception):
    """数值计算预期错误（可结构化返回给 LLM）。

    Attributes:
        code: 错误码字符串（如 "numeric_domain_error"）。
        message: 面向用户的错误描述。
        path: 错误路径（如 "expression.log"）。
        details: 结构化详情（不含原始值，只含安全的数量/变量名/字段路径）。
    """

    code: str
    message: str
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_llm_dict(self) -> dict[str, Any]:
        """转换为 LLM 可读的错误字典。"""
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            result["path"] = self.path
        if self.details:
            result["details"] = self.details
        return result


# =============================================================================
# Canonical Schema 常量（单一真相来源）
# =============================================================================

#: 变量来源对象 schema（被 evaluate_expression 和 describe_series 共享）。
_VARIABLE_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "pattern": VARIABLE_NAME_PATTERN,
            "description": "变量名，匹配 ^[A-Za-z_][A-Za-z0-9_]{0,63}$",
        },
        "source_type": {
            "type": "string",
            "enum": ["scalar", "inline", "fact_series"],
            "description": "数据来源类型",
        },
        "value": {
            "type": "number",
            "description": "标量值（仅 source_type=scalar）",
        },
        "values": {
            "type": "array",
            "items": {"type": ["number", "null"]},
            "description": "内联序列值数组（仅 source_type=inline，元素可为 null）",
        },
        "unit": {
            "type": "string",
            "maxLength": 64,
            "description": '单位标签（仅 scalar/inline；省略=未知，"1"=无量纲）',
        },
        "fact_id": {
            "type": "string",
            "format": "uuid",
            "description": "Fact UUID（仅 source_type=fact_series）",
        },
        "artifact_id": {
            "type": "string",
            "format": "uuid",
            "description": "Artifact UUID（仅 source_type=artifact_series）",
        },
        "series_index": {
            "type": "integer",
            "minimum": 0,
            "description": "序列索引（fact_series/artifact_series，非负整数）",
        },
        "column_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "description": "列名（fact_series/artifact_series）",
        },
    },
    "required": ["name", "source_type"],
    "additionalProperties": False,
}

#: evaluate_expression 工具 canonical schema。
EVALUATE_EXPRESSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "description": (
                "数学表达式，支持 +、-、*、/、**、% 以及白名单函数。"
                "需要单个最终值时应在表达式中聚合（sum/mean/var 等）。"
            ),
        },
        "variables": {
            "type": "array",
            "minItems": 0,
            "maxItems": 16,
            "items": _VARIABLE_SOURCE_SCHEMA,
            "description": "变量数组，每个变量指定名称和数据来源",
        },
        "options": {
            "type": "object",
            "properties": {
                "angle_unit": {
                    "type": "string",
                    "enum": ["radian", "degree"],
                },
                "null_policy": {
                    "type": "string",
                    "enum": ["fail", "propagate"],
                },
                "numeric_coercion": {
                    "type": "string",
                    "enum": ["strict"],
                },
                "broadcast_policy": {
                    "type": "string",
                    "enum": ["scalar_only"],
                },
                "domain_error": {
                    "type": "string",
                    "enum": ["fail"],
                },
                "numeric_type": {
                    "type": "string",
                    "enum": ["float64"],
                },
            },
            "additionalProperties": False,
        },
    },
    "required": ["expression", "variables"],
    "additionalProperties": False,
}

#: describe_series 工具 canonical schema。
DESCRIBE_SERIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "series": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "pattern": VARIABLE_NAME_PATTERN,
                },
                "source_type": {
                    "type": "string",
                    "enum": ["inline", "fact_series", "artifact_series"],
                    "description": "不接受 scalar",
                },
                "values": {
                    "type": "array",
                    "items": {"type": ["number", "null"]},
                },
                "unit": {
                    "type": "string",
                    "maxLength": 64,
                },
                "fact_id": {
                    "type": "string",
                    "format": "uuid",
                },
                "artifact_id": {
                    "type": "string",
                    "format": "uuid",
                },
                "series_index": {
                    "type": "integer",
                    "minimum": 0,
                },
                "column_name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
            },
            "required": ["name", "source_type"],
            "additionalProperties": False,
        },
        "statistics": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(DEFAULT_STATISTICS),
            },
            "uniqueItems": True,
            "description": "请求的统计项；省略返回全部默认统计项",
        },
        "quantiles": {
            "type": "array",
            "items": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "maxItems": 20,
            "description": "分位数列表，默认 [0.25, 0.5, 0.75]",
        },
        "variance_mode": {
            "type": "string",
            "enum": ["population", "sample", "both"],
            "description": "方差口径，默认 both",
        },
        "null_policy": {
            "type": "string",
            "enum": ["fail", "omit", "propagate"],
            "description": "空值策略，默认 fail",
        },
    },
    "required": ["series"],
    "additionalProperties": False,
}
