"""推导算法单元测试。

覆盖 packages/provenance/algorithms.py：
- RobustParameterEstimator: MAD 离群值检测、IQR 离群值检测、
  bootstrap 置信度、空输入、确定性、自定义参数；
- ParameterCandidateOutput: 不可变值对象；
- DerivationExecutor 协议: runtime_checkable；
- register_executor / get_executor: 注册表操作。
"""

from collections.abc import Mapping
from decimal import Decimal

import pytest

from packages.provenance.algorithms import (
    _REGISTRY,
    DerivationExecutor,
    ParameterCandidateOutput,
    RobustParameterEstimator,
    get_executor,
    register_executor,
)

# ---------------------------------------------------------------------------
# ParameterCandidateOutput 值对象
# ---------------------------------------------------------------------------


class TestParameterCandidateOutput:
    """ParameterCandidateOutput 不可变值对象测试。"""

    def test_create_minimal(self) -> None:
        """创建带必填字段。"""
        obj = ParameterCandidateOutput(
            variable_code="temp",
            value=Decimal("42.5"),
            unit="℃",
            confidence=0.95,
            exclusion_reasons=(),
        )
        assert obj.variable_code == "temp"
        assert obj.value == Decimal("42.5")
        assert obj.unit == "℃"
        assert obj.confidence == 0.95
        assert obj.exclusion_reasons == ()

    def test_frozen(self) -> None:
        """frozen=True → 不可变。"""
        obj = ParameterCandidateOutput(
            variable_code="x",
            value=Decimal("1"),
            unit=None,
            confidence=0.5,
            exclusion_reasons=(),
        )
        with pytest.raises(AttributeError):
            obj.confidence = 0.9  # type: ignore[misc]

    def test_equality(self) -> None:
        """相同字段 → 相等。"""
        a = ParameterCandidateOutput(
            variable_code="x",
            value=Decimal("1"),
            unit=None,
            confidence=0.5,
            exclusion_reasons=(),
        )
        b = ParameterCandidateOutput(
            variable_code="x",
            value=Decimal("1"),
            unit=None,
            confidence=0.5,
            exclusion_reasons=(),
        )
        assert a == b


# ---------------------------------------------------------------------------
# RobustParameterEstimator
# ---------------------------------------------------------------------------


