"""AI 数值计算工具 — 描述统计服务。

实现 SeriesStatisticsService：口径明确的序列描述统计。

统计定义（设计文档 §10.4）：
- count/valid_count/missing_count
- sum/mean/min/max/median/quantile
- population/sample variance/std
- bias-corrected Fisher-Pearson skewness (n >= 3)
- unbiased Fisher excess kurtosis (n >= 4)

空值策略：fail / omit / propagate
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from packages.ai.numeric.contracts import (
    DescribeSeriesRequest,
    NumericError,
    NumericLimits,
    ResolvedNumericInput,
    StatisticsResult,
)


class SeriesStatisticsService:
    """描述统计服务。

    Attributes:
        _limits: 资源限制配置。
    """

    def __init__(self, limits: NumericLimits | None = None) -> None:
        self._limits = limits or NumericLimits()

    def describe(
        self,
        source: ResolvedNumericInput,
        request: DescribeSeriesRequest,
    ) -> StatisticsResult:
        """计算序列描述统计。

        Args:
            source: 已解析的数值输入。
            request: describe_series 请求参数。

        Returns:
            StatisticsResult: 统计结果 + 警告。

        Raises:
            NumericError: 空值策略为 fail 且含 null 时。
        """
        values = source.values.astype(np.float64)
        null_mask = source.null_mask.astype(np.bool_)
        total_count = source.length
        missing_count = int(np.sum(null_mask))
        valid_count = total_count - missing_count

        warnings: list[str] = []
        result: dict[str, Any] = {}

        # 始终返回 count / valid_count / missing_count
        result["count"] = total_count
        result["valid_count"] = valid_count
        result["missing_count"] = missing_count

        # 提取有效值
        if source.is_scalar:
            valid_vals = (
                np.array([], dtype=np.float64)
                if null_mask
                else np.array([float(values)], dtype=np.float64)
            )
        else:
            if null_mask.size > 0:
                valid_vals = values[~null_mask]
            else:
                valid_vals = values

        # 空值策略处理
        if missing_count > 0:
            if request.null_policy == "fail":
                raise NumericError(
                    code="numeric_invalid_source",
                    message=(
                        f"series contains {missing_count} null values and null_policy is 'fail'"
                    ),
                    path="series",
                    details={"missing_count": missing_count},
                )
            elif request.null_policy == "omit":
                pass  # valid_vals already excludes nulls
            elif request.null_policy == "propagate":
                # count/valid_count/missing_count 正常返回
                # 其他统计返回 null + warning
                pass

        # 确定要计算的统计项
        stats = request.effective_statistics

        # 对每个统计项计算
        for stat in stats:
            if stat in ("count", "missing_count"):
                continue  # 已处理

            value = self._compute_stat(
                stat, valid_vals, valid_count, request, warnings, missing_count
            )
            if stat == "variance":
                result["variance"] = value
            elif stat == "std":
                result["std"] = value
            elif stat == "quantile":
                result["quantile"] = value
            else:
                result[stat] = value

        # 计算结果 digest
        digest = self._compute_result_digest(result)

        return StatisticsResult(
            values=result,
            warnings=warnings,
            result_digest=digest,
        )

    def _compute_stat(
        self,
        stat: str,
        valid_vals: np.ndarray,
        valid_count: int,
        request: DescribeSeriesRequest,
        warnings: list[str],
        missing_count: int,
    ) -> Any:
        """计算单个统计项。"""
        # propagate 策略下，除 count/missing_count 外，有 null 时返回 null
        if request.null_policy == "propagate" and missing_count > 0:
            warnings.append(f"null_propagated:{stat}")
            if stat == "variance":
                return self._null_variance(request.variance_mode)
            if stat == "std":
                return self._null_std(request.variance_mode)
            if stat == "quantile":
                return {str(q): None for q in request.quantiles}
            return None

        if valid_count == 0:
            # 空序列：只有 count/missing_count 有效
            warnings.append(f"empty_series:{stat}")
            if stat == "sum":
                return 0.0
            if stat == "variance":
                return self._null_variance(request.variance_mode)
            if stat == "std":
                return self._null_std(request.variance_mode)
            if stat == "quantile":
                return {str(q): None for q in request.quantiles}
            return None

        if stat == "sum":
            return self._safe_sum(valid_vals)
        if stat == "mean":
            return self._safe_mean(valid_vals, warnings, valid_count)
        if stat == "min":
            return float(np.min(valid_vals))
        if stat == "max":
            return float(np.max(valid_vals))
        if stat == "median":
            return self._quantile(valid_vals, 0.5)
        if stat == "variance":
            return self._variance(valid_vals, request.variance_mode, warnings, valid_count)
        if stat == "std":
            return self._std(valid_vals, request.variance_mode, warnings, valid_count)
        if stat == "quantile":
            return {str(q): self._quantile(valid_vals, q) for q in request.quantiles}
        if stat == "skewness":
            return self._skewness(valid_vals, warnings, valid_count)
        if stat == "kurtosis":
            return self._kurtosis(valid_vals, warnings, valid_count)

        warnings.append(f"unknown_statistic:{stat}")
        return None

    # ---- 统计计算 ----

    def _safe_sum(self, vals: np.ndarray) -> float:
        """稳定求和。"""
        result = float(np.sum(vals))
        if result == 0.0:
            result = 0.0  # normalize -0.0
        return result

    def _safe_mean(self, vals: np.ndarray, warnings: list[str], n: int) -> float:
        result = float(np.mean(vals))
        if result == 0.0:
            result = 0.0
        return result

    def _variance(
        self,
        vals: np.ndarray,
        mode: str,
        warnings: list[str],
        n: int,
    ) -> dict[str, float | None] | float | None:
        """计算方差。"""
        if mode == "both":
            return {
                "population": self._var_ddof(vals, 0, warnings, n),
                "sample": self._var_ddof(vals, 1, warnings, n),
            }
        if mode == "population":
            return self._var_ddof(vals, 0, warnings, n)
        return self._var_ddof(vals, 1, warnings, n)

    def _var_ddof(
        self,
        vals: np.ndarray,
        ddof: int,
        warnings: list[str],
        n: int,
    ) -> float | None:
        """计算指定 ddof 的方差。"""
        min_n = 1 if ddof == 0 else 2
        if n < min_n:
            warnings.append(
                f"insufficient_samples:variance_{('population' if ddof == 0 else 'sample')}"
            )
            return None
        result = float(np.var(vals, ddof=ddof))
        if result == 0.0:
            result = 0.0
        return result

    def _std(
        self,
        vals: np.ndarray,
        mode: str,
        warnings: list[str],
        n: int,
    ) -> dict[str, float | None] | float | None:
        """计算标准差。"""
        if mode == "both":
            return {
                "population": self._std_ddof(vals, 0, warnings, n),
                "sample": self._std_ddof(vals, 1, warnings, n),
            }
        if mode == "population":
            return self._std_ddof(vals, 0, warnings, n)
        return self._std_ddof(vals, 1, warnings, n)

    def _std_ddof(
        self,
        vals: np.ndarray,
        ddof: int,
        warnings: list[str],
        n: int,
    ) -> float | None:
        """计算指定 ddof 的标准差。"""
        min_n = 1 if ddof == 0 else 2
        if n < min_n:
            warnings.append(f"insufficient_samples:std_{('population' if ddof == 0 else 'sample')}")
            return None
        result = float(np.std(vals, ddof=ddof))
        if result == 0.0:
            result = 0.0
        return result

    def _quantile(self, vals: np.ndarray, q: float) -> float:
        """线性插值分位数（与 NumPy method="linear" 对齐）。"""
        result = float(np.quantile(vals, q))
        if result == 0.0:
            result = 0.0
        return result

    def _skewness(self, vals: np.ndarray, warnings: list[str], n: int) -> float | None:
        """bias-corrected Fisher-Pearson 样本偏度（n >= 3）。"""
        if n < 3:
            warnings.append("insufficient_samples:skewness")
            return None
        mean = float(np.mean(vals))
        s = float(np.std(vals, ddof=1))
        if s == 0.0:
            warnings.append("undefined_for_constant_series:skewness")
            return None
        # g1 = (n / ((n-1)*(n-2))) * sum(((x_i - mean)/s)^3)
        centered = vals - mean
        m3 = float(np.mean(centered**3))
        g1 = (m3 / (s**3)) * (n**2 / ((n - 1) * (n - 2)))
        if g1 == 0.0:
            g1 = 0.0
        return g1

    def _kurtosis(self, vals: np.ndarray, warnings: list[str], n: int) -> float | None:
        """unbiased Fisher excess kurtosis（n >= 4）。"""
        if n < 4:
            warnings.append("insufficient_samples:kurtosis")
            return None
        mean = float(np.mean(vals))
        m2 = float(np.var(vals, ddof=0))  # 有偏方差（除以 n）
        if m2 == 0.0:
            warnings.append("undefined_for_constant_series:kurtosis")
            return None
        # G2 = (n-1)/((n-2)(n-3)) * ((n+1)*g2 + 6)
        # where g2 = m4/m2^2 - 3 is the biased sample excess kurtosis
        centered = vals - mean
        m4 = float(np.mean(centered**4))
        g2_biased = m4 / (m2**2) - 3.0
        g2 = (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * g2_biased + 6.0)
        if g2 == 0.0:
            g2 = 0.0
        return g2

    # ---- 辅助 ----

    def _null_variance(self, mode: str) -> dict[str, None] | None:
        """propagate/空序列下的 null 方差。"""
        if mode == "both":
            return {"population": None, "sample": None}
        return None

    def _null_std(self, mode: str) -> dict[str, None] | None:
        """propagate/空序列下的 null 标准差。"""
        if mode == "both":
            return {"population": None, "sample": None}
        return None

    def _compute_result_digest(self, result: dict[str, Any]) -> str:
        """计算结果字典的 SHA-256 digest。"""
        import json

        canonical = json.dumps(result, sort_keys=True, default=str, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
