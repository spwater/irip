"""映射转换组件单元测试（7 个）。

测试覆盖：
- FieldMapper: 字段映射
- UnitConverter: 单位转换
- MissingValues: 缺失值处理（4 种策略）
- TimeAlignment: 时间对齐
- Resampler: 时间序列重采样
- MADOutliers: MAD 异常值检测
- SteadyWindow: 稳态窗口识别
"""

import pytest

from packages.components.builtin.transform.field_mapper import FieldMapper
from packages.components.builtin.transform.mad_outliers import MADOutliers
from packages.components.builtin.transform.missing_values import (
    MissingValues,
)
from packages.components.builtin.transform.resampler import Resampler
from packages.components.builtin.transform.steady_window import SteadyWindow
from packages.components.builtin.transform.time_alignment import TimeAlignment
from packages.components.builtin.transform.unit_converter import UnitConverter
from packages.components.builtin.types import ObservationTable

from tests.unit.components.conftest import make_test_context


def _make_table(
    columns: tuple[str, ...],
    rows: list[dict],
) -> ObservationTable:
    """构建测试用 ObservationTable。"""
    return ObservationTable(
        columns=columns,
        rows=tuple(rows),
        source_locations=(),
    )


# ---- FieldMapper ----


class TestFieldMapper:
    """字段映射组件测试。"""

    async def test_basic_mapping(self):
        """基本字段映射。"""
        table = _make_table(
            ("a", "b"), [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        )
        mapper = FieldMapper()
        ctx = make_test_context()
        result = await mapper.execute(
            ctx,
            {
                "observations": table,
                "mapping": {"a": "alpha", "b": "beta"},
            },
        )

        out = result.outputs["observations"]
        assert out.columns == ("alpha", "beta")
        assert out.rows[0]["alpha"] == 1
        assert out.rows[1]["beta"] == "y"

    async def test_include_unmapped(self):
        """保留未映射字段。"""
        table = _make_table(("a", "b", "c"), [{"a": 1, "b": 2, "c": 3}])
        mapper = FieldMapper()
        ctx = make_test_context()
        result = await mapper.execute(
            ctx,
            {
                "observations": table,
                "mapping": {"a": "alpha"},
                "include_unmapped": True,
            },
        )

        out = result.outputs["observations"]
        assert out.columns == ("alpha", "b", "c")
        assert out.rows[0]["c"] == 3

    async def test_exclude_unmapped(self):
        """未映射字段被丢弃。"""
        table = _make_table(("a", "b"), [{"a": 1, "b": 2}])
        mapper = FieldMapper()
        ctx = make_test_context()
        result = await mapper.execute(
            ctx,
            {"observations": table, "mapping": {"a": "alpha"}},
        )

        out = result.outputs["observations"]
        assert out.columns == ("alpha",)
        assert "b" not in out.columns


# ---- UnitConverter ----


class TestUnitConverter:
    """单位转换组件测试。"""

    async def test_predefined_mm_to_um(self):
        """使用预定义因子 mm→um。"""
        table = _make_table(
            ("diameter",), [{"diameter": 1.5}, {"diameter": 2.0}]
        )
        converter = UnitConverter()
        ctx = make_test_context()
        result = await converter.execute(
            ctx,
            {
                "observations": table,
                "conversions": [
                    {
                        "column": "diameter",
                        "from_unit": "mm",
                        "to_unit": "um",
                    }
                ],
            },
        )

        out = result.outputs["observations"]
        assert out.rows[0]["diameter"] == 1500.0
        assert out.rows[1]["diameter"] == 2000.0

    async def test_explicit_factor(self):
        """使用显式 factor。"""
        table = _make_table(("val",), [{"val": 10.0}])
        converter = UnitConverter()
        ctx = make_test_context()
        result = await converter.execute(
            ctx,
            {
                "observations": table,
                "conversions": [
                    {"column": "val", "factor": 0.001}
                ],
            },
        )

        out = result.outputs["observations"]
        assert out.rows[0]["val"] == 0.01

    async def test_unknown_conversion_warning(self):
        """未知单位转换产生警告。"""
        table = _make_table(("val",), [{"val": 10.0}])
        converter = UnitConverter()
        ctx = make_test_context()
        result = await converter.execute(
            ctx,
            {
                "observations": table,
                "conversions": [
                    {
                        "column": "val",
                        "from_unit": "foo",
                        "to_unit": "bar",
                    }
                ],
            },
        )

        assert result.diagnostics is not None
        assert len(result.diagnostics["warnings"]) > 0


# ---- MissingValues ----


class TestMissingValues:
    """缺失值处理组件测试。"""

    async def test_reject_strategy(self):
        """reject 策略删除含缺失的行。"""
        table = _make_table(
            ("a", "b"),
            [{"a": 1, "b": 2}, {"a": None, "b": 3}, {"a": 4, "b": 5}],
        )
        mv = MissingValues()
        ctx = make_test_context()
        result = await mv.execute(
            ctx, {"observations": table, "strategy": "reject"}
        )

        out = result.outputs["observations"]
        assert out.row_count() == 2

    async def test_constant_strategy(self):
        """constant 策略填充常量。"""
        table = _make_table(
            ("a",), [{"a": 1}, {"a": None}, {"a": 3}]
        )
        mv = MissingValues()
        ctx = make_test_context()
        result = await mv.execute(
            ctx,
            {
                "observations": table,
                "strategy": "constant",
                "fill_value": 0,
            },
        )

        out = result.outputs["observations"]
        assert out.rows[1]["a"] == 0

    async def test_forward_fill_strategy(self):
        """forward_fill 策略前向填充。"""
        table = _make_table(
            ("a",), [{"a": 10}, {"a": None}, {"a": None}, {"a": 40}]
        )
        mv = MissingValues()
        ctx = make_test_context()
        result = await mv.execute(
            ctx, {"observations": table, "strategy": "forward_fill"}
        )

        out = result.outputs["observations"]
        assert out.rows[1]["a"] == 10
        assert out.rows[2]["a"] == 10
        assert out.rows[3]["a"] == 40

    async def test_null_strategy(self):
        """null 策略统一为 None。"""
        table = _make_table(
            ("a",), [{"a": 1}, {"a": ""}, {"a": 3}]
        )
        mv = MissingValues()
        ctx = make_test_context()
        result = await mv.execute(
            ctx, {"observations": table, "strategy": "null"}
        )

        out = result.outputs["observations"]
        assert out.rows[1]["a"] is None


# ---- TimeAlignment ----


class TestTimeAlignment:
    """时间对齐组件测试。"""

    async def test_align_to_1_second(self):
        """对齐到 1 秒频率。"""
        table = _make_table(
            ("timestamp", "value"),
            [
                {"timestamp": "2026-01-01T00:00:00", "value": 10},
                {"timestamp": "2026-01-01T00:00:02", "value": 20},
                {"timestamp": "2026-01-01T00:00:04", "value": 30},
            ],
        )
        aligner = TimeAlignment()
        ctx = make_test_context()
        result = await aligner.execute(
            ctx,
            {
                "observations": table,
                "time_column": "timestamp",
                "frequency": "1s",
                "method": "ffill",
            },
        )

        out = result.outputs["observations"]
        assert out.row_count() >= 3

    async def test_missing_time_column(self):
        """时间列不存在时返回原表。"""
        table = _make_table(("a",), [{"a": 1}])
        aligner = TimeAlignment()
        ctx = make_test_context()
        result = await aligner.execute(
            ctx,
            {
                "observations": table,
                "time_column": "ts",
                "frequency": "1s",
            },
        )

        assert result.diagnostics is not None


# ---- Resampler ----


class TestResampler:
    """时间序列重采样组件测试。"""

    async def test_resample_mean(self):
        """按均值重采样。"""
        table = _make_table(
            ("timestamp", "value"),
            [
                {"timestamp": "2026-01-01T00:00:00", "value": 10},
                {"timestamp": "2026-01-01T00:00:30", "value": 20},
                {"timestamp": "2026-01-01T00:01:00", "value": 30},
                {"timestamp": "2026-01-01T00:01:30", "value": 40},
            ],
        )
        resampler = Resampler()
        ctx = make_test_context()
        result = await resampler.execute(
            ctx,
            {
                "observations": table,
                "time_column": "timestamp",
                "frequency": "1min",
                "aggregation": "mean",
            },
        )

        out = result.outputs["observations"]
        assert out.row_count() >= 1
        # 第一个 1 分钟窗口均值 = (10+20)/2 = 15
        assert any(
            row.get("value") == 15.0 for row in out.rows
        )

    async def test_unsupported_aggregation(self):
        """不支持的聚合方法。"""
        table = _make_table(
            ("timestamp", "value"),
            [{"timestamp": "2026-01-01T00:00:00", "value": 10}],
        )
        resampler = Resampler()
        ctx = make_test_context()
        result = await resampler.execute(
            ctx,
            {
                "observations": table,
                "time_column": "timestamp",
                "frequency": "1min",
                "aggregation": "invalid",
            },
        )

        assert result.diagnostics is not None


# ---- MADOutliers ----


class TestMADOutliers:
    """MAD 异常值检测组件测试。"""

    async def test_detect_outlier(self):
        """检测异常值并标记。"""
        values = [10, 11, 10, 12, 10, 11, 100]
        table = _make_table(
            ("value",), [{"value": v} for v in values]
        )
        detector = MADOutliers()
        ctx = make_test_context()
        result = await detector.execute(
            ctx, {"observations": table, "columns": ["value"], "threshold": 3.0}
        )

        out = result.outputs["observations"]
        report = result.outputs["diagnostics"]

        # 最后一行（100）应被标记为异常
        assert out.rows[-1]["value_outlier"] is True
        assert any(
            ann["status"] == "outlier" for ann in report.row_annotations
        )

    async def test_no_outliers(self):
        """无异常值时全部正常。"""
        values = [10, 11, 10, 11, 10]
        table = _make_table(
            ("value",), [{"value": v} for v in values]
        )
        detector = MADOutliers()
        ctx = make_test_context()
        result = await detector.execute(
            ctx, {"observations": table, "columns": ["value"]}
        )

        out = result.outputs["observations"]
        assert all(row["value_outlier"] is False for row in out.rows)

    async def test_insufficient_data(self):
        """数据不足时产生警告。"""
        table = _make_table(("value",), [{"value": 1}, {"value": 2}])
        detector = MADOutliers()
        ctx = make_test_context()
        result = await detector.execute(
            ctx, {"observations": table, "columns": ["value"]}
        )

        report = result.outputs["diagnostics"]
        assert len(report.warnings) > 0


# ---- SteadyWindow ----


class TestSteadyWindow:
    """稳态窗口识别组件测试。"""

    async def test_identify_steady_window(self):
        """识别稳态窗口。"""
        values = [10, 10.1, 9.9, 10.0, 10.1, 50, 60, 70]
        table = _make_table(
            ("value",), [{"value": v} for v in values]
        )
        sw = SteadyWindow()
        ctx = make_test_context()
        result = await sw.execute(
            ctx,
            {
                "observations": table,
                "value_column": "value",
                "window_size": 3,
                "tolerance": 0.5,
                "metric": "range",
            },
        )

        windows = result.outputs["windows"]
        assert len(windows) >= 1
        # 第一个窗口应为前 5 个点（10, 10.1, 9.9, 10.0, 10.1）
        assert windows[0]["start_index"] == 0
        assert windows[0]["mean"] == pytest.approx(10.02, abs=0.1)

    async def test_no_steady_window(self):
        """持续变化的数据无稳态窗口。"""
        values = [1, 2, 3, 4, 5, 6, 7, 8]
        table = _make_table(
            ("value",), [{"value": v} for v in values]
        )
        sw = SteadyWindow()
        ctx = make_test_context()
        result = await sw.execute(
            ctx,
            {
                "observations": table,
                "value_column": "value",
                "window_size": 3,
                "tolerance": 0.1,
                "metric": "std",
            },
        )

        windows = result.outputs["windows"]
        assert len(windows) == 0

    async def test_insufficient_data(self):
        """数据不足。"""
        table = _make_table(("value",), [{"value": 1}])
        sw = SteadyWindow()
        ctx = make_test_context()
        result = await sw.execute(
            ctx,
            {
                "observations": table,
                "value_column": "value",
                "window_size": 3,
                "tolerance": 0.5,
            },
        )

        assert result.metadata["window_count"] == 0
