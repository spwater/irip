"""基于 Decimal 的单位转换器。

核心设计：
- 维度注册表：每个单位代码映射到 (dimension, offset, scale) 三元组；
- 仿射变换：``base = (value + offset) * scale`` 将源单位转换为该维度的基准单位；
- 逆向变换：``target = base / target_scale - target_offset`` 将基准单位转为目标单位；
- 全程使用 ``Decimal`` 算术，不引入 float 精度损失；
- 跨维度转换（如 mm → °C）抛出 ``AppError(code="incompatible_dimensions")``；
- 未知单位抛出 ``AppError(code="unknown_unit")``。

支持的维度与单位（V1）：
- length（基准 m）：m, cm, mm, um, µm, nm, km
- temperature（基准 K）：K, °C, °F
- mass（基准 kg）：kg, g, mg, t
- angle（基准 rad）：rad, deg
- time（基准 s）：s, min, h
- area（基准 m²）：m2, cm2, mm2
- volume（基准 m³）：m3, L, mL
- dimensionless：dimensionless（恒等变换）
"""

from dataclasses import dataclass
from decimal import Decimal

from packages.common.errors import AppError


@dataclass(frozen=True)
class _UnitDef:
    """单位定义：维度 + 仿射变换参数。

    转换到基准单位：``base = (value + offset) * scale``。
    从基准单位转换：``target = base / scale - offset``。

    Attributes:
        dimension: 维度种类（如 "length"、"temperature"）。
        offset: 仿射变换偏移量（加法分量）。
        scale: 仿射变换比例因子（乘法分量）。
    """

    dimension: str
    offset: Decimal
    scale: Decimal


#: π / 180 的高精度近似值，用于 deg → rad 转换。
_PI_OVER_180: Decimal = Decimal("0.017453292519943295769236907684886")

#: 180 / π 的高精度近似值，用于 rad → deg 转换。
_180_OVER_PI: Decimal = Decimal("57.2957795130823208767981548141052")


#: 单位注册表：单位代码 → _UnitDef。
_UNIT_REGISTRY: dict[str, _UnitDef] = {
    # ---- length（基准：m）----
    "m": _UnitDef("length", Decimal("0"), Decimal("1")),
    "cm": _UnitDef("length", Decimal("0"), Decimal("0.01")),
    "mm": _UnitDef("length", Decimal("0"), Decimal("0.001")),
    "um": _UnitDef("length", Decimal("0"), Decimal("0.000001")),
    "µm": _UnitDef("length", Decimal("0"), Decimal("0.000001")),
    "nm": _UnitDef("length", Decimal("0"), Decimal("0.000000001")),
    "km": _UnitDef("length", Decimal("0"), Decimal("1000")),
    # ---- temperature（基准：K）----
    "K": _UnitDef("temperature", Decimal("0"), Decimal("1")),
    "°C": _UnitDef("temperature", Decimal("273.15"), Decimal("1")),
    "°F": _UnitDef("temperature", Decimal("459.67"), Decimal("5") / Decimal("9")),
    # ---- mass（基准：kg）----
    "kg": _UnitDef("mass", Decimal("0"), Decimal("1")),
    "g": _UnitDef("mass", Decimal("0"), Decimal("0.001")),
    "mg": _UnitDef("mass", Decimal("0"), Decimal("0.000001")),
    "t": _UnitDef("mass", Decimal("0"), Decimal("1000")),
    # ---- angle（基准：rad）----
    "rad": _UnitDef("angle", Decimal("0"), Decimal("1")),
    "deg": _UnitDef("angle", Decimal("0"), _PI_OVER_180),
    # ---- time（基准：s）----
    "s": _UnitDef("time", Decimal("0"), Decimal("1")),
    "min": _UnitDef("time", Decimal("0"), Decimal("60")),
    "h": _UnitDef("time", Decimal("0"), Decimal("3600")),
    # ---- area（基准：m²）----
    "m2": _UnitDef("area", Decimal("0"), Decimal("1")),
    "cm2": _UnitDef("area", Decimal("0"), Decimal("0.0001")),
    "mm2": _UnitDef("area", Decimal("0"), Decimal("0.000001")),
    # ---- volume（基准：m³）----
    "m3": _UnitDef("volume", Decimal("0"), Decimal("1")),
    "L": _UnitDef("volume", Decimal("0"), Decimal("0.001")),
    "mL": _UnitDef("volume", Decimal("0"), Decimal("0.000001")),
    # ---- dimensionless ----
    "dimensionless": _UnitDef("dimensionless", Decimal("0"), Decimal("1")),
}


class UnitConverter:
    """单位转换器：基于 Decimal 仿射变换，支持维度检查。

    用法::

        >>> from decimal import Decimal
        >>> UnitConverter.convert(Decimal("0.0186"), "mm", "um")
        Decimal('18.6')
        >>> UnitConverter.convert(Decimal("0"), "°C", "K")
        Decimal('273.15')
    """

    @staticmethod
    def convert(value: Decimal, source: str, target: str) -> Decimal:
        """将数值从源单位转换到目标单位。

        转换流程：
        1. 查找源单位与目标单位的定义（未知则抛 ``unknown_unit``）；
        2. 校验维度一致（不一致则抛 ``incompatible_dimensions``）；
        3. 源 → 基准：``base = (value + source.offset) * source.scale``；
        4. 基准 → 目标：``target = base / target.scale - target.offset``。

        Args:
            value: 待转换的数值（Decimal）。
            source: 源单位代码（如 ``"mm"``）。
            target: 目标单位代码（如 ``"um"``）。

        Returns:
            Decimal: 转换后的数值。

        Raises:
            AppError: code="unknown_unit"，当单位代码不在注册表中时。
            AppError: code="incompatible_dimensions"，当源与目标维度不同时。
        """
        source_def = _UNIT_REGISTRY.get(source)
        target_def = _UNIT_REGISTRY.get(target)

        if source_def is None:
            raise AppError(
                code="unknown_unit",
                message=f"未知单位代码：{source}",
                retryable=False,
                fields={"unit": source},
            )
        if target_def is None:
            raise AppError(
                code="unknown_unit",
                message=f"未知单位代码：{target}",
                retryable=False,
                fields={"unit": target},
            )

        if source_def.dimension != target_def.dimension:
            raise AppError(
                code="incompatible_dimensions",
                message=(
                    f"单位「{source}」（维度 {source_def.dimension}）"
                    f"与「{target}」（维度 {target_def.dimension}）"
                    f"维度不兼容，无法转换"
                ),
                retryable=False,
                fields={"source": source, "target": target},
            )

        # 源 → 基准单位
        base_value: Decimal = (value + source_def.offset) * source_def.scale

        # 基准 → 目标单位
        result: Decimal = base_value / target_def.scale - target_def.offset

        return result

    @staticmethod
    def get_dimension(unit: str) -> str | None:
        """查询单位所属的维度种类。

        Args:
            unit: 单位代码。

        Returns:
            str | None: 维度名称（如 ``"length"``）；未知单位返回 None。
        """
        unit_def = _UNIT_REGISTRY.get(unit)
        if unit_def is None:
            return None
        return unit_def.dimension

    @staticmethod
    def is_compatible(source: str, target: str) -> bool:
        """判断两个单位是否属于同一维度（可互相转换）。

        Args:
            source: 源单位代码。
            target: 目标单位代码。

        Returns:
            bool: 同维度且均存在返回 True。
        """
        source_def = _UNIT_REGISTRY.get(source)
        target_def = _UNIT_REGISTRY.get(target)
        if source_def is None or target_def is None:
            return False
        return source_def.dimension == target_def.dimension
