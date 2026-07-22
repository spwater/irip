"""质量/统计/输出组件单元测试（11 个）。

测试覆盖：
- Quality (4): SchemaCheck, RangeCheck, ParticleOrder, RelationCompleteness
- Statistics (4): Descriptive, RobustEstimator, BootstrapInterval, CurveFit
- Output (3): ParameterCard, ExperimentComparison, ReportDraft
"""

import pytest

from packages.components.builtin.output.experiment_comparison import (
    ExperimentComparison,
)
from packages.components.builtin.output.parameter_card import ParameterCard
from packages.components.builtin.output.report_draft import ReportDraft
from packages.components.builtin.quality.particle_order import ParticleOrder
from packages.components.builtin.quality.range_check import RangeCheck
from packages.components.builtin.quality.relation_completeness import (
    RelationCompleteness,
)
from packages.components.builtin.quality.schema_check import SchemaCheck
from packages.components.builtin.statistics.bootstrap_interval import (
    BootstrapInterval,
)
from packages.components.builtin.statistics.curve_fit import CurveFit
from packages.components.builtin.statistics.descriptive import Descriptive
from packages.components.builtin.statistics.robust_estimator import (
    RobustEstimator,
)
from packages.components.builtin.types import (
    ObservationTable,
    ParameterCandidate,
)

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


# ===== Quality Components =====


class TestSchemaCheck:
    """Schema 检查组件测试。"""

    async def test_type_check_pass(self):
        """类型校验通过。"""
        table = _make_table(
            ("name", "value"),
            [{"name": "D50", "value": 12.5}, {"name": "D90", "value": 25.0}],
        )
        checker = SchemaCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "schema": {
                    "name": {"type": "string", "required": True},
                    "value": {"type": "number", "required": True, "min": 0},
                },
            },
        )

        assert result.metadata["fail_count"] == 0
        assert result.metadata["pass_count"] == 2

    async def test_type_mismatch(self):
        """类型不匹配。"""
        table = _make_table(
            ("name", "value"),
            [{"name": "D50", "value": "not_a_number"}],
        )
        checker = SchemaCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "schema": {"value": {"type": "number", "required": True}},
            },
        )

        assert result.metadata["fail_count"] == 1
        report = result.outputs["diagnostics"]
        assert any(
            "type_mismatch" in ann["detail"]
            for ann in report.row_annotations
        )

    async def test_missing_required(self):
        """必需列缺失。"""
        table = _make_table(("a",), [{"a": 1}])
        checker = SchemaCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {"observations": table, "schema": {"b": {"required": True}}},
        )

        report = result.outputs["diagnostics"]
        assert any("缺失" in w for w in report.warnings)

    async def test_range_constraint(self):
        """min/max 约束检查。"""
        table = _make_table(
            ("value",), [{"value": -5}, {"value": 10}, {"value": 100}]
        )
        checker = SchemaCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "schema": {"value": {"type": "number", "min": 0, "max": 50}},
            },
        )

        assert result.metadata["fail_count"] == 2


class TestRangeCheck:
    """值域边界检查组件测试。"""

    async def test_within_range(self):
        """值在范围内。"""
        table = _make_table(
            ("value",), [{"value": 10}, {"value": 20}, {"value": 30}]
        )
        checker = RangeCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {"observations": table, "rules": [{"column": "value", "min": 0, "max": 50}]},
        )

        assert result.metadata["fail_count"] == 0

    async def test_out_of_range(self):
        """值超出范围。"""
        table = _make_table(
            ("value",), [{"value": -5}, {"value": 10}, {"value": 100}]
        )
        checker = RangeCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {"observations": table, "rules": [{"column": "value", "min": 0, "max": 50}]},
        )

        assert result.metadata["fail_count"] == 2

    async def test_non_inclusive(self):
        """非包含边界。"""
        table = _make_table(("value",), [{"value": 0}, {"value": 5}])
        checker = RangeCheck()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "rules": [{"column": "value", "min": 0, "max": 10}],
                "inclusive": False,
            },
        )

        assert result.metadata["fail_count"] == 1


