"""推导算法与执行器协议（IRIP Task 17）。

定义：
- DerivationExecutor 协议：所有推导执行器必须实现 execute 方法；
- ParameterCandidateOutput：推导输出候选（冻结值对象）；
- RobustParameterEstimator：鲁棒参数估计器（中位数 + MAD 阈值 + bootstrap）；
- 全局执行器注册表：按 (component_name, component_version) 查找执行器。

RobustParameterEstimator 算法：
1. 计算所有值的中位数（median）；
2. 计算绝对偏差中位数（MAD = median(|value - median|)）；
3. 标记 |value - median| > threshold * MAD 的值为离群值；
4. 使用固定随机种子进行 bootstrap 估计置信度；
5. 返回 ParameterCandidateOutput（中位数值、置信度、排除原因）。

确定性保证：相同输入值 + 相同参数 + 相同随机种子 → 相同输出。
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ParameterCandidateOutput:
    """推导输出候选（不可变值对象）。

    Attributes:
        variable_code: 输出变量代码。
        value: 估计值（Decimal 或字符串形式）。
        unit: 单位（可选）。
        confidence: 置信度（0.0 ~ 1.0）。
        exclusion_reasons: 被排除成员的原因元组。
    """

    variable_code: str
    value: Decimal
    unit: str | None
    confidence: float
    exclusion_reasons: tuple[str, ...]


@runtime_checkable
class DerivationExecutor(Protocol):
    """推导执行器协议。

    所有推导执行器必须实现此协议，包含 name、version 属性和 execute 方法。

    Attributes:
        name: 执行器组件名称。
        version: 执行器组件版本。
    """

    name: str
    version: str

    def execute(
        self,
        values: Sequence[Decimal],
        parameters: Mapping[str, object],
    ) -> ParameterCandidateOutput:
        """对输入值执行推导算法，返回参数候选。

        Args:
            values: 输入值序列（Decimal）。
            parameters: 算法参数映射。

        Returns:
            ParameterCandidateOutput: 推导输出候选。
        """
        ...


class RobustParameterEstimator:
    """鲁棒参数估计器（中位数 + MAD 阈值 + bootstrap）。

    使用中位数作为鲁棒点估计，MAD（绝对偏差中位数）检测离群值，
    bootstrap（固定种子）估计置信度。

    确定性保证：相同输入 + 相同参数 + 相同随机种子 → 相同输出。

    Attributes:
        name: 执行器组件名称。
        version: 执行器组件版本。
    """

    name: str = "robust-parameter-estimator"
    version: str = "0.1.0"

    def execute(
        self,
        values: Sequence[Decimal],
        parameters: Mapping[str, object],
    ) -> ParameterCandidateOutput:
        """执行鲁棒参数估计。

        算法步骤：
        1. 计算所有值的中位数（median）；
        2. 计算 MAD = median(|value - median|)；
        3. 标记 |value - median| > threshold * MAD 的值为离群值；
        4. 使用 random.Random(seed) 进行 bootstrap 估计置信度；
        5. 返回 ParameterCandidateOutput。

        Args:
            values: 输入值序列（Decimal）。
            parameters: 算法参数，支持：
                outlier_method: "mad"（默认）或 "iqr"；
                threshold: float（默认 3.5）；
                bootstrap_samples: int（默认 1000）；
                random_seed: int（来自配方的随机种子）。

        Returns:
            ParameterCandidateOutput: 中位数估计值、置信度、排除原因。
        """
        if not values:
            return ParameterCandidateOutput(
                variable_code="estimated_value",
                value=Decimal("0"),
                unit=None,
                confidence=0.0,
                exclusion_reasons=(),
            )

        # 提取参数
        outlier_method: str = str(parameters.get("outlier_method", "mad"))
        threshold: float = float(parameters.get("threshold", 3.5))
        bootstrap_samples: int = int(parameters.get("bootstrap_samples", 1000))
        random_seed: int = int(parameters.get("random_seed", 42))

        # 转换为 float 进行计算
        float_values: list[float] = [float(v) for v in values]
        n: int = len(float_values)

        # 1. 计算中位数
        median_value: float = statistics.median(float_values)

        # 2. 计算离群值
        exclusion_reasons: list[str] = []
        non_outlier_values: list[float] = []

        if outlier_method == "iqr":
            # IQR 方法
            sorted_values: list[float] = sorted(float_values)
            if n >= 4:
                q1_index: int = n // 4
                q3_index: int = (3 * n) // 4
                q1: float = sorted_values[q1_index]
                q3: float = sorted_values[q3_index]
                iqr: float = q3 - q1
                lower_bound: float = q1 - 1.5 * iqr
                upper_bound: float = q3 + 1.5 * iqr
            else:
                lower_bound = median_value - abs(median_value) * 0.5
                upper_bound = median_value + abs(median_value) * 0.5
            for i, v in enumerate(float_values):
                if v < lower_bound or v > upper_bound:
                    exclusion_reasons.append(
                        f"value_{i}={v} 超出 IQR 范围 "
                        f"[{lower_bound:.4f}, {upper_bound:.4f}]"
                    )
                else:
                    non_outlier_values.append(v)
        else:
            # MAD 方法（默认）
            abs_deviations: list[float] = [abs(v - median_value) for v in float_values]
            mad: float = statistics.median(abs_deviations) if abs_deviations else 0.0

            if mad > 0:
                mad_threshold: float = threshold * mad
                for i, v in enumerate(float_values):
                    if abs(v - median_value) > mad_threshold:
                        exclusion_reasons.append(
                            f"value_{i}={v} 偏离中位数 {abs(v - median_value):.4f} "
                            f"> {threshold} * MAD={mad:.4f}"
                        )
                    else:
                        non_outlier_values.append(v)
            else:
                # MAD = 0 表示所有值相同，无非离群值
                non_outlier_values = list(float_values)

        # 3. 使用非离群值重新计算中位数（如果有离群值被排除）
        if non_outlier_values and len(non_outlier_values) < n:
            point_estimate: float = statistics.median(non_outlier_values)
        else:
            point_estimate = median_value

        # 4. Bootstrap 估计置信度
        import random

        rng: random.Random = random.Random(random_seed)
        bootstrap_medians: list[float] = []

        sample_values: list[float] = (
            non_outlier_values if non_outlier_values else float_values
        )
        sample_n: int = len(sample_values)

        if sample_n > 0:
            for _ in range(bootstrap_samples):
                # 有放回重采样
                resampled: list[float] = [
                    sample_values[rng.randint(0, sample_n - 1)]
                    for _ in range(sample_n)
                ]
                bootstrap_medians.append(statistics.median(resampled))

        # 置信度：1 - 变异系数（CV = std / |mean|），限制在 [0, 1]
        if bootstrap_medians and abs(point_estimate) > 1e-10:
            bs_std: float = statistics.stdev(bootstrap_medians) if len(bootstrap_medians) > 1 else 0.0
            cv: float = bs_std / abs(point_estimate)
            confidence: float = max(0.0, min(1.0, 1.0 - cv))
        elif bootstrap_medians:
            bs_std = statistics.stdev(bootstrap_medians) if len(bootstrap_medians) > 1 else 0.0
            confidence = max(0.0, min(1.0, 1.0 - bs_std))
        else:
            confidence = 0.0

        # 5. 返回 ParameterCandidateOutput
        # 将 float 转回 Decimal（保留精度）
        try:
            decimal_value: Decimal = Decimal(str(point_estimate))
        except Exception:
            decimal_value = Decimal("0")

        return ParameterCandidateOutput(
            variable_code="estimated_value",
            value=decimal_value,
            unit=None,
            confidence=round(confidence, 6),
            exclusion_reasons=tuple(exclusion_reasons),
        )


# ---- 全局执行器注册表 ----

#: 执行器注册表：key = (component_name, component_version), value = executor。
_REGISTRY: dict[tuple[str, str], DerivationExecutor] = {}


def register_executor(executor: DerivationExecutor) -> None:
    """注册推导执行器到全局注册表。

    Args:
        executor: 实现 DerivationExecutor 协议的执行器实例。
    """
    _REGISTRY[(executor.name, executor.version)] = executor


def get_executor(
    component_name: str,
    component_version: str,
) -> DerivationExecutor | None:
    """按组件名称和版本查找执行器。

    Args:
        component_name: 组件名称。
        component_version: 组件版本。

    Returns:
        DerivationExecutor | None: 找到的执行器，或 None。
    """
    return _REGISTRY.get((component_name, component_version))


def _register_defaults() -> None:
    """注册默认执行器。"""
    register_executor(RobustParameterEstimator())


# 模块加载时自动注册默认执行器
_register_defaults()
