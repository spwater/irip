"""单位转换单元测试（纯函数，无需数据库）。

验证（IRIP Task 10）：
- mm→um: 0.0186 mm == 18.6000 um；
- um→mm 往返保持精度；
- °C→K: 0 °C == 273.15 K；
- K→°C 往返保持精度；
- 不兼容维度（mm → °C）抛出 AppError(incompatible_dimensions)；
- 未知单位抛出 AppError(unknown_unit)；
- 无量纲恒等变换。
"""

from decimal import Decimal

import pytest

from packages.common.errors import AppError
from packages.standards.units import UnitConverter


class TestUnitConverter:
    """单位转换器单元测试。"""

    def test_mm_to_um(self) -> None:
        """mm → um：0.0186 mm == 18.6000 um。"""
        result = UnitConverter.convert(Decimal("0.0186"), "mm", "um")
        assert result == Decimal("18.6000")

    def test_um_to_mm_roundtrip(self) -> None:
        """um → mm 往返保持精度。"""
        original = Decimal("0.0186")
        to_um = UnitConverter.convert(original, "mm", "um")
        back = UnitConverter.convert(to_um, "um", "mm")
        assert back == original

    def test_celsius_to_kelvin(self) -> None:
        """°C → K：0 °C == 273.15 K。"""
        result = UnitConverter.convert(Decimal("0"), "°C", "K")
        assert result == Decimal("273.15")

    def test_kelvin_to_celsius_roundtrip(self) -> None:
        """K → °C 往返保持精度。"""
        original = Decimal("25.5")
        to_k = UnitConverter.convert(original, "°C", "K")
        back = UnitConverter.convert(to_k, "K", "°C")
        assert back == original

    def test_celsius_to_fahrenheit(self) -> None:
        """°C → °F：0 °C == 32 °F。"""
        result = UnitConverter.convert(Decimal("0"), "°C", "°F")
        assert result == Decimal("32")

    def test_fahrenheit_to_celsius(self) -> None:
        """°F → °C：212 °F == 100 °C。"""
        result = UnitConverter.convert(Decimal("212"), "°F", "°C")
        assert result == Decimal("100")

    def test_incompatible_dimensions(self) -> None:
        """不兼容维度：mm → °C 抛出 AppError(incompatible_dimensions)。"""
        with pytest.raises(AppError) as exc_info:
            UnitConverter.convert(Decimal("1"), "mm", "°C")
        assert exc_info.value.code == "incompatible_dimensions"

    def test_incompatible_dimensions_reverse(self) -> None:
        """不兼容维度：°C → mm 抛出 AppError(incompatible_dimensions)。"""
        with pytest.raises(AppError) as exc_info:
            UnitConverter.convert(Decimal("273.15"), "°C", "mm")
        assert exc_info.value.code == "incompatible_dimensions"

    def test_unknown_source_unit(self) -> None:
        """未知源单位抛出 AppError(unknown_unit)。"""
        with pytest.raises(AppError) as exc_info:
            UnitConverter.convert(Decimal("1"), "foobar", "mm")
        assert exc_info.value.code == "unknown_unit"

    def test_unknown_target_unit(self) -> None:
        """未知目标单位抛出 AppError(unknown_unit)。"""
        with pytest.raises(AppError) as exc_info:
            UnitConverter.convert(Decimal("1"), "mm", "foobar")
        assert exc_info.value.code == "unknown_unit"

    def test_dimensionless_identity(self) -> None:
        """无量纲恒等变换：1 → 1。"""
        result = UnitConverter.convert(
            Decimal("1"), "dimensionless", "dimensionless"
        )
        assert result == Decimal("1")

    def test_dimensionless_preserves_value(self) -> None:
        """无量纲恒等变换：任意值保持不变。"""
        value = Decimal("42.75")
        result = UnitConverter.convert(
            value, "dimensionless", "dimensionless"
        )
        assert result == value

    def test_same_unit_identity(self) -> None:
        """同单位转换：值不变。"""
        result = UnitConverter.convert(Decimal("5.5"), "mm", "mm")
        assert result == Decimal("5.5")

    def test_mm_to_cm(self) -> None:
        """mm → cm：10 mm == 1 cm。"""
        result = UnitConverter.convert(Decimal("10"), "mm", "cm")
        assert result == Decimal("1")

    def test_g_to_kg(self) -> None:
        """g → kg：1000 g == 1 kg。"""
        result = UnitConverter.convert(Decimal("1000"), "g", "kg")
        assert result == Decimal("1")

    def test_deg_to_rad(self) -> None:
        """deg → rad：0 deg == 0 rad。"""
        result = UnitConverter.convert(Decimal("0"), "deg", "rad")
        assert result == Decimal("0")

    def test_deg_to_rad_180(self) -> None:
        """deg → rad：180 deg ≈ π rad。"""
        result = UnitConverter.convert(Decimal("180"), "deg", "rad")
        # π ≈ 3.141592653589793238462643383279502884
        assert abs(result - Decimal("3.141592653589793")) < Decimal("0.0001")

    def test_get_dimension(self) -> None:
        """get_dimension 返回正确维度。"""
        assert UnitConverter.get_dimension("mm") == "length"
        assert UnitConverter.get_dimension("°C") == "temperature"
        assert UnitConverter.get_dimension("kg") == "mass"
        assert UnitConverter.get_dimension("dimensionless") == "dimensionless"
        assert UnitConverter.get_dimension("unknown") is None

    def test_is_compatible(self) -> None:
        """is_compatible 判断维度兼容性。"""
        assert UnitConverter.is_compatible("mm", "um") is True
        assert UnitConverter.is_compatible("°C", "K") is True
        assert UnitConverter.is_compatible("mm", "°C") is False
        assert UnitConverter.is_compatible("mm", "unknown") is False

    def test_large_value_precision(self) -> None:
        """大数值精度保持：1 km → mm = 1000000 mm。"""
        result = UnitConverter.convert(Decimal("1"), "km", "mm")
        assert result == Decimal("1000000")

    def test_small_value_precision(self) -> None:
        """小数值精度保持：1 nm → mm = 0.000001 mm。"""
        result = UnitConverter.convert(Decimal("1"), "nm", "mm")
        assert result == Decimal("0.000001")