class TestParticleOrder:
    """粒度序检查组件测试。"""

    async def test_correct_order(self):
        """正确的 D10<D50<D90。"""
        table = _make_table(
            ("D10", "D50", "D90"),
            [{"D10": 5, "D50": 15, "D90": 30}],
        )
        checker = ParticleOrder()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "d10_column": "D10",
                "d50_column": "D50",
                "d90_column": "D90",
            },
        )

        assert result.metadata["fail_count"] == 0

    async def test_wrong_order(self):
        """错误的 D10>=D50。"""
        table = _make_table(
            ("D10", "D50", "D90"),
            [{"D10": 20, "D50": 15, "D90": 30}],
        )
        checker = ParticleOrder()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "d10_column": "D10",
                "d50_column": "D50",
                "d90_column": "D90",
            },
        )

        assert result.metadata["fail_count"] == 1

    async def test_missing_values(self):
        """缺失值导致失败。"""
        table = _make_table(
            ("D10", "D50", "D90"),
            [{"D10": None, "D50": 15, "D90": 30}],
        )
        checker = ParticleOrder()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": table,
                "d10_column": "D10",
                "d50_column": "D50",
                "d90_column": "D90",
            },
        )

        assert result.metadata["fail_count"] == 1


class TestRelationCompleteness:
    """引用完整性检查组件测试。"""

    async def test_all_keys_found(self):
        """所有外键都能找到。"""
        child = _make_table(
            ("id", "parent_id"),
            [{"id": 1, "parent_id": 100}, {"id": 2, "parent_id": 200}],
        )
        parent = _make_table(
            ("pid",), [{"pid": 100}, {"pid": 200}, {"pid": 300}]
        )
        checker = RelationCompleteness()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": child,
                "parent_table": parent,
                "foreign_key": "parent_id",
                "parent_key": "pid",
            },
        )

        assert result.metadata["fail_count"] == 0

    async def test_missing_key(self):
        """外键在父表中不存在。"""
        child = _make_table(
            ("id", "parent_id"),
            [{"id": 1, "parent_id": 100}, {"id": 2, "parent_id": 999}],
        )
        parent = _make_table(("pid",), [{"pid": 100}, {"pid": 200}])
        checker = RelationCompleteness()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": child,
                "parent_table": parent,
                "foreign_key": "parent_id",
                "parent_key": "pid",
            },
        )

        assert result.metadata["fail_count"] == 1

    async def test_null_foreign_key(self):
        """空外键失败。"""
        child = _make_table(
            ("id", "parent_id"),
            [{"id": 1, "parent_id": None}],
        )
        parent = _make_table(("pid",), [{"pid": 100}])
        checker = RelationCompleteness()
        ctx = make_test_context()
        result = await checker.execute(
            ctx,
            {
                "observations": child,
                "parent_table": parent,
                "foreign_key": "parent_id",
                "parent_key": "pid",
            },
        )

        assert result.metadata["fail_count"] == 1


# ===== Statistics Components =====


class TestDescriptive:
    """描述性统计组件测试。"""

    async def test_basic_stats(self):
        """基本统计量。"""
        table = _make_table(
            ("value",), [{"value": v} for v in [1, 2, 3, 4, 5]]
        )
        desc = Descriptive()
        ctx = make_test_context()
        result = await desc.execute(ctx, {"observations": table, "columns": ["value"]})

        stats = result.outputs["statistics"]
        assert stats["value"]["count"] == 5
        assert stats["value"]["mean"] == pytest.approx(3.0)
        assert stats["value"]["min"] == 1
        assert stats["value"]["max"] == 5
        assert stats["value"]["median"] == pytest.approx(3.0)

    async def test_empty_column(self):
        """空列产生警告。"""
        table = _make_table(("empty",), [{"empty": None}])
        desc = Descriptive()
        ctx = make_test_context()
        result = await desc.execute(ctx, {"observations": table, "columns": ["empty"]})

        report = result.outputs["diagnostics"]
        assert len(report.warnings) > 0


class TestRobustEstimator:
    """稳健估计器组件测试。"""

    async def test_robust_stats(self):
        """稳健统计量。"""
        table = _make_table(
            ("value",), [{"value": v} for v in [10, 11, 10, 12, 10, 11, 100]]
        )
        est = RobustEstimator()
        ctx = make_test_context()
        result = await est.execute(ctx, {"observations": table, "columns": ["value"]})

        stats = result.outputs["statistics"]
        assert stats["value"]["median"] == pytest.approx(11.0)
        assert stats["value"]["mad"] == pytest.approx(1.0)
        # 截尾均值应接近 10.x 而非受 100 拉高
        assert stats["value"]["trimmed_mean"] < 30

    async def test_trimmed_mean(self):
        """截尾均值过滤极端值。"""
        table = _make_table(
            ("value",), [{"value": v} for v in [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]]
        )
        est = RobustEstimator()
        ctx = make_test_context()
        result = await est.execute(
            ctx,
            {"observations": table, "columns": ["value"], "trim_percent": 0.1},
        )

        stats = result.outputs["statistics"]
        # 截尾后 100 被移除，均值应远小于包含 100 的均值
        assert stats["value"]["trimmed_mean"] < 20


