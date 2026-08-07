"""AI 数值计算工具 — 轻量单位标签系统。

三态 UnitTag：已知单位（KNOWN）、明确无量纲（DIMENSIONLESS）、单位未知（UNKNOWN）。
只做安全检查和标签传播，不做自动换算，也不试图实现完整物理量系统。

设计文档 §11：轻量单位策略
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from packages.ai.numeric.contracts import NumericError


class UnitTagState(enum.Enum):
    """单位标签三态。"""

    KNOWN = "known"
    DIMENSIONLESS = "dimensionless"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UnitTag:
    """单位标签（不可变值对象）。

    Attributes:
        state: 三态之一。
        label: 单位字符串（KNOWN 时为单位名，DIMENSIONLESS 时为 "1"，UNKNOWN 时为 None）。
    """

    state: UnitTagState
    label: str | None = None

    @classmethod
    def known(cls, label: str) -> UnitTag:
        """创建已知单位标签。"""
        return cls(state=UnitTagState.KNOWN, label=label)

    @classmethod
    def dimensionless(cls) -> UnitTag:
        """创建明确无量纲标签。"""
        return cls(state=UnitTagState.DIMENSIONLESS, label="1")

    @classmethod
    def unknown(cls) -> UnitTag:
        """创建单位未知标签。"""
        return cls(state=UnitTagState.UNKNOWN, label=None)

    @classmethod
    def from_unit_string(cls, unit: str | None) -> UnitTag:
        """从 unit 字符串构造标签。

        None → UNKNOWN，"1" → DIMENSIONLESS，其他 → KNOWN。
        """
        if unit is None:
            return cls.unknown()
        if unit == "1":
            return cls.dimensionless()
        return cls.known(unit)

    @property
    def is_known(self) -> bool:
        """是否为已知单位（KNOWN 或 DIMENSIONLESS，即非 UNKNOWN）。"""
        return self.state != UnitTagState.UNKNOWN

    @property
    def is_dimensionless(self) -> bool:
        """是否为明确无量纲。"""
        return self.state == UnitTagState.DIMENSIONLESS

    @property
    def is_unknown(self) -> bool:
        """是否为单位未知。"""
        return self.state == UnitTagState.UNKNOWN

    def to_output(self) -> str | None:
        """转换为输出用单位字符串（UNKNOWN → None，DIMENSIONLESS → "1"）。"""
        return self.label


def _labels_match(left: UnitTag, right: UnitTag) -> bool:
    """判断两个已知单位标签是否完全相同。"""
    return left.state == right.state and left.label == right.label


# =============================================================================
# 加减取模：同单位检查
# =============================================================================


def combine_additive(
    left: UnitTag,
    right: UnitTag,
    op_name: str = "addition",
) -> tuple[UnitTag, list[str]]:
    """加减取模的单位传播。

    规则：
    - 两个已知单位必须完全相同，否则拒绝；
    - 一个已知一个未知时允许计算，结果附 unit_unverified warning；
    - 两个未知时结果未知并附 warning。

    Args:
        left: 左操作数单位。
        right: 右操作数单位。
        op_name: 运算名（用于错误消息）。

    Returns:
        (结果单位, 警告列表)。

    Raises:
        NumericError: 已知单位不兼容时。
    """
    warnings: list[str] = []
    left_known = left.is_known
    right_known = right.is_known

    if left_known and right_known:
        if not _labels_match(left, right):
            raise NumericError(
                code="numeric_unit_conflict",
                message=f"unit conflict in {op_name}: {left.label} vs {right.label}",
            )
        return left, warnings

    if left_known and not right_known:
        warnings.append("unit_unverified")
        return left, warnings

    if not left_known and right_known:
        warnings.append("unit_unverified")
        return right, warnings

    # 两个都未知
    warnings.append("unit_unverified")
    return UnitTag.unknown(), warnings


# =============================================================================
# 乘除
# =============================================================================


def combine_multiplication(
    left: UnitTag,
    right: UnitTag,
) -> tuple[UnitTag, list[str]]:
    """乘法单位传播。

    规则：
    - 两个已知单位组合为 left*right；
    - 任一单位未知时结果单位未知并附 warning；
    - 任一无量纲时保留另一单位。
    """
    warnings: list[str] = []

    if left.is_unknown or right.is_unknown:
        warnings.append("unit_unverified")
        return UnitTag.unknown(), warnings

    if left.is_dimensionless and right.is_dimensionless:
        return UnitTag.dimensionless(), warnings

    if left.is_dimensionless:
        return right, warnings

    if right.is_dimensionless:
        return left, warnings

    # 两个都是 KNOWN
    combined = f"{left.label}*{right.label}"
    return UnitTag.known(combined), warnings


def combine_division(
    left: UnitTag,
    right: UnitTag,
) -> tuple[UnitTag, list[str]]:
    """除法单位传播。

    规则：
    - 两个已知单位组合为 left/right；
    - 相同已知单位相除标为无量纲；
    - 任一单位未知时结果单位未知并附 warning；
    - 无量纲除以已知单位 → 1/unit。
    """
    warnings: list[str] = []

    if left.is_unknown or right.is_unknown:
        warnings.append("unit_unverified")
        return UnitTag.unknown(), warnings

    if _labels_match(left, right):
        return UnitTag.dimensionless(), warnings

    if left.is_dimensionless and right.is_dimensionless:
        return UnitTag.dimensionless(), warnings

    if left.is_dimensionless:
        return UnitTag.known(f"1/{right.label}"), warnings

    if right.is_dimensionless:
        return left, warnings

    # 两个都是 KNOWN，不同
    combined = f"{left.label}/{right.label}"
    return UnitTag.known(combined), warnings


# =============================================================================
# 幂运算
# =============================================================================


def combine_power(
    base: UnitTag,
    exponent_tag: UnitTag,
    exponent_is_int_literal: bool,
    exponent_int_value: int | None,
) -> tuple[UnitTag, list[str]]:
    """幂运算单位传播。

    规则：
    - 指数必须无量纲；已知单位指数拒绝；
    - 指数未知时允许并附 warning；
    - 标量整数指数可生成 unit^n；
    - 其他指数返回未简化标签并附 warning。

    Args:
        base: 底数单位。
        exponent_tag: 指数单位。
        exponent_is_int_literal: 指数是否为整数字面量。
        exponent_int_value: 整数字面量值（非整数时为 None）。
    """
    warnings: list[str] = []

    # 指数必须无量纲
    if exponent_tag.is_known and not exponent_tag.is_dimensionless:
        raise NumericError(
            code="numeric_unit_conflict",
            message="exponent must be dimensionless",
        )
    if exponent_tag.is_unknown:
        warnings.append("unit_unverified")

    # 底数未知
    if base.is_unknown:
        warnings.append("unit_unverified")
        return UnitTag.unknown(), warnings

    # 底数无量纲
    if base.is_dimensionless:
        return UnitTag.dimensionless(), warnings

    # 底数 KNOWN
    if exponent_is_int_literal and exponent_int_value is not None:
        n = exponent_int_value
        if n == 0:
            return UnitTag.dimensionless(), warnings
        if n == 1:
            return base, warnings
        return UnitTag.known(f"{base.label}^{n}"), warnings

    # 非整数指数
    warnings.append("unit_unsimplified")
    return UnitTag.known(f"{base.label}^n"), warnings


# =============================================================================
# 一元与初等函数
# =============================================================================


def propagate_abs(base: UnitTag) -> UnitTag:
    """abs 保留单位。"""
    return base


def propagate_floor_ceil_round(base: UnitTag) -> UnitTag:
    """floor/ceil/round 保留单位。"""
    return base


def propagate_sqrt(base: UnitTag) -> tuple[UnitTag, list[str]]:
    """sqrt 的单位传播。

    已知单位标为 sqrt(unit)，不化简；未知单位附 warning。
    """
    warnings: list[str] = []
    if base.is_unknown:
        warnings.append("unit_unverified")
        return UnitTag.unknown(), warnings
    if base.is_dimensionless:
        return UnitTag.dimensionless(), warnings
    return UnitTag.known(f"sqrt({base.label})"), warnings


def require_dimensionless(
    base: UnitTag,
    func_name: str,
) -> list[str]:
    """要求输入无量纲的函数（log/log10/exp/trig）。

    有明确单位时拒绝；单位缺失时允许并附 warning。

    Args:
        base: 输入单位。
        func_name: 函数名（用于错误消息）。

    Returns:
        警告列表。

    Raises:
        NumericError: 输入有明确单位时。
    """
    if base.is_known and not base.is_dimensionless:
        raise NumericError(
            code="numeric_unit_conflict",
            message=f"{func_name} requires dimensionless input, got unit '{base.label}'",
        )
    if base.is_unknown:
        return ["unit_unverified"]
    return []


def propagate_inverse_trig(
    base: UnitTag,
    angle_unit: str,
) -> tuple[UnitTag, list[str]]:
    """反三角函数的单位传播。

    返回由 angle_unit 决定的 rad 或 deg。

    Args:
        base: 输入单位（必须无量纲或未知）。
        angle_unit: 角度单位（radian/degree）。
    """
    warnings = require_dimensionless(base, "inverse_trig")
    if angle_unit == "degree":
        return UnitTag.known("deg"), warnings
    return UnitTag.known("rad"), warnings


def propagate_atan2(
    y_tag: UnitTag,
    x_tag: UnitTag,
    angle_unit: str,
) -> tuple[UnitTag, list[str]]:
    """atan2 的单位传播。

    要求两个输入单位兼容；单位均未知时允许并附 warning。
    返回由 angle_unit 决定的 rad 或 deg。
    """
    warnings: list[str] = []

    # 两已知单位必须相同
    if y_tag.is_known and x_tag.is_known:
        if not _labels_match(y_tag, x_tag):
            raise NumericError(
                code="numeric_unit_conflict",
                message=f"atan2 requires compatible units: {y_tag.label} vs {x_tag.label}",
            )
    elif y_tag.is_unknown or x_tag.is_unknown:
        warnings.append("unit_unverified")

    if angle_unit == "degree":
        return UnitTag.known("deg"), warnings
    return UnitTag.known("rad"), warnings


# =============================================================================
# 聚合与统计函数
# =============================================================================


def propagate_aggregation(base: UnitTag) -> UnitTag:
    """sum/mean/min/max/median/quantile/count 保留单位。"""
    return base


def propagate_variance(base: UnitTag) -> UnitTag:
    """方差单位为 unit^2。"""
    if base.is_unknown:
        return UnitTag.unknown()
    if base.is_dimensionless:
        return UnitTag.dimensionless()
    return UnitTag.known(f"{base.label}^2")


def propagate_std(base: UnitTag) -> UnitTag:
    """标准差保留原单位。"""
    return base


# =============================================================================
# where / minimum / maximum / clip
# =============================================================================


def combine_where(
    a_tag: UnitTag,
    b_tag: UnitTag,
) -> tuple[UnitTag, list[str]]:
    """where 的数值分支单位传播（同加法规则）。"""
    return combine_additive(a_tag, b_tag, "where")


def combine_minimum_maximum(
    left: UnitTag,
    right: UnitTag,
    func_name: str,
) -> tuple[UnitTag, list[str]]:
    """minimum/maximum 的单位传播（同加法规则）。"""
    return combine_additive(left, right, func_name)


def check_clip_bounds(
    value_tag: UnitTag,
    low_tag: UnitTag,
    high_tag: UnitTag,
) -> tuple[UnitTag, list[str]]:
    """clip 的单位传播。

    边界为标量且单位必须与值兼容。
    """
    warnings: list[str] = []
    # 值与 low
    tag1, w1 = combine_additive(value_tag, low_tag, "clip")
    # 值与 high
    tag2, w2 = combine_additive(value_tag, high_tag, "clip")
    warnings.extend(w1)
    warnings.extend(w2)
    return value_tag, warnings


# =============================================================================
# 常量
# =============================================================================


def literal_unit() -> UnitTag:
    """数值字面量的单位（无量纲）。"""
    return UnitTag.dimensionless()


def constant_unit(name: str) -> UnitTag:
    """常量 pi/e 的单位（无量纲）。"""
    return UnitTag.dimensionless()
