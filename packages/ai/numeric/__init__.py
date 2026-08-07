"""AI 数值计算工具子包。

公开接口：
- ``NumericToolFacade``: 门面编排层
- ``NumericPrincipal``: 调用主体
- ``NumericExecutionResult``: 执行结果
- ``NumericLimits``: 资源限制配置
- ``NumericError``: 预期错误
- ``EVALUATE_EXPRESSION_SCHEMA`` / ``DESCRIBE_SERIES_SCHEMA``: canonical schema
- ``NUMERIC_ENGINE_VERSION``: 引擎版本
"""

from packages.ai.numeric.contracts import (
    DEFAULT_QUANTILES,
    DEFAULT_STATISTICS,
    DESCRIBE_SERIES_SCHEMA,
    EVALUATE_EXPRESSION_SCHEMA,
    NUMERIC_ENGINE_VERSION,
    DescribeSeriesRequest,
    ExpressionOptions,
    NumericError,
    NumericExecutionResult,
    NumericLimits,
    NumericPrincipal,
    NumericSource,
    NumericSourceProvenance,
    NumericValue,
    ResolvedNumericInput,
    StatisticsResult,
)
from packages.ai.numeric.data_resolver import NumericDataResolver
from packages.ai.numeric.expression import SafeExpressionEngine
from packages.ai.numeric.service import NumericToolFacade
from packages.ai.numeric.statistics import SeriesStatisticsService

__all__ = [
    "DEFAULT_QUANTILES",
    "DEFAULT_STATISTICS",
    "DESCRIBE_SERIES_SCHEMA",
    "EVALUATE_EXPRESSION_SCHEMA",
    "NUMERIC_ENGINE_VERSION",
    "DescribeSeriesRequest",
    "ExpressionOptions",
    "NumericDataResolver",
    "NumericError",
    "NumericExecutionResult",
    "NumericLimits",
    "NumericPrincipal",
    "NumericSource",
    "NumericSourceProvenance",
    "NumericToolFacade",
    "NumericValue",
    "ResolvedNumericInput",
    "SafeExpressionEngine",
    "SeriesStatisticsService",
    "StatisticsResult",
]