class TestBootstrapInterval:
    """Bootstrap 置信区间组件测试。"""

    async def test_mean_interval(self):
        """均值置信区间。"""
        import random

        rng = random.Random(42)
        values = [rng.gauss(10, 2) for _ in range(100)]
        table = _make_table(
            ("value",), [{"value": v} for v in values]
        )
        boot = BootstrapInterval()
        ctx = make_test_context()
        result = await boot.execute(
            ctx,
            {
                "observations": table,
                "column": "value",
                "statistic": "mean",
                "iterations": 1000,
                "seed": 42,
            },
        )

        intervals = result.outputs["intervals"]
        ci = intervals["value"]
        assert ci["lower"] < 10 < ci["upper"]
        assert ci["confidence"] == 0.95
        assert ci["iterations"] == 1000

    async def test_reproducible(self):
        """固定种子保证可重复性。"""
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        table = _make_table(("value",), [{"value": v} for v in values])

        boot = BootstrapInterval()
        ctx = make_test_context()

        result1 = await boot.execute(
            ctx,
            {"observations": table, "column": "value", "iterations": 500, "seed": 123},
        )
        result2 = await boot.execute(
            ctx,
            {"observations": table, "column": "value", "iterations": 500, "seed": 123},
        )

        ci1 = result1.outputs["intervals"]["value"]
        ci2 = result2.outputs["intervals"]["value"]
        assert ci1["lower"] == ci2["lower"]
        assert ci1["upper"] == ci2["upper"]

    async def test_insufficient_data(self):
        """数据不足时警告。"""
        table = _make_table(("value",), [{"value": 1}])
        boot = BootstrapInterval()
        ctx = make_test_context()
        result = await boot.execute(
            ctx, {"observations": table, "column": "value"}
        )

        report = result.outputs["diagnostics"]
        assert len(report.warnings) > 0


class TestCurveFit:
    """曲线拟合组件测试。"""

    async def test_linear_fit(self):
        """线性拟合。"""
        xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        ys = [2 * x + 1 for x in xs]  # y = 2x + 1
        table = _make_table(
            ("x", "y"), [{"x": x, "y": y} for x, y in zip(xs, ys)]
        )
        fitter = CurveFit()
        ctx = make_test_context()
        result = await fitter.execute(
            ctx,
            {
                "observations": table,
                "x_column": "x",
                "y_column": "y",
                "model": "linear",
            },
        )

        params = result.outputs["model_params"]
        assert params["model"] == "linear"
        assert params["parameters"]["slope"]["value"] == pytest.approx(2.0, rel=0.01)
        assert params["parameters"]["intercept"]["value"] == pytest.approx(1.0, rel=0.01)
        assert params["r_squared"] > 0.99

    async def test_insufficient_points(self):
        """数据点不足。"""
        table = _make_table(("x", "y"), [{"x": 1, "y": 2}])
        fitter = CurveFit()
        ctx = make_test_context()
        result = await fitter.execute(
            ctx,
            {
                "observations": table,
                "x_column": "x",
                "y_column": "y",
                "model": "linear",
            },
        )

        report = result.outputs["diagnostics"]
        assert len(report.warnings) > 0


# ===== Output Components =====


