"""质量规则与质量引擎单元测试（IRIP Task 16）。

测试四条质量规则（RequiredFieldRule / PositiveValueRule /
MoistureRangeRule / ParticleQuantileOrderRule）以及 QualityEngine 的
综合评估逻辑（overall_status 与 summary 统计）。
"""

from packages.facts.quality import QualityAssessment, QualityLevel, QualityEngine
from packages.facts.quality_rules import (
    MoistureRangeRule,
    ParticleQuantileOrderRule,
    PositiveValueRule,
    RequiredFieldRule,
    _default_rules,
)


class TestParticleQuantileOrderRule:
    """ParticleQuantileOrderRule 测试。"""

    def test_particle_order_rule_blocks_invalid_quantiles(self) -> None:
        """D10=20, D50=18, D90=40 → D10 > D50，违反排序，返回 blocked。"""
        rule = ParticleQuantileOrderRule()
        result = rule.evaluate({"d10_um": 20, "d50_um": 18, "d90_um": 40})
        assert result is not None
        assert result.status == "blocked"
        assert result.code == "particle_quantile_order"
        assert result.level == QualityLevel.Q2
        assert result.evidence["d10"] == 20
        assert result.evidence["d50"] == 18
        assert result.evidence["d90"] == 40

    def test_particle_order_rule_passes_valid_quantiles(self) -> None:
        """D10=1, D50=10, D90=100 → D10 < D50 < D90，通过。"""
        rule = ParticleQuantileOrderRule()
        result = rule.evaluate({"d10_um": 1, "d50_um": 10, "d90_um": 100})
        assert result is not None
        assert result.status == "passed"
        assert result.code == "particle_quantile_order"

    def test_particle_order_rule_not_applicable_when_missing(self) -> None:
        """缺少任一分位数时，规则不适用，返回 None。"""
        rule = ParticleQuantileOrderRule()
        result = rule.evaluate({"d10_um": 1, "d50_um": 10})
        assert result is None

    def test_particle_order_rule_blocks_equal_quantiles(self) -> None:
        """D10=D50=D90 → 不满足严格排序，返回 blocked。"""
        rule = ParticleQuantileOrderRule()
        result = rule.evaluate({"d10_um": 5, "d50_um": 5, "d90_um": 5})
        assert result is not None
        assert result.status == "blocked"


class TestMoistureRangeRule:
    """MoistureRangeRule 测试。"""

    def test_moisture_range_rule_warning(self) -> None:
        """moisture=3.5 → >3.0%，触发 warning。"""
        rule = MoistureRangeRule()
        result = rule.evaluate({"moisture_pct": 3.5})
        assert result is not None
        assert result.status == "warning"
        assert result.code == "moisture_range"
        assert result.level == QualityLevel.Q1
        assert result.evidence["moisture"] == 3.5

    def test_moisture_range_rule_passes_normal(self) -> None:
        """moisture=1.0 → <=3.0%，通过。"""
        rule = MoistureRangeRule()
        result = rule.evaluate({"moisture_pct": 1.0})
        assert result is not None
        assert result.status == "passed"
        assert result.code == "moisture_range"

    def test_moisture_range_rule_not_applicable_when_missing(self) -> None:
        """无湿度字段时，规则不适用。"""
        rule = MoistureRangeRule()
        result = rule.evaluate({"d10_um": 1})
        assert result is None

    def test_moisture_range_rule_accepts_moisture_alias(self) -> None:
        """moisture 字段名也能识别。"""
        rule = MoistureRangeRule()
        result = rule.evaluate({"moisture": 4.0})
        assert result is not None
        assert result.status == "warning"


class TestRequiredFieldRule:
    """RequiredFieldRule 测试。"""

    def test_required_field_rule_blocks_missing_d50(self) -> None:
        """只有 D10 和 D90，缺少 D50 → blocked。"""
        rule = RequiredFieldRule()
        result = rule.evaluate({"d10_um": 1, "d90_um": 100})
        assert result is not None
        assert result.status == "blocked"
        assert result.code == "required_field_missing"
        assert result.level == QualityLevel.Q0
        assert "d50" in result.evidence["missing_fields"]

    def test_required_field_rule_passes_all_present(self) -> None:
        """D10/D50/D90 全部存在 → passed。"""
        rule = RequiredFieldRule()
        result = rule.evaluate(
            {"d10_um": 1, "d50_um": 10, "d90_um": 100}
        )
        assert result is not None
        assert result.status == "passed"

    def test_required_field_rule_blocks_all_missing(self) -> None:
        """全部缺失 → blocked。"""
        rule = RequiredFieldRule()
        result = rule.evaluate({})
        assert result is not None
        assert result.status == "blocked"
        assert len(result.evidence["missing_fields"]) == 3