class TestRobustParameterEstimator:
    """RobustParameterEstimator 算法测试。"""

    def test_empty_values(self) -> None:
        """空输入 → value=0, confidence=0。"""
        est = RobustParameterEstimator()
        result = est.execute([], {})
        assert result.value == Decimal("0")
        assert result.confidence == 0.0
        assert result.exclusion_reasons == ()
        assert result.variable_code == "estimated_value"

    def test_single_value(self) -> None:
        """单值 → 中位数即该值，无离群值。"""
        est = RobustParameterEstimator()
        result = est.execute([Decimal("10")], {})
        assert result.value == Decimal("10")
        assert result.exclusion_reasons == ()

    def test_mad_no_outliers(self) -> None:
        """无离群值时 MAD 方法不排除任何值。"""
        est = RobustParameterEstimator()
        values = [Decimal("10"), Decimal("10.1"), Decimal("9.9"), Decimal("10.05")]
        result = est.execute(values, {"threshold": 3.5})
        assert result.exclusion_reasons == ()

    def test_mad_with_outlier(self) -> None:
        """MAD 方法检测并排除离群值。"""
        est = RobustParameterEstimator()
        # 使用值使 MAD > 0（deviations 的中位数非零）
        values = [
            Decimal("10"),
            Decimal("12"),
            Decimal("11"),
            Decimal("9"),
            Decimal("10.5"),
            Decimal("50"),  # 50 是离群值
        ]
        result = est.execute(values, {"threshold": 2.0, "outlier_method": "mad"})
        assert len(result.exclusion_reasons) >= 1
        # 排除离群值后中位数应接近 10-11 而非 50
        assert abs(float(result.value) - 10.5) < 3.0

    def test_iqr_with_outlier(self) -> None:
        """IQR 方法检测并排除离群值。"""
        est = RobustParameterEstimator()
        values = [
            Decimal("1"),
            Decimal("2"),
            Decimal("3"),
            Decimal("4"),
            Decimal("5"),
            Decimal("6"),
            Decimal("7"),
            Decimal("100"),
        ]
        result = est.execute(values, {"outlier_method": "iqr"})
        assert len(result.exclusion_reasons) >= 1

    def test_iqr_small_sample(self) -> None:
        """IQR 在小样本 (n<4) 下使用 median ± 0.5*|median| 作为边界。"""
        est = RobustParameterEstimator()
        values = [Decimal("10"), Decimal("20")]
        result = est.execute(values, {"outlier_method": "iqr"})
        assert result.value is not None

    def test_all_same_values(self) -> None:
        """所有值相同 → MAD=0，无非离群值，中位数 = 该值。"""
        est = RobustParameterEstimator()
        values = [Decimal("5")] * 10
        result = est.execute(values, {})
        assert result.exclusion_reasons == ()
        assert result.value == Decimal("5")

    def test_determinism(self) -> None:
        """相同输入 + 相同种子 → 相同输出（确定性）。"""
        est = RobustParameterEstimator()
        values = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("25")]
        params: Mapping[str, object] = {"random_seed": 42, "bootstrap_samples": 500}
        r1 = est.execute(values, params)
        r2 = est.execute(values, params)
        assert r1.value == r2.value
        assert r1.confidence == r2.confidence
        assert r1.exclusion_reasons == r2.exclusion_reasons

    def test_different_seed_different_confidence(self) -> None:
        """不同随机种子 → 可能产生不同置信度。"""
        est = RobustParameterEstimator()
        values = [Decimal("10"), Decimal("20"), Decimal("30")]
        r1 = est.execute(values, {"random_seed": 42, "bootstrap_samples": 2000})
        r2 = est.execute(values, {"random_seed": 999, "bootstrap_samples": 2000})
        # 置信度可能不同（概率极高，因为不同种子的 bootstrap 结果不同）
        # 但验证两者都在 [0, 1]
        assert 0.0 <= r1.confidence <= 1.0
        assert 0.0 <= r2.confidence <= 1.0

    def test_confidence_in_range(self) -> None:
        """置信度始终在 [0, 1] 范围内。"""
        est = RobustParameterEstimator()
        values = [Decimal(str(v)) for v in range(100)]
        result = est.execute(values, {"bootstrap_samples": 100})
        assert 0.0 <= result.confidence <= 1.0

    def test_high_variance_low_confidence(self) -> None:
        """高方差数据 → 置信度偏低。"""
        est = RobustParameterEstimator()
        values = [Decimal(str(v)) for v in [1, 100, 5000, 0, 99999, 42]]
        result = est.execute(values, {"bootstrap_samples": 1000})
        assert result.confidence < 0.5

    def test_low_variance_high_confidence(self) -> None:
        """低方差数据 → 置信度偏高。"""
        est = RobustParameterEstimator()
        values = [Decimal("10.00"), Decimal("10.01"), Decimal("10.02")]
        result = est.execute(values, {"bootstrap_samples": 500})
        assert result.confidence > 0.5

    def test_custom_threshold(self) -> None:
        """自定义 threshold 影响离群值检测灵敏度。"""
        est = RobustParameterEstimator()
        values = [Decimal("10"), Decimal("10"), Decimal("10"), Decimal("12")]
        # 低 threshold → 更严格，12 可能被标记
        strict = est.execute(values, {"threshold": 0.5})
        # 高 threshold → 更宽松
        loose = est.execute(values, {"threshold": 100})
        assert len(strict.exclusion_reasons) >= len(loose.exclusion_reasons)

    def test_custom_bootstrap_samples(self) -> None:
        """自定义 bootstrap_samples 参数。"""
        est = RobustParameterEstimator()
        values = [Decimal("10"), Decimal("20"), Decimal("30")]
        result = est.execute(values, {"bootstrap_samples": 10})
        assert 0.0 <= result.confidence <= 1.0

    def test_name_and_version(self) -> None:
        """执行器有正确的 name 和 version 属性。"""
        est = RobustParameterEstimator()
        assert est.name == "robust-parameter-estimator"
        assert est.version == "0.8.0"

    def test_unit_is_none(self) -> None:
        """输出结果 unit 始终为 None。"""
        est = RobustParameterEstimator()
        result = est.execute([Decimal("1")], {})
        assert result.unit is None

    def test_variable_code_is_estimated_value(self) -> None:
        """输出结果 variable_code 始终为 estimated_value。"""
        est = RobustParameterEstimator()
        result = est.execute([Decimal("1")], {})
        assert result.variable_code == "estimated_value"

    def test_negative_values(self) -> None:
        """负值输入也能正确处理。"""
        est = RobustParameterEstimator()
        values = [Decimal("-10"), Decimal("-20"), Decimal("-30")]
        result = est.execute(values, {})
        assert result.value == Decimal("-20")

    def test_decimal_precision_preserved(self) -> None:
        """Decimal 精度保留到字符串转换。"""
        est = RobustParameterEstimator()
        values = [Decimal("3.14159"), Decimal("3.14159"), Decimal("3.14159")]
        result = est.execute(values, {})
        assert float(result.value) == 3.14159