class TestParameterCard:
    """参数候选生成组件测试。"""

    async def test_generate_candidates(self):
        """生成参数候选。"""
        table = _make_table(
            ("variable", "value", "unit"),
            [
                {"variable": "D50", "value": "12.5", "unit": "um"},
                {"variable": "D50", "value": "13.0", "unit": "um"},
                {"variable": "D50", "value": "12.8", "unit": "um"},
            ],
        )
        card = ParameterCard()
        ctx = make_test_context()
        result = await card.execute(
            ctx,
            {
                "observations": table,
                "variable_code": "particle_d50",
                "value_column": "value",
                "unit_column": "unit",
            },
        )

        candidates = result.outputs["candidates"]
        assert len(candidates) == 3
        assert all(isinstance(c, ParameterCandidate) for c in candidates)
        assert all(c.variable_code == "particle_d50" for c in candidates)
        assert result.metadata["active_candidates"] == 3

    async def test_exclusion_rules(self):
        """排除规则生效。"""
        table = _make_table(
            ("value", "status"),
            [
                {"value": "12.5", "status": "valid"},
                {"value": "13.0", "status": "rejected"},
            ],
        )
        card = ParameterCard()
        ctx = make_test_context()
        result = await card.execute(
            ctx,
            {
                "observations": table,
                "variable_code": "test_var",
                "value_column": "value",
                "exclusion_rules": [
                    {"column": "status", "operator": "eq", "value": "rejected"}
                ],
            },
        )

        candidates = result.outputs["candidates"]
        assert candidates[0].exclusion_reasons == ()
        assert len(candidates[1].exclusion_reasons) > 0
        assert result.metadata["excluded_candidates"] == 1

    async def test_skip_missing_value(self):
        """缺失值跳过。"""
        table = _make_table(
            ("value",), [{"value": None}, {"value": "10"}]
        )
        card = ParameterCard()
        ctx = make_test_context()
        result = await card.execute(
            ctx,
            {"observations": table, "variable_code": "v", "value_column": "value"},
        )

        candidates = result.outputs["candidates"]
        assert len(candidates) == 1


class TestExperimentComparison:
    """多实验对照表组件测试。"""

    async def test_compare_two_experiments(self):
        """两个实验对照。"""
        exp_a = _make_table(
            ("sample", "value"), [{"sample": "s1", "value": 10}, {"sample": "s2", "value": 20}]
        )
        exp_b = _make_table(
            ("sample", "value"), [{"sample": "s1", "value": 15}, {"sample": "s3", "value": 25}]
        )
        comp = ExperimentComparison()
        ctx = make_test_context()
        result = await comp.execute(
            ctx,
            {
                "experiments": [
                    {"label": "exp_a", "observations": exp_a},
                    {"label": "exp_b", "observations": exp_b},
                ],
                "key_column": "sample",
                "value_columns": ["value"],
            },
        )

        table = result.outputs["comparison_table"]
        assert table.row_count() == 3  # s1, s2, s3
        assert "exp_a.value" in table.columns
        assert "exp_b.value" in table.columns

        # s1 应有两个实验的值
        s1_row = [r for r in table.rows if r["sample"] == "s1"][0]
        assert s1_row["exp_a.value"] == 10
        assert s1_row["exp_b.value"] == 15

        # s2 只有 exp_a 的值
        s2_row = [r for r in table.rows if r["sample"] == "s2"][0]
        assert s2_row["exp_a.value"] == 20
        assert s2_row["exp_b.value"] is None

    async def test_text_table_output(self):
        """文本对照表输出。"""
        exp_a = _make_table(
            ("sample", "value"), [{"sample": "s1", "value": 10}]
        )
        comp = ExperimentComparison()
        ctx = make_test_context()
        result = await comp.execute(
            ctx,
            {
                "experiments": [{"label": "A", "observations": exp_a}],
                "key_column": "sample",
                "value_columns": ["value"],
            },
        )

        text = result.outputs["text_table"]
        assert "sample" in text
        assert "A.value" in text


class TestReportDraft:
    """报告草稿组件测试。"""

    async def test_generate_report(self):
        """生成 Markdown 报告。"""
        report_gen = ReportDraft()
        ctx = make_test_context()
        result = await report_gen.execute(
            ctx,
            {
                "title": "测试报告",
                "sections": [
                    {"heading": "概述", "content": "这是概述。"},
                    {
                        "heading": "数据",
                        "content": {"行数": 100, "列数": 5},
                    },
                    {
                        "heading": "结论",
                        "content": ["结论一", "结论二"],
                    },
                ],
                "metadata": {"作者": "测试", "日期": "2026-01-01"},
            },
        )

        report = result.outputs["report"]
        assert "# 测试报告" in report
        assert "## 概述" in report
        assert "这是概述。" in report
        assert "## 数据" in report
        assert "行数" in report
        assert "结论一" in report
        assert "作者" in report

    async def test_empty_sections(self):
        """空章节列表。"""
        report_gen = ReportDraft()
        ctx = make_test_context()
        result = await report_gen.execute(
            ctx, {"title": "空报告", "sections": []}
        )

        assert "# 空报告" in result.outputs["report"]
        assert result.metadata["section_count"] == 0
