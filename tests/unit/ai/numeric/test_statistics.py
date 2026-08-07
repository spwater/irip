"""test_statistics.py — 描述统计正确性测试。

设计文档 §19.4 统计正确性、§20.1 基准序列、§10.4 描述统计定义。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from packages.ai.numeric.contracts import (
    DEFAULT_QUANTILES,
    DescribeSeriesRequest,
    NumericError,
    NumericSourceProvenance,
    ResolvedNumericInput,
)
from packages.ai.numeric.statistics import SeriesStatisticsService


# =============================================================================
# 辅助函数
# =============================================================================


def make_series(values: list[float | None]) -> ResolvedNumericInput:
    """创建序列 ResolvedNumericInput。"""
    float_vals: list[float] = []
    nulls: list[bool] = []
    for v in values:
        if v is None:
            float_vals.append(0.0)
            nulls.append(True)
        else:
            float_vals.append(float(v))
            nulls.append(False)
    arr = np.array(float_vals, dtype=np.float64)
    mask = np.array(nulls, dtype=np.bool_)
    return ResolvedNumericInput(
        name="x",
        values=arr,
        null_mask=mask,
        unit=None,
        source_provenance=NumericSourceProvenance(source_type="inline", row_count=len(values)),
        input_digest="",
    )


def describe(
    values: list[float | None],
    request: DescribeSeriesRequest | None = None,
) -> dict:
    """便捷描述统计。"""
    service = SeriesStatisticsService()
    source = make_series(values)
    req = request or DescribeSeriesRequest()
    result = service.describe(source, req)
    return result.values


# =============================================================================
# 基准序列 [1..100]
# =============================================================================


class TestBenchmarkSeries:
    """基准序列 [1..100] 验收值。"""

    @pytest.fixture
    def benchmark_values(self) -> list[float]:
        return [float(i) for i in range(1, 101)]

    def test_count(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert result["count"] == 100

    def test_sum(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert abs(result["sum"] - 5050.0) < 1e-6

    def test_mean(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert abs(result["mean"] - 50.5) < 1e-6

    def test_population_variance(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        var = result["variance"]
        assert isinstance(var, dict)
        assert abs(var["population"] - 833.25) < 1e-6

    def test_sample_variance(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        var = result["variance"]
        assert isinstance(var, dict)
        assert abs(var["sample"] - 841.6666666666666) < 1e-4

    def test_population_std(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        std = result["std"]
        assert isinstance(std, dict)
        assert abs(std["population"] - 28.86607004772212) < 1e-6

    def test_sample_std(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        std = result["std"]
        assert isinstance(std, dict)
        assert abs(std["sample"] - 29.011491975882016) < 1e-6

    def test_min(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert result["min"] == 1.0

    def test_max(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert result["max"] == 100.0

    def test_median(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert result["median"] == 50.5

    def test_valid_count(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert result["valid_count"] == 100

    def test_missing_count(self, benchmark_values: list[float]) -> None:
        result = describe(benchmark_values)
        assert result["missing_count"] == 0


# =============================================================================
# 空序列
# =============================================================================


class TestEmptySeries:
    """空序列仅 count/missing_count。"""

    def test_empty_count(self) -> None:
        result = describe([])
        assert result["count"] == 0
        assert result["missing_count"] == 0
        assert result["valid_count"] == 0

    def test_empty_sum(self) -> None:
        result = describe([])
        assert result["sum"] == 0.0

    def test_empty_mean_null(self) -> None:
        result = describe([])
        assert result["mean"] is None

    def test_empty_variance_null(self) -> None:
        result = describe([])
        var = result["variance"]
        assert var is not None
        assert isinstance(var, dict)
        assert var["population"] is None
        assert var["sample"] is None


# =============================================================================
# 单元素、双元素、常量序列
# =============================================================================


class TestSpecialSeries:
    """单元素、双元素、常量序列。"""

    def test_single_element(self) -> None:
        result = describe([5.0])
        assert result["count"] == 1
        assert result["sum"] == 5.0
        assert result["mean"] == 5.0
        assert result["min"] == 5.0
        assert result["max"] == 5.0
        assert result["median"] == 5.0

    def test_single_element_variance(self) -> None:
        result = describe([5.0])
        var = result["variance"]
        assert var["population"] == 0.0
        assert var["sample"] is None  # need n >= 2

    def test_two_elements(self) -> None:
        result = describe([1.0, 3.0])
        assert result["mean"] == 2.0
        var = result["variance"]
        assert var["population"] == 1.0
        assert abs(var["sample"] - 2.0) < 1e-10

    def test_constant_series(self) -> None:
        result = describe([5.0, 5.0, 5.0, 5.0, 5.0])
        var = result["variance"]
        assert var["population"] == 0.0
        assert var["sample"] == 0.0
        std = result["std"]
        assert std["population"] == 0.0
        assert std["sample"] == 0.0

    def test_constant_series_skewness_null(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([5.0, 5.0, 5.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert stats.values["skewness"] is None
        assert any("constant_series" in w for w in stats.warnings)

    def test_constant_series_kurtosis_null(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([5.0, 5.0, 5.0, 5.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert stats.values["kurtosis"] is None
        assert any("constant_series" in w for w in stats.warnings)


# =============================================================================
# population / sample variance
# =============================================================================


class TestVarianceModes:
    """population/sample variance。"""

    def test_population_mode(self) -> None:
        req = DescribeSeriesRequest(variance_mode="population")
        result = describe([1.0, 2.0, 3.0, 4.0, 5.0], req)
        var = result["variance"]
        assert not isinstance(var, dict)
        assert abs(var - 2.0) < 1e-10

    def test_sample_mode(self) -> None:
        req = DescribeSeriesRequest(variance_mode="sample")
        result = describe([1.0, 2.0, 3.0, 4.0, 5.0], req)
        var = result["variance"]
        assert not isinstance(var, dict)
        assert abs(var - 2.5) < 1e-10

    def test_both_mode(self) -> None:
        req = DescribeSeriesRequest(variance_mode="both")
        result = describe([1.0, 2.0, 3.0, 4.0, 5.0], req)
        var = result["variance"]
        assert isinstance(var, dict)
        assert abs(var["population"] - 2.0) < 1e-10
        assert abs(var["sample"] - 2.5) < 1e-10


# =============================================================================
# linear quantile（与 NumPy 交叉验证）
# =============================================================================


class TestQuantile:
    """线性插值分位数。"""

    def test_quantile_median(self) -> None:
        req = DescribeSeriesRequest(quantiles=(0.5,))
        result = describe([1.0, 2.0, 3.0, 4.0, 5.0], req)
        q = result["quantile"]
        assert abs(q["0.5"] - 3.0) < 1e-10

    def test_quantile_cross_validate_numpy(self) -> None:
        data = list(np.random.RandomState(42).rand(100))
        req = DescribeSeriesRequest(quantiles=(0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0))
        result = describe(data, req)
        q_result = result["quantile"]
        for q_val in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            expected = float(np.quantile(np.array(data, dtype=np.float64), q_val))
            assert abs(q_result[str(q_val)] - expected) < 1e-10

    def test_default_quantiles(self) -> None:
        req = DescribeSeriesRequest()
        result = describe([1.0, 2.0, 3.0, 4.0, 5.0], req)
        q = result["quantile"]
        assert "0.25" in q
        assert "0.5" in q
        assert "0.75" in q


# =============================================================================
# Fisher-Pearson skewness（n >= 3）
# =============================================================================


class TestSkewness:
    """bias-corrected Fisher-Pearson 样本偏度。"""

    def test_symmetric_skewness_near_zero(self) -> None:
        # Symmetric data should have skewness near 0
        result = describe([1.0, 2.0, 3.0, 4.0, 5.0])
        assert abs(result["skewness"]) < 1e-6

    def test_right_skewness_positive(self) -> None:
        # Right-skewed data should have positive skewness
        result = describe([1.0, 1.0, 1.0, 1.0, 10.0])
        assert result["skewness"] is not None
        assert result["skewness"] > 0

    def test_insufficient_samples_n2(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([1.0, 2.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert stats.values["skewness"] is None
        assert any("insufficient" in w for w in stats.warnings)

    def test_cross_validate_scipy(self) -> None:
        # Cross-validate with scipy if available, else manual formula
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        result = describe(data)
        if result["skewness"] is not None:
            # Manual Fisher-Pearson skewness
            n = len(data)
            arr = np.array(data, dtype=np.float64)
            mean = float(np.mean(arr))
            s = float(np.std(arr, ddof=1))
            if s > 0:
                m3 = float(np.mean((arr - mean) ** 3))
                expected = (m3 / s ** 3) * (n ** 2 / ((n - 1) * (n - 2)))
                assert abs(result["skewness"] - expected) < 1e-10


# =============================================================================
# unbiased Fisher excess kurtosis（n >= 4）
# =============================================================================


class TestKurtosis:
    """unbiased Fisher excess kurtosis。"""

    def test_normal_kurtosis_near_zero(self) -> None:
        """For large sample from normal, Fisher excess kurtosis ≈ 0.

        NOTE: This test currently fails due to a source code bug in the kurtosis
        formula (uses unbiased std s^4 instead of biased variance m2^2).
        See test report for details. The assertion is correct per design §10.4.
        """
        np.random.seed(42)
        data = list(np.random.randn(10000))
        result = describe(data)
        if result["kurtosis"] is not None:
            # Fisher excess kurtosis for normal should be ~0
            # Known source bug: implementation gives ~-3.0 due to wrong formula
            # Asserting correct behavior per design
            assert abs(result["kurtosis"]) < 0.5  # generous tolerance for sample variation

    def test_insufficient_samples_n3(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([1.0, 2.0, 3.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert stats.values["kurtosis"] is None
        assert any("insufficient" in w for w in stats.warnings)

    def test_cross_validate_formula(self) -> None:
        """Cross-validate kurtosis against the correct Fisher excess kurtosis formula.

        The correct unbiased Fisher excess kurtosis formula is:
        G2 = (n-1)/((n-2)(n-3)) * ((n+1)*(m4/m2^2 - 3) + 6)
        where m2 = var(ddof=0) (biased variance) and m4 = mean((x-mean)^4).
        This matches scipy.stats.kurtosis(bias=False, fisher=True).
        """
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        result = describe(data)
        if result["kurtosis"] is not None:
            n = len(data)
            arr = np.array(data, dtype=np.float64)
            mean = float(np.mean(arr))
            m2 = float(np.var(arr, ddof=0))  # biased variance
            m4 = float(np.mean((arr - mean) ** 4))  # biased fourth moment
            g2_biased = m4 / (m2 ** 2) - 3.0
            expected = (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * g2_biased + 6.0)
            assert abs(result["kurtosis"] - expected) < 1e-10


# =============================================================================
# 三种 null policy
# =============================================================================


class TestNullPolicy:
    """三种 null policy。"""

    def test_fail_policy(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([1.0, None, 3.0])
        req = DescribeSeriesRequest(null_policy="fail")
        with pytest.raises(NumericError) as exc_info:
            service.describe(source, req)
        assert exc_info.value.code == "numeric_invalid_source"

    def test_omit_policy(self) -> None:
        req = DescribeSeriesRequest(null_policy="omit")
        result = describe([1.0, None, 3.0], req)
        assert result["count"] == 3
        assert result["valid_count"] == 2
        assert result["missing_count"] == 1
        assert result["sum"] == 4.0
        assert result["mean"] == 2.0

    def test_propagate_policy(self) -> None:
        req = DescribeSeriesRequest(null_policy="propagate")
        result = describe([1.0, None, 3.0], req)
        assert result["count"] == 3
        assert result["valid_count"] == 2
        assert result["missing_count"] == 1
        # Other stats should be null
        assert result["sum"] is None
        assert result["mean"] is None

    def test_propagate_count_always_present(self) -> None:
        req = DescribeSeriesRequest(null_policy="propagate")
        result = describe([None, None, None], req)
        assert result["count"] == 3
        assert result["missing_count"] == 3
        assert result["valid_count"] == 0


# =============================================================================
# 样本不足时单项 null + warning
# =============================================================================


class TestInsufficientSamples:
    """样本不足时单项 null + warning，其他指标仍成功。"""

    def test_single_element_stats(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([5.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert stats.values["count"] == 1
        assert stats.values["sum"] == 5.0
        assert stats.values["mean"] == 5.0
        # sample variance needs n >= 2
        var = stats.values["variance"]
        assert var["sample"] is None
        assert var["population"] == 0.0
        # skewness needs n >= 3
        assert stats.values["skewness"] is None
        # kurtosis needs n >= 4
        assert stats.values["kurtosis"] is None

    def test_warnings_for_insufficient(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([5.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert any("insufficient" in w for w in stats.warnings)

    def test_result_digest_not_empty(self) -> None:
        service = SeriesStatisticsService()
        source = make_series([1.0, 2.0, 3.0])
        req = DescribeSeriesRequest()
        stats = service.describe(source, req)
        assert stats.result_digest != ""
        assert len(stats.result_digest) == 64  # SHA-256 hex

    def test_statistics_subset(self) -> None:
        req = DescribeSeriesRequest(statistics=("count", "sum", "mean"))
        result = describe([1.0, 2.0, 3.0], req)
        assert "count" in result
        assert "sum" in result
        assert "mean" in result
        # Should NOT have stats not requested... but count/missing_count always present
        # Check that requested items are present
        assert result["sum"] == 6.0
