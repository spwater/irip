"""AI 数值计算工具 — 门面编排层。

NumericToolFacade：编排解析 → 计算 → 摘要 → 审计 → 引用。

职责：
- evaluate_expression / describe_series 两个公开方法；
- 解析工具参数为类型对象；
- 调用 NumericDataResolver 解析数据来源；
- 调用 SafeExpressionEngine / SeriesStatisticsService 执行计算；
- 生成 summary / llm_data / audit_data / citation_params 三路分流结果；
- 错误转换为结构化 NumericError（code/message/path/details），details 不含原始值；
- CPU 计算通过 asyncio.to_thread() 在有界线程中执行；
- 并发信号量限制并行数值请求数。

设计文档 §13：执行结果、审计与引用
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np

from packages.ai.numeric.contracts import (
    DEFAULT_QUANTILES,
    NUMERIC_ENGINE_VERSION,
    DescribeSeriesRequest,
    ExpressionOptions,
    NumericError,
    NumericExecutionResult,
    NumericLimits,
    NumericPrincipal,
    NumericSource,
    NumericValue,
    ResolvedNumericInput,
    StatisticsResult,
)
from packages.ai.numeric.data_resolver import NumericDataResolver, FactQueryFactory
from packages.ai.numeric.expression import SafeExpressionEngine
from packages.ai.numeric.statistics import SeriesStatisticsService

#: 允许的 source_type 集合
_VALID_SOURCE_TYPES: frozenset[str] = frozenset({
    "scalar", "inline", "fact_series", "artifact_series",
})

#: describe_series 允许的 source_type（不含 scalar）
_SERIES_SOURCE_TYPES: frozenset[str] = frozenset({
    "inline", "fact_series", "artifact_series",
})


class NumericToolFacade:
    """数值工具门面编排层。

    Attributes:
        _resolver: 数据解析器。
        _expression_engine: 安全表达式引擎。
        _statistics_service: 描述统计服务。
        _limits: 资源限制配置。
        _semaphore: 并发信号量。
    """

    def __init__(
        self,
        data_resolver: NumericDataResolver,
        expression_engine: SafeExpressionEngine | None = None,
        statistics_service: SeriesStatisticsService | None = None,
        limits: NumericLimits | None = None,
        max_concurrent: int = 4,
    ) -> None:
        self._resolver = data_resolver
        self._limits = limits or NumericLimits()
        self._expression_engine = expression_engine or SafeExpressionEngine(self._limits)
        self._statistics_service = statistics_service or SeriesStatisticsService(self._limits)
        self._semaphore = asyncio.Semaphore(max_concurrent)

    # ---- evaluate_expression ----

    async def evaluate_expression(
        self,
        args: Mapping[str, Any],
        principal: NumericPrincipal,
    ) -> NumericExecutionResult:
        """执行 evaluate_expression 工具。

        Args:
            args: 工具参数（expression, variables, options）。
            principal: 调用主体。

        Returns:
            NumericExecutionResult: 三路分流结果。
        """
        async with self._semaphore:
            start_time = time.monotonic()

            # 1. 解析参数（同步，快速）
            expression = self._parse_expression(dict(args))
            variable_sources = self._parse_variables(dict(args))
            options = ExpressionOptions.from_dict(args.get("options"))
            expression_sha256 = hashlib.sha256(expression.encode("utf-8")).hexdigest()

            try:
                # 2. 解析数据来源（异步，可能访问数据库）
                resolved: dict[str, ResolvedNumericInput] = {}
                for source in variable_sources:
                    resolved[source.name] = await self._resolver.resolve(source, principal)

                # 3. 执行表达式（同步 CPU 计算，在线程中执行）
                result: NumericValue = await asyncio.to_thread(
                    self._expression_engine.evaluate,
                    expression, resolved, options,
                )

                # 4. 构建结果
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                return self._build_evaluate_result(
                    expression, expression_sha256, resolved, result,
                    options, elapsed_ms,
                )

            except NumericError as exc:
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                return self._build_error_result(
                    "evaluate_expression", expression, expression_sha256,
                    variable_sources, exc, options.to_audit_dict(), elapsed_ms,
                )
            except Exception:
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                internal_err = NumericError(
                    code="numeric_internal_error",
                    message="unexpected internal error",
                )
                return self._build_error_result(
                    "evaluate_expression", expression, expression_sha256,
                    variable_sources, internal_err, options.to_audit_dict(), elapsed_ms,
                )

    # ---- describe_series ----

    async def describe_series(
        self,
        args: Mapping[str, Any],
        principal: NumericPrincipal,
    ) -> NumericExecutionResult:
        """执行 describe_series 工具。

        Args:
            args: 工具参数（series, statistics, quantiles, variance_mode, null_policy）。
            principal: 调用主体。

        Returns:
            NumericExecutionResult: 三路分流结果。
        """
        async with self._semaphore:
            start_time = time.monotonic()

            # 1. 解析参数（同步，快速）
            series_source = self._parse_series(dict(args))
            request = self._parse_describe_request(dict(args))

            try:
                # 2. 解析数据来源（异步，可能访问数据库）
                resolved = await self._resolver.resolve(series_source, principal)

                # 3. 执行统计（同步 CPU 计算，在线程中执行）
                stats_result: StatisticsResult = await asyncio.to_thread(
                    self._statistics_service.describe,
                    resolved, request,
                )

                # 4. 构建结果
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                return self._build_describe_result(
                    series_source, resolved, stats_result, request, elapsed_ms,
                )

            except NumericError as exc:
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                return self._build_describe_error_result(
                    series_source, exc, request.to_audit_dict(), elapsed_ms,
                )
            except Exception:
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                internal_err = NumericError(
                    code="numeric_internal_error",
                    message="unexpected internal error",
                )
                return self._build_describe_error_result(
                    series_source, internal_err, request.to_audit_dict(), elapsed_ms,
                )

    # ---- 参数解析 ----

    def _parse_expression(self, args: dict[str, Any]) -> str:
        """解析 expression 参数。"""
        expression = args.get("expression")
        if not isinstance(expression, str):
            raise NumericError(
                code="numeric_expression_rejected",
                message="'expression' must be a string",
                path="expression",
            )
        if len(expression) == 0:
            raise NumericError(
                code="numeric_expression_rejected",
                message="'expression' must not be empty",
                path="expression",
            )
        if len(expression) > self._limits.max_expression_length:
            raise NumericError(
                code="numeric_size_limit",
                message=f"'expression' exceeds max length ({self._limits.max_expression_length})",
                path="expression",
            )
        return expression

    def _parse_variables(self, args: dict[str, Any]) -> list[NumericSource]:
        """解析 variables 参数。"""
        variables = args.get("variables")
        if not isinstance(variables, list):
            raise NumericError(
                code="numeric_invalid_source",
                message="'variables' must be an array",
                path="variables",
            )
        if len(variables) < 1:
            raise NumericError(
                code="numeric_invalid_source",
                message="'variables' must have at least 1 variable",
                path="variables",
            )
        if len(variables) > self._limits.max_variables:
            raise NumericError(
                code="numeric_size_limit",
                message=f"too many variables ({len(variables)} > {self._limits.max_variables})",
                path="variables",
            )

        seen_names: set[str] = set()
        sources: list[NumericSource] = []

        for i, var in enumerate(variables):
            if not isinstance(var, dict):
                raise NumericError(
                    code="numeric_invalid_source",
                    message=f"variable at index {i} must be an object",
                    path=f"variables[{i}]",
                )

            source = self._parse_source(var, f"variables[{i}]")

            if source.name in seen_names:
                raise NumericError(
                    code="numeric_invalid_source",
                    message=f"duplicate variable name: {source.name}",
                    path=f"variables[{i}].name",
                )
            seen_names.add(source.name)
            sources.append(source)

        return sources

    def _parse_series(self, args: dict[str, Any]) -> NumericSource:
        """解析 series 参数（describe_series）。"""
        series = args.get("series")
        if not isinstance(series, dict):
            raise NumericError(
                code="numeric_invalid_source",
                message="'series' must be an object",
                path="series",
            )

        source = self._parse_source(series, "series")

        # describe_series 不接受 scalar
        if source.source_type == "scalar":
            raise NumericError(
                code="numeric_invalid_source",
                message="describe_series does not accept scalar source_type",
                path="series.source_type",
            )

        return source

    def _parse_source(self, var: dict[str, Any], path_prefix: str) -> NumericSource:
        """解析单个数据来源对象。"""
        # 检查未知字段
        allowed_keys = {
            "name", "source_type", "value", "values", "unit",
            "fact_id", "artifact_id", "series_index", "column_name",
        }
        unknown_keys = set(var.keys()) - allowed_keys
        if unknown_keys:
            raise NumericError(
                code="numeric_invalid_source",
                message=f"unknown fields: {sorted(unknown_keys)}",
                path=f"{path_prefix}",
            )

        name = var.get("name")
        if not isinstance(name, str) or len(name) == 0:
            raise NumericError(
                code="numeric_invalid_source",
                message="'name' must be a non-empty string",
                path=f"{path_prefix}.name",
            )
        # 变量名格式检查
        import re
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", name):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"invalid variable name: {name}",
                path=f"{path_prefix}.name",
            )

        source_type = var.get("source_type")
        if not isinstance(source_type, str) or source_type not in _VALID_SOURCE_TYPES:
            raise NumericError(
                code="numeric_invalid_source",
                message=f"invalid source_type: {source_type}",
                path=f"{path_prefix}.source_type",
            )

        # source_type 特定字段检查
        value = var.get("value")
        values = var.get("values")
        unit = var.get("unit")
        fact_id = var.get("fact_id")
        artifact_id = var.get("artifact_id")
        series_index = var.get("series_index")
        column_name = var.get("column_name")

        if source_type == "scalar":
            if value is None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="scalar source requires 'value'",
                    path=f"{path_prefix}.value",
                )
            if values is not None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="scalar source must not have 'values'",
                    path=f"{path_prefix}.values",
                )
            if fact_id or artifact_id or series_index is not None or column_name:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="scalar source must not have platform fields",
                    path=f"{path_prefix}",
                )
        elif source_type == "inline":
            if values is None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="inline source requires 'values'",
                    path=f"{path_prefix}.values",
                )
            if value is not None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="inline source must not have 'value'",
                    path=f"{path_prefix}.value",
                )
            if fact_id or artifact_id or series_index is not None or column_name:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="inline source must not have platform fields",
                    path=f"{path_prefix}",
                )
        elif source_type == "fact_series":
            if not fact_id:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="fact_series requires 'fact_id'",
                    path=f"{path_prefix}.fact_id",
                )
            if series_index is None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="fact_series requires 'series_index'",
                    path=f"{path_prefix}.series_index",
                )
            if not column_name:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="fact_series requires 'column_name'",
                    path=f"{path_prefix}.column_name",
                )
            # 平台来源不能指定 unit
            if unit is not None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="platform source must not specify 'unit'",
                    path=f"{path_prefix}.unit",
                )
            if value is not None or values is not None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="fact_series must not have 'value' or 'values'",
                    path=f"{path_prefix}",
                )
            if not isinstance(series_index, int) or isinstance(series_index, bool) or series_index < 0:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="series_index must be a non-negative integer",
                    path=f"{path_prefix}.series_index",
                )
            if not isinstance(column_name, str) or len(column_name) < 1 or len(column_name) > 128:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="column_name must be 1-128 characters",
                    path=f"{path_prefix}.column_name",
                )
        elif source_type == "artifact_series":
            if not artifact_id:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="artifact_series requires 'artifact_id'",
                    path=f"{path_prefix}.artifact_id",
                )
            if series_index is None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="artifact_series requires 'series_index'",
                    path=f"{path_prefix}.series_index",
                )
            if not column_name:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="artifact_series requires 'column_name'",
                    path=f"{path_prefix}.column_name",
                )
            if unit is not None:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="platform source must not specify 'unit'",
                    path=f"{path_prefix}.unit",
                )
            if not isinstance(series_index, int) or isinstance(series_index, bool) or series_index < 0:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="series_index must be a non-negative integer",
                    path=f"{path_prefix}.series_index",
                )

        # unit 校验（仅 scalar/inline）
        if unit is not None:
            if not isinstance(unit, str):
                raise NumericError(
                    code="numeric_invalid_source",
                    message="unit must be a string",
                    path=f"{path_prefix}.unit",
                )
            if len(unit) > 64:
                raise NumericError(
                    code="numeric_invalid_source",
                    message="unit exceeds 64 characters",
                    path=f"{path_prefix}.unit",
                )

        return NumericSource(
            name=name,
            source_type=source_type,
            value=value if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
            values=values if isinstance(values, list) else None,
            unit=unit if isinstance(unit, str) else None,
            fact_id=fact_id if isinstance(fact_id, str) else None,
            artifact_id=artifact_id if isinstance(artifact_id, str) else None,
            series_index=series_index if isinstance(series_index, int) and not isinstance(series_index, bool) else None,
            column_name=column_name if isinstance(column_name, str) else None,
        )

    def _parse_describe_request(self, args: dict[str, Any]) -> DescribeSeriesRequest:
        """解析 describe_series 请求参数。"""
        statistics = args.get("statistics")
        if statistics is not None:
            if not isinstance(statistics, list):
                raise NumericError(
                    code="numeric_invalid_source",
                    message="'statistics' must be an array",
                    path="statistics",
                )
            for i, s in enumerate(statistics):
                if not isinstance(s, str):
                    raise NumericError(
                        code="numeric_invalid_source",
                        message=f"statistics[{i}] must be a string",
                        path=f"statistics[{i}]",
                    )

        quantiles = args.get("quantiles", list(DEFAULT_QUANTILES))
        if not isinstance(quantiles, list):
            raise NumericError(
                code="numeric_invalid_source",
                message="'quantiles' must be an array",
                path="quantiles",
            )
        if len(quantiles) > 20:
            raise NumericError(
                code="numeric_size_limit",
                message="'quantiles' exceeds max 20 items",
                path="quantiles",
            )
        for i, q in enumerate(quantiles):
            if not isinstance(q, (int, float)) or isinstance(q, bool):
                raise NumericError(
                    code="numeric_invalid_source",
                    message=f"quantiles[{i}] must be a number",
                    path=f"quantiles[{i}]",
                )
            if q < 0.0 or q > 1.0:
                raise NumericError(
                    code="numeric_domain_error",
                    message=f"quantiles[{i}] must be in [0, 1]",
                    path=f"quantiles[{i}]",
                )

        variance_mode = args.get("variance_mode", "both")
        if variance_mode not in ("population", "sample", "both"):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"invalid variance_mode: {variance_mode}",
                path="variance_mode",
            )

        null_policy = args.get("null_policy", "fail")
        if null_policy not in ("fail", "omit", "propagate"):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"invalid null_policy: {null_policy}",
                path="null_policy",
            )

        return DescribeSeriesRequest(
            statistics=tuple(statistics) if statistics is not None else None,
            quantiles=tuple(float(q) for q in quantiles),
            variance_mode=variance_mode,
            null_policy=null_policy,
        )

    # ---- 结果构建 ----

    def _build_evaluate_result(
        self,
        expression: str,
        expression_sha256: str,
        resolved: dict[str, ResolvedNumericInput],
        result: NumericValue,
        options: ExpressionOptions,
        elapsed_ms: float,
    ) -> NumericExecutionResult:
        """构建 evaluate_expression 成功结果。"""
        # 生成 llm_data
        llm_data = self._build_evaluate_llm_data(result)

        # 生成 result digest
        result_digest = self._compute_result_digest(result)

        # 生成审计数据
        audit_data = self._build_evaluate_audit(
            expression, expression_sha256, resolved, result,
            result_digest, options, elapsed_ms,
        )

        # 生成 citation_params
        citation_params = self._build_evaluate_citation(
            expression, expression_sha256, resolved,
            result_digest, options,
        )

        # 生成 summary
        if result.kind == "scalar":
            if result.is_null_scalar:
                summary = f"evaluate_expression: result is null (expression: {expression[:80]})"
            else:
                summary = f"evaluate_expression: {result.scalar} (expression: {expression[:80]})"
        else:
            count = len(result.vector) if result.vector is not None else 0
            truncated = count > self._limits.vector_preview_threshold
            summary = f"evaluate_expression: vector result with {count} values (expression: {expression[:80]})"

        return NumericExecutionResult(
            summary=summary,
            llm_data=llm_data,
            audit_data=audit_data,
            citation_params=citation_params,
        )

    def _build_evaluate_llm_data(self, result: NumericValue) -> dict[str, Any]:
        """构建 evaluate_expression 的 LLM 数据。"""
        if result.kind == "scalar":
            return {
                "result_type": "scalar",
                "value": result.scalar,
                "unit": result.unit,
                "warnings": result.warnings,
            }

        # vector
        vector = result.vector
        null_mask = result.null_mask
        count = len(vector) if vector is not None else 0

        if count <= self._limits.vector_preview_threshold:
            values_list = [self._normalize_zero(float(v)) for v in vector]
            return {
                "result_type": "vector",
                "count": count,
                "values": values_list,
                "unit": result.unit,
                "warnings": result.warnings,
            }

        # 截断预览
        preview_count = 5
        head = [self._normalize_zero(float(v)) for v in vector[:preview_count]]
        tail = [self._normalize_zero(float(v)) for v in vector[-preview_count:]]
        sha256 = self._compute_vector_digest(vector)

        return {
            "result_type": "vector_preview",
            "count": count,
            "head": head,
            "tail": tail,
            "sha256": sha256,
            "unit": result.unit,
            "truncated": True,
            "warnings": result.warnings,
        }

    def _build_evaluate_audit(
        self,
        expression: str,
        expression_sha256: str,
        resolved: dict[str, ResolvedNumericInput],
        result: NumericValue,
        result_digest: str,
        options: ExpressionOptions,
        elapsed_ms: float,
    ) -> dict[str, Any]:
        """构建审计数据（不含原始数组）。"""
        sources_audit = []
        for name, inp in resolved.items():
            sources_audit.append({
                "name": name,
                "source_type": inp.source_provenance.source_type,
                "count": inp.length,
                "unit": inp.unit,
                "input_sha256": inp.input_digest,
                "fact_id": str(inp.source_provenance.fact_id) if inp.source_provenance.fact_id else None,
                "artifact_id": str(inp.source_provenance.artifact_id) if inp.source_provenance.artifact_id else None,
                "series_index": inp.source_provenance.series_index,
                "column_name": inp.source_provenance.column_name,
            })

        result_type = result.kind
        truncated = False
        if result.kind == "vector" and result.vector is not None:
            truncated = len(result.vector) > self._limits.vector_preview_threshold

        return {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": "evaluate_expression",
            "expression": expression,
            "expression_sha256": expression_sha256,
            "sources": sources_audit,
            "policies": options.to_audit_dict(),
            "result": {
                "result_type": result_type,
                "sha256": result_digest,
                "truncated": truncated,
            },
            "warnings": result.warnings,
            "duration_ms": round(elapsed_ms, 3),
        }

    def _build_evaluate_citation(
        self,
        expression: str,
        expression_sha256: str,
        resolved: dict[str, ResolvedNumericInput],
        result_digest: str,
        options: ExpressionOptions,
    ) -> dict[str, Any]:
        """构建 citation 参数（净化、稳定排序）。"""
        sources_cite = []
        for name in sorted(resolved.keys()):
            inp = resolved[name]
            sources_cite.append({
                "name": name,
                "source_type": inp.source_provenance.source_type,
                "input_sha256": inp.input_digest,
                "fact_id": str(inp.source_provenance.fact_id) if inp.source_provenance.fact_id else None,
                "artifact_id": str(inp.source_provenance.artifact_id) if inp.source_provenance.artifact_id else None,
                "series_index": inp.source_provenance.series_index,
                "column_name": inp.source_provenance.column_name,
            })

        return {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": "evaluate_expression",
            "expression": expression,
            "expression_sha256": expression_sha256,
            "sources": sources_cite,
            "policies": options.to_audit_dict(),
            "result_sha256": result_digest,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # ---- describe_series 结果构建 ----

    def _build_describe_result(
        self,
        source: NumericSource,
        resolved: ResolvedNumericInput,
        stats_result: StatisticsResult,
        request: DescribeSeriesRequest,
        elapsed_ms: float,
    ) -> NumericExecutionResult:
        """构建 describe_series 成功结果。"""
        # llm_data
        llm_data = {
            "result_type": "statistics",
            "statistics": stats_result.values,
            "warnings": stats_result.warnings,
        }

        # 审计数据
        audit_data = {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": "describe_series",
            "sources": [{
                "name": resolved.name,
                "source_type": resolved.source_provenance.source_type,
                "count": resolved.length,
                "unit": resolved.unit,
                "input_sha256": resolved.input_digest,
                "fact_id": str(resolved.source_provenance.fact_id) if resolved.source_provenance.fact_id else None,
                "artifact_id": str(resolved.source_provenance.artifact_id) if resolved.source_provenance.artifact_id else None,
                "series_index": resolved.source_provenance.series_index,
                "column_name": resolved.source_provenance.column_name,
            }],
            "policies": request.to_audit_dict(),
            "statistics_requested": list(request.effective_statistics),
            "quantiles": list(request.quantiles),
            "result": {
                "result_type": "statistics",
                "sha256": stats_result.result_digest,
            },
            "warnings": stats_result.warnings,
            "duration_ms": round(elapsed_ms, 3),
        }

        # citation_params
        citation_params = {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": "describe_series",
            "sources": [{
                "name": resolved.name,
                "source_type": resolved.source_provenance.source_type,
                "input_sha256": resolved.input_digest,
                "fact_id": str(resolved.source_provenance.fact_id) if resolved.source_provenance.fact_id else None,
                "artifact_id": str(resolved.source_provenance.artifact_id) if resolved.source_provenance.artifact_id else None,
                "series_index": resolved.source_provenance.series_index,
                "column_name": resolved.source_provenance.column_name,
            }],
            "policies": request.to_audit_dict(),
            "result_sha256": stats_result.result_digest,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # summary
        summary = f"describe_series: {resolved.length} values, {len(stats_result.values)} statistics"

        return NumericExecutionResult(
            summary=summary,
            llm_data=llm_data,
            audit_data=audit_data,
            citation_params=citation_params,
        )

    # ---- 错误结果构建 ----

    def _build_error_result(
        self,
        tool_name: str,
        expression: str,
        expression_sha256: str,
        sources: list[NumericSource],
        error: NumericError,
        policies: dict[str, Any],
        elapsed_ms: float,
    ) -> NumericExecutionResult:
        """构建 evaluate_expression 错误结果。"""
        error_dict = error.to_llm_dict()

        llm_data = {"error": error_dict}

        sources_audit = [
            {
                "name": s.name,
                "source_type": s.source_type,
            }
            for s in sources
        ]

        audit_data = {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": tool_name,
            "expression": expression,
            "expression_sha256": expression_sha256,
            "sources": sources_audit,
            "policies": policies,
            "error": {"code": error.code, "path": error.path},
            "warnings": [],
            "duration_ms": round(elapsed_ms, 3),
        }

        citation_params = {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": tool_name,
            "expression": expression,
            "expression_sha256": expression_sha256,
            "error": error.code,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        return NumericExecutionResult(
            summary=f"evaluate_expression failed: {error.message}",
            llm_data=llm_data,
            audit_data=audit_data,
            citation_params=citation_params,
        )

    def _build_describe_error_result(
        self,
        source: NumericSource,
        error: NumericError,
        policies: dict[str, Any],
        elapsed_ms: float,
    ) -> NumericExecutionResult:
        """构建 describe_series 错误结果。"""
        error_dict = error.to_llm_dict()

        llm_data = {"error": error_dict}

        audit_data = {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": "describe_series",
            "sources": [{"name": source.name, "source_type": source.source_type}],
            "policies": policies,
            "error": {"code": error.code, "path": error.path},
            "warnings": [],
            "duration_ms": round(elapsed_ms, 3),
        }

        citation_params = {
            "engine_version": NUMERIC_ENGINE_VERSION,
            "tool": "describe_series",
            "sources": [{"name": source.name, "source_type": source.source_type}],
            "error": error.code,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        return NumericExecutionResult(
            summary=f"describe_series failed: {error.message}",
            llm_data=llm_data,
            audit_data=audit_data,
            citation_params=citation_params,
        )

    # ---- 辅助 ----

    def _compute_result_digest(self, result: NumericValue) -> str:
        """计算结果 digest。"""
        if result.kind == "scalar":
            if result.is_null_scalar:
                return hashlib.sha256(b"null").hexdigest()
            val_bytes = np.float64(result.scalar).tobytes()
            return hashlib.sha256(val_bytes).hexdigest()
        # vector
        return self._compute_vector_digest(result.vector)

    def _compute_vector_digest(self, vector: np.ndarray | None) -> str:
        """计算向量 digest。"""
        if vector is None:
            return hashlib.sha256(b"empty").hexdigest()
        return hashlib.sha256(
            np.ascontiguousarray(vector.astype(np.float64)).tobytes()
        ).hexdigest()

    def _normalize_zero(self, val: float) -> float:
        """规范化 -0.0 为 0.0。"""
        if val == 0.0:
            return 0.0
        return val
