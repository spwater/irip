"""AI 数值计算工具 — 数据解析器。

NumericDataResolver：校验并解析 scalar、inline、fact_series、artifact_series。
- scalar/inline：纯 CPU 校验，不读数据库；
- fact_series：要求 fact:read，基于 NumericPrincipal 构建作用域 FactQueryService 实例，
  通过 RLS 加载 Fact + 权威 artifact，定位 series_index/column_name；
- artifact_series：第一版 stub（校验 artifact:read 后返回 numeric_invalid_source）。

设计文档 §8：数据解析与授权
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from packages.ai.numeric.contracts import (
    NumericError,
    NumericLimits,
    NumericPrincipal,
    NumericSource,
    NumericSourceProvenance,
    ResolvedNumericInput,
)

#: FactQueryService 工厂函数类型（基于 NumericPrincipal 构建实例）。
FactQueryFactory = Callable[[NumericPrincipal], Any]


class NumericDataResolver:
    """数值数据解析器。

    Attributes:
        _fact_query_factory: 基于 NumericPrincipal 构建 FactQueryService 的工厂函数。
        _limits: 资源限制配置。
    """

    def __init__(
        self,
        fact_query_factory: FactQueryFactory | None = None,
        limits: NumericLimits | None = None,
    ) -> None:
        self._fact_query_factory = fact_query_factory
        self._limits = limits or NumericLimits()

    async def resolve(
        self,
        source: NumericSource,
        principal: NumericPrincipal,
    ) -> ResolvedNumericInput:
        """解析数据来源为 ResolvedNumericInput。

        Args:
            source: 数据来源规格。
            principal: 调用主体（用于权限检查和 RLS 作用域）。

        Returns:
            ResolvedNumericInput: 解析后的数值输入。

        Raises:
            NumericError: 校验失败、权限不足或数据不可用时。
        """
        if source.source_type == "scalar":
            return self._resolve_scalar(source)
        if source.source_type == "inline":
            return self._resolve_inline(source)
        if source.source_type == "fact_series":
            return await self._resolve_fact_series(source, principal)
        if source.source_type == "artifact_series":
            return await self._resolve_artifact_series(source, principal)

        raise NumericError(
            code="numeric_invalid_source",
            message=f"unknown source_type: {source.source_type}",
            path=f"variables.{source.name}.source_type",
        )

    # ---- scalar ----

    def _resolve_scalar(self, source: NumericSource) -> ResolvedNumericInput:
        """解析标量来源。"""
        if source.value is None:
            raise NumericError(
                code="numeric_invalid_source",
                message="scalar source requires 'value' field",
                path=f"variables.{source.name}.value",
            )

        val = source.value

        # 拒绝 bool
        if isinstance(val, bool):
            raise NumericError(
                code="numeric_non_numeric",
                message="boolean is not a valid numeric value",
                path=f"variables.{source.name}.value",
            )

        # 拒绝字符串
        if isinstance(val, str):
            raise NumericError(
                code="numeric_non_numeric",
                message="string is not a valid numeric value",
                path=f"variables.{source.name}.value",
            )

        float_val = float(val)

        # 拒绝 NaN/Infinity
        if not math.isfinite(float_val):
            raise NumericError(
                code="numeric_non_finite_result",
                message="scalar value must be finite",
                path=f"variables.{source.name}.value",
            )

        values = np.float64(float_val)  # 0-d array
        null_mask = np.bool_(False)  # 0-d
        unit = self._validate_unit(source.unit, source.name)
        input_digest = self._compute_digest(values, null_mask)  # type: ignore[arg-type]

        return ResolvedNumericInput(
            name=source.name,
            values=values,  # type: ignore[arg-type]
            null_mask=null_mask,  # type: ignore[arg-type]
            unit=unit,
            source_provenance=NumericSourceProvenance(
                source_type="scalar",
                row_count=1,
            ),
            input_digest=input_digest,
        )

    # ---- inline ----

    def _resolve_inline(self, source: NumericSource) -> ResolvedNumericInput:
        """解析内联序列来源。"""
        if source.values is None:
            raise NumericError(
                code="numeric_invalid_source",
                message="inline source requires 'values' field",
                path=f"variables.{source.name}.values",
            )
        if not isinstance(source.values, list):
            raise NumericError(
                code="numeric_invalid_source",
                message="inline 'values' must be an array",
                path=f"variables.{source.name}.values",
            )

        raw_values = source.values

        # 检查长度
        if len(raw_values) > self._limits.max_inline_series_length:
            raise NumericError(
                code="numeric_size_limit",
                message=(
                    f"inline series length {len(raw_values)} exceeds limit"
                    f" ({self._limits.max_inline_series_length})"
                ),
                path=f"variables.{source.name}.values",
                details={"count": len(raw_values), "limit": self._limits.max_inline_series_length},
            )

        float_values: list[float] = []
        nulls: list[bool] = []

        for i, raw in enumerate(raw_values):
            if raw is None:
                float_values.append(0.0)  # placeholder
                nulls.append(True)
                continue

            # 拒绝 bool
            if isinstance(raw, bool):
                raise NumericError(
                    code="numeric_non_numeric",
                    message=f"element at index {i} is boolean, not numeric",
                    path=f"variables.{source.name}.values[{i}]",
                )

            # 拒绝字符串
            if isinstance(raw, str):
                raise NumericError(
                    code="numeric_non_numeric",
                    message=f"element at index {i} is string, not numeric",
                    path=f"variables.{source.name}.values[{i}]",
                )

            # 拒绝嵌套数组/对象
            if isinstance(raw, (list, dict)):
                raise NumericError(
                    code="numeric_non_numeric",
                    message=f"element at index {i} is nested structure, not numeric",
                    path=f"variables.{source.name}.values[{i}]",
                )

            float_val = float(raw)

            # 拒绝 NaN/Infinity
            if not math.isfinite(float_val):
                raise NumericError(
                    code="numeric_non_finite_result",
                    message=f"element at index {i} is not finite (NaN or Infinity)",
                    path=f"variables.{source.name}.values[{i}]",
                )

            float_values.append(float_val)
            nulls.append(False)

        values = np.array(float_values, dtype=np.float64)
        null_mask = np.array(nulls, dtype=np.bool_)
        unit = self._validate_unit(source.unit, source.name)
        input_digest = self._compute_digest(values, null_mask)

        return ResolvedNumericInput(
            name=source.name,
            values=values,
            null_mask=null_mask,
            unit=unit,
            source_provenance=NumericSourceProvenance(
                source_type="inline",
                row_count=len(raw_values),
            ),
            input_digest=input_digest,
        )

    # ---- fact_series ----

    async def _resolve_fact_series(
        self,
        source: NumericSource,
        principal: NumericPrincipal,
    ) -> ResolvedNumericInput:
        """解析 Fact 序列来源。"""
        # 权限检查
        self._check_permission(principal, "fact:read", source.name)

        # 校验必填字段
        if not source.fact_id:
            raise NumericError(
                code="numeric_invalid_source",
                message="fact_series requires 'fact_id' field",
                path=f"variables.{source.name}.fact_id",
            )
        if source.series_index is None:
            raise NumericError(
                code="numeric_invalid_source",
                message="fact_series requires 'series_index' field",
                path=f"variables.{source.name}.series_index",
            )
        if not source.column_name:
            raise NumericError(
                code="numeric_invalid_source",
                message="fact_series requires 'column_name' field",
                path=f"variables.{source.name}.column_name",
            )

        # 解析 fact_id UUID
        try:
            fact_id = UUID(source.fact_id)
        except (ValueError, TypeError):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"invalid fact_id: {source.fact_id}",
                path=f"variables.{source.name}.fact_id",
            ) from None

        # 构建 FactQueryService
        if self._fact_query_factory is None:
            raise NumericError(
                code="numeric_internal_error",
                message="fact query factory not configured",
            )

        fact_service = self._fact_query_factory(principal)

        # 加载 Fact 数据
        try:
            fact_data = await fact_service.get_fact_data(fact_id)
        except Exception:
            # 跨租户/不存在统一返回 not found
            raise NumericError(
                code="numeric_field_not_found",
                message=f"fact not found: {source.fact_id}",
                path=f"variables.{source.name}.fact_id",
            ) from None

        # 定位 series
        series_list = fact_data.get("series", [])
        if not isinstance(series_list, list):
            raise NumericError(
                code="numeric_invalid_source",
                message="fact data has no valid series array",
                path=f"variables.{source.name}",
            )

        if source.series_index < 0 or source.series_index >= len(series_list):
            raise NumericError(
                code="numeric_field_not_found",
                message=(
                    f"series_index {source.series_index} not found (have {len(series_list)} series)"
                ),
                path=f"variables.{source.name}.series_index",
                details={"series_index": source.series_index, "series_count": len(series_list)},
            )

        series = series_list[source.series_index]
        if not isinstance(series, dict):
            raise NumericError(
                code="numeric_invalid_source",
                message=f"series at index {source.series_index} is not an object",
                path=f"variables.{source.name}.series_index",
            )

        # 定位 column
        columns = series.get("columns", [])
        if not isinstance(columns, list) or source.column_name not in columns:
            raise NumericError(
                code="numeric_field_not_found",
                message=f"column '{source.column_name}' not found in series",
                path=f"variables.{source.name}.column_name",
                details={"requested_column": source.column_name, "available_columns": columns},
            )

        col_idx = columns.index(source.column_name)

        # 提取数值列 — 从 series["rows"] 读取（IRIP 标准结构）
        rows = series.get("rows", [])
        if not isinstance(rows, list):
            raise NumericError(
                code="numeric_invalid_source",
                message="series has no valid rows array",
                path=f"variables.{source.name}",
            )

        float_values: list[float] = []
        nulls: list[bool] = []

        for i, row in enumerate(rows):
            val: Any = None
            if isinstance(row, dict):
                val = row.get(source.column_name)
            elif isinstance(row, (list, tuple)):
                val = row[col_idx] if col_idx < len(row) else None
            else:
                val = None

            if val is None:
                float_values.append(0.0)
                nulls.append(True)
                continue

            # 拒绝 bool/字符串/嵌套
            if isinstance(val, bool) or isinstance(val, str) or isinstance(val, (list, dict)):
                raise NumericError(
                    code="numeric_non_numeric",
                    message=f"point at index {i} column '{source.column_name}' is not numeric",
                    path=f"variables.{source.name}.points[{i}]",
                )

            float_val = float(val)
            if not math.isfinite(float_val):
                raise NumericError(
                    code="numeric_non_finite_result",
                    message=f"point at index {i} is not finite",
                    path=f"variables.{source.name}.points[{i}]",
                )

            float_values.append(float_val)
            nulls.append(False)

        # 检查长度限制
        if len(float_values) > self._limits.max_platform_series_length:
            raise NumericError(
                code="numeric_size_limit",
                message=(
                    f"platform series length {len(float_values)} exceeds limit"
                    f" ({self._limits.max_platform_series_length})"
                ),
                path=f"variables.{source.name}",
                details={
                    "count": len(float_values),
                    "limit": self._limits.max_platform_series_length,
                },
            )

        values = np.array(float_values, dtype=np.float64)
        null_mask = np.array(nulls, dtype=np.bool_)

        # 从 artifact 元数据提取单位（平台来源单位不能由工具参数覆盖）
        units_map = series.get("units", {})
        if isinstance(units_map, dict):
            unit = units_map.get(source.column_name)
            if unit is not None and not isinstance(unit, str):
                unit = str(unit)
        else:
            unit = None

        # 提取 artifact 信息
        artifact_id, artifact_sha256 = await self._get_artifact_info(fact_service, fact_id)

        input_digest = self._compute_digest(values, null_mask)

        return ResolvedNumericInput(
            name=source.name,
            values=values,
            null_mask=null_mask,
            unit=unit,
            source_provenance=NumericSourceProvenance(
                source_type="fact_series",
                fact_id=fact_id,
                artifact_id=artifact_id,
                artifact_sha256=artifact_sha256,
                series_index=source.series_index,
                column_name=source.column_name,
                row_count=len(float_values),
            ),
            input_digest=input_digest,
        )

    # ---- artifact_series (stub) ----

    async def _resolve_artifact_series(
        self,
        source: NumericSource,
        principal: NumericPrincipal,
    ) -> ResolvedNumericInput:
        """解析 Artifact 序列来源（第一版 stub）。

        校验 artifact:read 权限后返回 numeric_invalid_source。
        """
        # 权限检查
        self._check_permission(principal, "artifact:read", source.name)

        raise NumericError(
            code="numeric_invalid_source",
            message="artifact_series not yet supported",
            path=f"variables.{source.name}.source_type",
        )

    # ---- 辅助方法 ----

    def _check_permission(
        self,
        principal: NumericPrincipal,
        permission: str,
        var_name: str,
    ) -> None:
        """检查主体是否拥有指定权限。"""
        from packages.auth.permissions import has_role_permission

        has_perm = any(has_role_permission(role, permission) for role in principal.roles)
        if not has_perm:
            raise NumericError(
                code="numeric_field_not_found",
                message=f"permission '{permission}' required for variable '{var_name}'",
                path=f"variables.{var_name}",
            )

    def _validate_unit(self, unit: str | None, var_name: str) -> str | None:
        """校验单位标签。"""
        if unit is None:
            return None
        if not isinstance(unit, str):
            raise NumericError(
                code="numeric_invalid_source",
                message="unit must be a string",
                path=f"variables.{var_name}.unit",
            )
        if len(unit) > 64:
            raise NumericError(
                code="numeric_invalid_source",
                message="unit exceeds 64 characters",
                path=f"variables.{var_name}.unit",
            )
        return unit

    def _compute_digest(
        self,
        values: NDArray[np.float64],
        null_mask: NDArray[np.bool_],
    ) -> str:
        """计算输入规范二进制表示的 SHA-256 digest。"""
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(values.astype(np.float64)).tobytes())
        h.update(np.ascontiguousarray(null_mask.astype(np.bool_)).tobytes())
        return h.hexdigest()

    async def _get_artifact_info(
        self,
        fact_service: Any,
        fact_id: UUID,
    ) -> tuple[UUID | None, str | None]:
        """从 FactQueryService 查询 artifact id 和 sha256。"""
        try:
            from packages.facts.repository import FactRepository

            # 使用 FactQueryService 的 scoped session 查询 artifact 记录
            async with fact_service._scoped_session() as session:
                art_record = await FactRepository.find_json_artifact(session, fact_id)
                if art_record is not None:
                    return art_record.id, art_record.sha256  # type: ignore[attr-defined]
        except Exception:
            pass
        return None, None