class TestPositiveValueRule:
    """PositiveValueRule 测试。"""

    def test_positive_value_rule_blocks_negative(self) -> None:
        """D50=-1 → 负值，blocked。"""
        rule = PositiveValueRule()
        result = rule.evaluate(
            {"d10_um": 1, "d50_um": -1, "d90_um": 100}
        )
        assert result is not None
        assert result.status == "blocked"
        assert result.code == "positive_value"
        assert result.level == QualityLevel.Q1
        assert "d50_um" in result.evidence["non_positive"]

    def test_positive_value_rule_passes_all_positive(self) -> None:
        """全为正值 → passed。"""
        rule = PositiveValueRule()
        result = rule.evaluate(
            {"d10_um": 1, "d50_um": 10, "d90_um": 100}
        )
        assert result is not None
        assert result.status == "passed"

    def test_positive_value_rule_not_applicable_when_empty(self) -> None:
        """无可检查字段时，规则不适用。"""
        rule = PositiveValueRule()
        result = rule.evaluate({"moisture_pct": 3.5})
        assert result is None


class TestQualityEngine:
    """QualityEngine 综合评估测试。"""

    def test_quality_engine_overall_blocked(self) -> None:
        """一条 blocked → overall blocked。"""
        engine = QualityEngine()
        # D10 > D50 → ParticleQuantileOrderRule blocked
        observations = {
            "d10_um": 20,
            "d50_um": 18,
            "d90_um": 40,
            "moisture_pct": 1.0,
        }
        assessment = engine.evaluate(observations)
        assert assessment.overall_status == "blocked"
        assert assessment.summary["blocked"] >= 1

    def test_quality_engine_overall_warning(self) -> None:
        """一条 warning，无 blocked → overall warning。"""
        engine = QualityEngine()
        # D10 < D50 < D90 → passed，moisture > 3.0 → warning
        observations = {
            "d10_um": 1,
            "d50_um": 10,
            "d90_um": 100,
            "moisture_pct": 3.5,
        }
        assessment = engine.evaluate(observations)
        assert assessment.overall_status == "warning"
        assert assessment.summary["warning"] >= 1
        assert assessment.summary["blocked"] == 0

    def test_quality_engine_overall_passed(self) -> None:
        """全部 passed → overall passed。"""
        engine = QualityEngine()
        observations = {
            "d10_um": 1,
            "d50_um": 10,
            "d90_um": 100,
            "moisture_pct": 1.0,
        }
        assessment = engine.evaluate(observations)
        assert assessment.overall_status == "passed"
        assert assessment.summary["warning"] == 0
        assert assessment.summary["blocked"] == 0
        assert assessment.summary["passed"] >= 1

    def test_quality_engine_summary_counts(self) -> None:
        """混合结果 → 正确统计计数。"""
        engine = QualityEngine()
        # D10 < D50 < D90 → passed（排序），passed（必填），passed（正值）
        # moisture > 3.0 → warning
        observations = {
            "d10_um": 1,
            "d50_um": 10,
            "d90_um": 100,
            "moisture_pct": 4.0,
        }
        assessment = engine.evaluate(observations)
        assert assessment.summary["passed"] >= 3
        assert assessment.summary["warning"] >= 1
        assert assessment.summary["blocked"] == 0
        # 总结果数 = 默认规则数
        assert len(assessment.results) == len(_default_rules())

    def test_quality_engine_custom_rules(self) -> None:
        """自定义规则集 → 只评估自定义规则。"""
        custom_rule = MoistureRangeRule()
        engine = QualityEngine(rules=(custom_rule,))
        assessment = engine.evaluate({"moisture_pct": 3.5})
        assert len(assessment.results) == 1
        assert assessment.overall_status == "warning"

    def test_quality_engine_empty_observations(self) -> None:
        """空观察值 → RequiredFieldRule blocked，其余不适用。"""
        engine = QualityEngine()
        assessment = engine.evaluate({})
        # RequiredFieldRule 始终返回结果（blocked）
        assert assessment.overall_status == "blocked"
        assert assessment.summary["blocked"] >= 1