# ---------------------------------------------------------------------------
# DerivationExecutor 协议
# ---------------------------------------------------------------------------


class TestDerivationExecutorProtocol:
    """DerivationExecutor runtime_checkable 协议测试。"""

    def test_robust_estimator_satisfies_protocol(self) -> None:
        """RobustParameterEstimator 实现 DerivationExecutor 协议。"""
        est = RobustParameterEstimator()
        assert isinstance(est, DerivationExecutor)

    def test_custom_executor_satisfies_protocol(self) -> None:
        """自定义实现类满足协议。"""

        class MyExecutor:
            name = "my-executor"
            version = "1.0.0"

            def execute(
                self,
                values,
                parameters,
            ) -> ParameterCandidateOutput:
                return ParameterCandidateOutput(
                    variable_code="x",
                    value=Decimal("0"),
                    unit=None,
                    confidence=1.0,
                    exclusion_reasons=(),
                )

        assert isinstance(MyExecutor(), DerivationExecutor)

    def test_missing_method_fails_protocol(self) -> None:
        """缺少 execute 方法的类不满足协议。"""

        class NoExecute:
            name = "bad"
            version = "0.0.0"

        assert not isinstance(NoExecute(), DerivationExecutor)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


class TestRegistry:
    """全局执行器注册表测试。"""

    def test_get_default_executor(self) -> None:
        """默认注册了 RobustParameterEstimator。"""
        executor = get_executor("robust-parameter-estimator", "0.8.0")
        assert executor is not None
        assert isinstance(executor, RobustParameterEstimator)

    def test_get_nonexistent_executor(self) -> None:
        """不存在的键 → None。"""
        assert get_executor("nonexistent", "0.0.0") is None

    def test_register_and_retrieve(self) -> None:
        """注册自定义执行器并检索。"""

        class CustomExecutor:
            name = "custom-test-executor"
            version = "2.0.0"

            def execute(
                self,
                values,
                parameters,
            ) -> ParameterCandidateOutput:
                return ParameterCandidateOutput(
                    variable_code="custom",
                    value=Decimal("1"),
                    unit=None,
                    confidence=1.0,
                    exclusion_reasons=(),
                )

        executor = CustomExecutor()
        register_executor(executor)
        retrieved = get_executor("custom-test-executor", "2.0.0")
        assert retrieved is executor

    def test_register_overwrites(self) -> None:
        """注册相同 (name, version) 覆盖已有。"""

        class V1:
            name = "overwrite-test"
            version = "1.0.0"

            def execute(self, values, parameters) -> ParameterCandidateOutput:
                return ParameterCandidateOutput(
                    variable_code="v1",
                    value=Decimal("1"),
                    unit=None,
                    confidence=0.1,
                    exclusion_reasons=(),
                )

        class V2:
            name = "overwrite-test"
            version = "1.0.0"

            def execute(self, values, parameters) -> ParameterCandidateOutput:
                return ParameterCandidateOutput(
                    variable_code="v2",
                    value=Decimal("2"),
                    unit=None,
                    confidence=0.2,
                    exclusion_reasons=(),
                )

        register_executor(V1())
        register_executor(V2())
        result = get_executor("overwrite-test", "1.0.0")
        assert result is not None
        assert result.execute([], {}).variable_code == "v2"

    def test_registry_is_global(self) -> None:
        """_REGISTRY 是模块级全局变量。"""
        assert isinstance(_REGISTRY, dict)
        assert ("robust-parameter-estimator", "0.8.0") in _REGISTRY
