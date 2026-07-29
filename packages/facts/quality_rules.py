"""质量规则定义（IRIP Task 16）。

定义 QualityRule 协议与具体规则实现：

- RequiredFieldRule: 必填字段校验（Q0），D10/D50/D90 必须存在。
- PositiveValueRule: 正值校验（Q1），粒度值必须为正数。
- MoistureRangeRule: 湿度范围校验（Q1），>3.0% 触发 warning。
- ParticleQuantileOrderRule: D10 < D50 < D90 排序校验（Q2 交叉一致性）。

_default_rules() 返回所有默认规则的元组，供 QualityEngine 使用。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.facts.quality import QualityLevel, QualityResult


@runtime_checkable
class QualityRule(Protocol):
    """质量规则协议。

    每条规则声明自己的 code（规则代码）与 level（质量等级），
    并实现 evaluate(observations) 方法对观察值字典进行评估。

    Attributes:
        code: 规则代码（如 ``"particle_quantile_order"``）。
        level: 质量等级（Q0-Q3）。
    """

    code: str
    level: QualityLevel

    def evaluate(self, observations: dict[str, object]) -> QualityResult | None:
        """评估观察值，返回评估结果或 None（规则不适用）。

        Args:
            observations: 字段名→值的字典。

        Returns:
            QualityResult | None: 评估结果，None 表示规则不适用。
        """
        ...


def _get_quantile(observations: dict[str, object], prefix: str) -> float | None:
    """从观察值字典中获取分位数值（兼容 _um 和 _mm 后缀）。

    Args:
        observations: 观察值字典。
        prefix: 分位数前缀（如 ``"d10"``）。

    Returns:
        float | None: 分位数值，不存在时返回 None。
    """
    val = observations.get(f"{prefix}_um")
    if val is None:
        val = observations.get(f"{prefix}_mm")
    if val is None:
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class RequiredFieldRule:
    """必填字段校验（Q0 基本格式）。

    校验 D10、D50、D90 三个分位数字段是否都存在于观察值中。
    缺失任一字段时返回 blocked。

    Attributes:
        code: 规则代码 ``"required_field_missing"``。
        level: 质量等级 Q0。
    """

    code: str = "required_field_missing"
    level: QualityLevel = QualityLevel.Q0

    def evaluate(self, observations: dict[str, object]) -> QualityResult | None:
        """评估必填字段是否存在。

        Args:
            observations: 观察值字典。

        Returns:
            QualityResult: 缺失字段时 blocked，全部存在时 passed。
        """
        d10 = _get_quantile(observations, "d10")
        d50 = _get_quantile(observations, "d50")
        d90 = _get_quantile(observations, "d90")

        missing: list[str] = []
        if d10 is None:
            missing.append("d10")
        if d50 is None:
            missing.append("d50")
        if d90 is None:
            missing.append("d90")

        if missing:
            return QualityResult(
                level=self.level,
                code=self.code,
                status="blocked",
                evidence={"missing_fields": missing},
            )
        return QualityResult(
            level=self.level,
            code=self.code,
            status="passed",
            evidence={"d10": d10, "d50": d50, "d90": d90},
        )


class PositiveValueRule:
    """正值校验（Q1 范围/单位）。

    校验粒度分位数值（d10/d50/d90）和比表面积是否为正数。
    任一值 <= 0 时返回 blocked。

    Attributes:
        code: 规则代码 ``"positive_value"``。
        level: 质量等级 Q1。
    """

    code: str = "positive_value"
    level: QualityLevel = QualityLevel.Q1

    def evaluate(self, observations: dict[str, object]) -> QualityResult | None:
        """评估粒度值是否为正数。

        Args:
            observations: 观察值字典。

        Returns:
            QualityResult | None: 有负值时 blocked，全为正时 passed，
                无可检查字段时 None。
        """
        fields_to_check: dict[str, float] = {}
        for prefix in ("d10", "d50", "d90"):
            val = _get_quantile(observations, prefix)
            if val is not None:
                fields_to_check[f"{prefix}_um"] = val

        # 比表面积
        ss = observations.get("specific_surface")
        if ss is not None:
            try:
                fields_to_check["specific_surface"] = float(ss)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass

        if not fields_to_check:
            return None

        non_positive: dict[str, float] = {}
        for key, val in fields_to_check.items():
            if val <= 0:
                non_positive[key] = val

        if non_positive:
            return QualityResult(
                level=self.level,
                code=self.code,
                status="blocked",
                evidence={"non_positive": non_positive},
            )
        return QualityResult(
            level=self.level,
            code=self.code,
            status="passed",
            evidence={"checked": list(fields_to_check.keys())},
        )


class MoistureRangeRule:
    """湿度范围校验（Q1），>3.0% 触发 warning。

    校验湿度值是否超过 3.0%。超过时返回 warning（不阻断），
    正常时返回 passed。无湿度字段时返回 None（不适用）。

    Attributes:
        code: 规则代码 ``"moisture_range"``。
        level: 质量等级 Q1。
    """

    code: str = "moisture_range"
    level: QualityLevel = QualityLevel.Q1

    #: 湿度告警阈值（%）。
    THRESHOLD: float = 3.0

    def evaluate(self, observations: dict[str, object]) -> QualityResult | None:
        """评估湿度值是否在正常范围。

        Args:
            observations: 观察值字典。

        Returns:
            QualityResult | None: 超阈值时 warning，正常时 passed，
                无湿度字段时 None。
        """
        moisture = observations.get("moisture_pct")
        if moisture is None:
            moisture = observations.get("moisture")
        if moisture is None:
            return None

        try:
            moisture_val = float(moisture)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

        if moisture_val > self.THRESHOLD:
            return QualityResult(
                level=self.level,
                code=self.code,
                status="warning",
                evidence={"moisture": moisture_val, "threshold": self.THRESHOLD},
            )
        return QualityResult(
            level=self.level,
            code=self.code,
            status="passed",
            evidence={"moisture": moisture_val, "threshold": self.THRESHOLD},
        )


class ParticleQuantileOrderRule:
    """D10 < D50 < D90 排序校验（Q2 交叉一致性）。

    校验粒度分位数是否满足 D10 < D50 < D90 的物理排序约束。
    违反时返回 blocked（数据自检失败），通过时返回 passed。
    任一分位数缺失时返回 None（规则不适用）。

    Attributes:
        code: 规则代码 ``"particle_quantile_order"``。
        level: 质量等级 Q2。
    """

    code: str = "particle_quantile_order"
    level: QualityLevel = QualityLevel.Q2

    def evaluate(self, observations: dict[str, object]) -> QualityResult | None:
        """评估 D10 < D50 < D90 排序约束。

        Args:
            observations: 观察值字典。

        Returns:
            QualityResult | None: 排序正确时 passed，违反时 blocked，
                任一分位数缺失时 None。
        """
        d10 = _get_quantile(observations, "d10")
        d50 = _get_quantile(observations, "d50")
        d90 = _get_quantile(observations, "d90")

        if d10 is None or d50 is None or d90 is None:
            return None

        if d10 < d50 < d90:
            return QualityResult(
                level=self.level,
                code=self.code,
                status="passed",
                evidence={"d10": d10, "d50": d50, "d90": d90},
            )
        return QualityResult(
            level=self.level,
            code=self.code,
            status="blocked",
            evidence={"d10": d10, "d50": d50, "d90": d90},
        )


def _default_rules() -> tuple[QualityRule, ...]:
    """返回默认质量规则集。

    包含四条规则，按等级升序排列：
    1. RequiredFieldRule（Q0）：必填字段校验
    2. PositiveValueRule（Q1）：正值校验
    3. MoistureRangeRule（Q1）：湿度范围校验
    4. ParticleQuantileOrderRule（Q2）：分位数排序校验

    Returns:
        tuple[QualityRule, ...]: 默认规则元组。
    """
    return (
        RequiredFieldRule(),
        PositiveValueRule(),
        MoistureRangeRule(),
        ParticleQuantileOrderRule(),
    )
