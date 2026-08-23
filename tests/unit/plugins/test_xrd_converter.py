"""单元测试：XRD RAS/RAW 文件解析器。

覆盖 ``packages/plugins/converters/xrd_converter/converter.py``：
- convert_value：类型转换
- _parse_star_line / _parse_hash_line / _parse_xrd_data_line：行解析
- _extract_index / _strip_index / _get_group_prefix：编号字段处理
- _lookup_unit / _collect_unit_source_fields：单位查找
- _build_meas_cond_axis_series / _build_other_numbered_series / _build_xrd_series：序列构建
- validate_result：完整性校验
- parse_ras_raw / convert_xrd_file_to_json：核心解析
- XrdConverter.execute：插件接口
- 异常路径：文件不存在、不支持格式、无数据
"""

import json
from pathlib import Path

import pytest

from packages.plugins.converters.xrd_converter.converter import (
    FileReadError,
    InvalidRasRawStructureError,
    UnsupportedFileFormatError,
    XrdConverter,
    XRDConverterError,
    XrdDataParseError,
    _build_meas_cond_axis_series,
    _build_other_numbered_series,
    _build_xrd_series,
    _check_no_nan_inf,
    _collect_unit_source_fields,
    _extract_index,
    _get_group_prefix,
    _lookup_unit,
    _parse_hash_line,
    _parse_star_line,
    _parse_xrd_data_line,
    _strip_index,
    convert_value,
    convert_xrd_file_to_json,
    convert_xrd_file_to_json_string,
    parse_ras_raw,
    validate_result,
)

# ============================================================
# convert_value
# ============================================================


class TestConvertValue:
    """convert_value 类型转换测试。"""

    def test_empty_string_returns_none(self) -> None:
        assert convert_value("") is None

    def test_integer_string(self) -> None:
        assert convert_value("42") == 42
        assert isinstance(convert_value("42"), int)

    def test_negative_integer(self) -> None:
        assert convert_value("-10") == -10

    def test_float_string(self) -> None:
        assert convert_value("3.14") == 3.14
        assert isinstance(convert_value("3.14"), float)

    def test_negative_float(self) -> None:
        assert convert_value("-0.5") == -0.5

    def test_plain_string(self) -> None:
        assert convert_value("hello") == "hello"

    def test_string_with_letters(self) -> None:
        assert convert_value("SmartLabXE") == "SmartLabXE"

    def test_string_with_special_chars(self) -> None:
        assert convert_value("RAS_RAW") == "RAS_RAW"


# ============================================================
# _parse_star_line / _parse_hash_line / _parse_xrd_data_line
# ============================================================


class TestParseStarLine:
    """_parse_star_line 行解析测试。"""

    def test_valid_star_line(self) -> None:
        result = _parse_star_line('*FILE_TYPE "RAS_RAW"')
        assert result == ("FILE_TYPE", "RAS_RAW")

    def test_star_line_with_spaces_in_value(self) -> None:
        result = _parse_star_line('*NAME "My Sample Name"')
        assert result == ("NAME", "My Sample Name")

    def test_non_star_line_returns_none(self) -> None:
        assert _parse_star_line("#KEY=VALUE") is None

    def test_empty_line_returns_none(self) -> None:
        assert _parse_star_line("") is None

    def test_data_line_returns_none(self) -> None:
        assert _parse_star_line("10.0 100.0") is None


class TestParseHashLine:
    """_parse_hash_line 行解析测试。"""

    def test_valid_hash_line(self) -> None:
        result = _parse_hash_line("#Intensity_unit=count")
        assert result == ("Intensity_unit", "count")

    def test_hash_line_with_spaces_in_value(self) -> None:
        result = _parse_hash_line("#Key=value with spaces")
        assert result == ("Key", "value with spaces")

    def test_non_hash_line_returns_none(self) -> None:
        assert _parse_hash_line('*FILE_TYPE "RAS_RAW"') is None

    def test_data_line_returns_none(self) -> None:
        assert _parse_hash_line("10.0 100.0") is None

    def test_empty_line_returns_none(self) -> None:
        assert _parse_hash_line("") is None


class TestParseXrdDataLine:
    """_parse_xrd_data_line 行解析测试。"""

    def test_valid_data_line(self) -> None:
        result = _parse_xrd_data_line("10.0 100.0")
        assert result == (10.0, 100.0)

    def test_integer_data_line(self) -> None:
        result = _parse_xrd_data_line("10 100")
        assert result == (10.0, 100.0)

    def test_decimal_values(self) -> None:
        result = _parse_xrd_data_line("5.5 20.5")
        assert result == (5.5, 20.5)

    def test_non_data_line_returns_none(self) -> None:
        assert _parse_xrd_data_line("*KEY VALUE") is None

    def test_single_number_returns_none(self) -> None:
        assert _parse_xrd_data_line("10.0") is None

    def test_three_columns_returns_none(self) -> None:
        assert _parse_xrd_data_line("10.0 100.0 200.0") is None

    def test_empty_line_returns_none(self) -> None:
        assert _parse_xrd_data_line("") is None


# ============================================================
# _extract_index / _strip_index / _get_group_prefix
# ============================================================


class TestExtractIndex:
    """_extract_index 编号提取测试。"""

    def test_extract_index_zero(self) -> None:
        assert _extract_index("KEY-0") == 0

    def test_extract_index_positive(self) -> None:
        assert _extract_index("KEY-5") == 5

    def test_no_index_returns_none(self) -> None:
        assert _extract_index("KEY") is None

    def test_no_index_with_underscore(self) -> None:
        assert _extract_index("KEY_VALUE") is None


class TestStripIndex:
    """_strip_index 去除编号后缀测试。"""

    def test_strip_index_zero(self) -> None:
        assert _strip_index("KEY-0") == "KEY"

    def test_strip_index_positive(self) -> None:
        assert _strip_index("KEY-10") == "KEY"

    def test_no_index_unchanged(self) -> None:
        assert _strip_index("KEY") == "KEY"

    def test_no_index_with_underscore_unchanged(self) -> None:
        assert _strip_index("KEY_VALUE") == "KEY_VALUE"


class TestGetGroupPrefix:
    """_get_group_prefix 分组前缀测试。"""

    def test_simple_prefix(self) -> None:
        assert _get_group_prefix("MEAS_COND_AXIS_NAME") == "MEAS_COND_AXIS"

    def test_no_underscore(self) -> None:
        assert _get_group_prefix("KEY") == "KEY"

    def test_single_underscore_at_start(self) -> None:
        assert _get_group_prefix("_VALUE") == "_VALUE"

    def test_multiple_underscores(self) -> None:
        assert _get_group_prefix("A_B_C_D") == "A_B_C"


# ============================================================
# _lookup_unit / _collect_unit_source_fields
# ============================================================


class TestLookupUnit:
    """_lookup_unit 单位查找测试。"""

    def test_lookup_via_unit_mapping(self) -> None:
        star_params = {"MEAS_SCAN_SPEED": "10", "MEAS_SCAN_SPEED_UNIT": "deg/min"}
        value, source = _lookup_unit("MEAS_SCAN_SPEED", star_params)
        assert value == "deg/min"
        assert source == "MEAS_SCAN_SPEED_UNIT"

    def test_lookup_via_suffix_unit(self) -> None:
        star_params = {"SOME_FIELD": "42", "SOME_FIELD_UNIT": "kV"}
        value, source = _lookup_unit("SOME_FIELD", star_params)
        assert value == "kV"
        assert source == "SOME_FIELD_UNIT"

    def test_no_unit_found(self) -> None:
        star_params = {"SOME_FIELD": "42"}
        value, source = _lookup_unit("SOME_FIELD", star_params)
        assert value == ""
        assert source is None

    def test_unit_mapping_source_missing(self) -> None:
        """UNIT_MAPPING 中有映射但 source_field 不在 star_params 时回退。"""
        star_params = {"MEAS_SCAN_SPEED": "10"}
        value, source = _lookup_unit("MEAS_SCAN_SPEED", star_params)
        assert value == ""
        assert source is None


class TestCollectUnitSourceFields:
    """_collect_unit_source_fields 收集单位来源字段测试。"""

    def test_collect_from_unit_mapping(self) -> None:
        star_params = {"MEAS_SCAN_SPEED_UNIT": "deg/min", "OTHER": "x"}
        result = _collect_unit_source_fields(star_params)
        assert "MEAS_SCAN_SPEED_UNIT" in result

    def test_collect_from_suffix(self) -> None:
        star_params = {"SOME_FIELD": "42", "SOME_FIELD_UNIT": "kV"}
        result = _collect_unit_source_fields(star_params)
        assert "SOME_FIELD_UNIT" in result

    def test_no_unit_sources(self) -> None:
        star_params = {"KEY1": "val1", "KEY2": "val2"}
        result = _collect_unit_source_fields(star_params)
        assert len(result) == 0


# ============================================================
# _build_meas_cond_axis_series / _build_other_numbered_series / _build_xrd_series
# ============================================================


class TestBuildMeasCondAxisSeries:
    """_build_meas_cond_axis_series 序列构建测试。"""

    def test_build_with_data(self) -> None:
        numbered_fields = {
            "MEAS_COND_AXIS_NAME": {0: "X", 1: "Y"},
            "MEAS_COND_AXIS_POSITION": {0: "10.0", 1: "20.0"},
        }
        result = _build_meas_cond_axis_series(numbered_fields)
        assert result is not None
        assert result["name"] == "MEAS_COND_AXIS"
        assert "index" in result["columns"]
        assert "NAME" in result["columns"]
        assert "POSITION" in result["columns"]
        assert len(result["rows"]) == 2
        assert result["rows"][0][0] == 0
        assert result["rows"][0][1] == "X"

    def test_build_empty_returns_none(self) -> None:
        result = _build_meas_cond_axis_series({})
        assert result is None

    def test_build_with_partial_data(self) -> None:
        """部分子字段缺失时填 None。"""
        numbered_fields = {
            "MEAS_COND_AXIS_NAME": {0: "X"},
        }
        result = _build_meas_cond_axis_series(numbered_fields)
        assert result is not None
        assert len(result["rows"]) == 1
        # NAME 有值，其他子字段为 None
        name_idx = result["columns"].index("NAME")
        speed_idx = result["columns"].index("SPEED")
        assert result["rows"][0][name_idx] == "X"
        assert result["rows"][0][speed_idx] is None


class TestBuildOtherNumberedSeries:
    """_build_other_numbered_series 序列构建测试。"""

    def test_single_field(self) -> None:
        numbered_fields = {"SOME_FIELD": {0: "a", 1: "b"}}
        result = _build_other_numbered_series(numbered_fields)
        assert len(result) == 1
        assert result[0]["name"] == "SOME_FIELD"
        assert result[0]["columns"] == ["index", "value"]
        assert len(result[0]["rows"]) == 2

    def test_multiple_fields_same_prefix(self) -> None:
        numbered_fields = {
            "GROUP_A": {0: "1", 1: "2"},
            "GROUP_B": {0: "10", 1: "20"},
        }
        result = _build_other_numbered_series(numbered_fields)
        assert len(result) == 1
        assert "index" in result[0]["columns"]
        assert "GROUP_A" in result[0]["columns"]
        assert "GROUP_B" in result[0]["columns"]

    def test_empty_returns_empty_list(self) -> None:
        result = _build_other_numbered_series({})
        assert result == []

    def test_excludes_meas_cond_axis(self) -> None:
        """MEAS_COND_AXIS 字段被排除。"""
        numbered_fields = {
            "MEAS_COND_AXIS_NAME": {0: "X"},
            "OTHER_FIELD": {0: "val"},
        }
        result = _build_other_numbered_series(numbered_fields)
        assert len(result) == 1
        assert result[0]["name"] == "OTHER_FIELD"


class TestBuildXrdSeries:
    """_build_xrd_series 序列构建测试。"""

    def test_build_with_units(self) -> None:
        xrd_data = [(10.0, 100.0), (20.0, 200.0)]
        star_params = {"MEAS_SCAN_AXIS_X": "2Theta", "MEAS_SCAN_UNIT_X": "deg"}
        hash_params = {"Intensity_unit": "counts"}
        result = _build_xrd_series(xrd_data, star_params, hash_params)
        assert result["name"] == "XRD衍射谱"
        assert "2Theta (deg)" in result["columns"]
        assert "Intensity (counts)" in result["columns"]
        assert len(result["rows"]) == 2

    def test_build_without_units(self) -> None:
        xrd_data = [(5.0, 50.0)]
        result = _build_xrd_series(xrd_data, {}, {})
        assert result["name"] == "XRD衍射谱"
        assert result["columns"][0] == ""
        assert result["columns"][1] == "Intensity"

    def test_build_empty_data(self) -> None:
        result = _build_xrd_series([], {}, {})
        assert result["rows"] == []


# ============================================================
# _check_no_nan_inf
# ============================================================


class TestCheckNoNanInf:
    """_check_no_nan_inf NaN/Inf 检测测试。"""

    def test_normal_float(self) -> None:
        assert _check_no_nan_inf(1.0) is False

    def test_nan(self) -> None:
        assert _check_no_nan_inf(float("nan")) is True

    def test_inf(self) -> None:
        assert _check_no_nan_inf(float("inf")) is True

    def test_negative_inf(self) -> None:
        assert _check_no_nan_inf(float("-inf")) is True

    def test_int(self) -> None:
        assert _check_no_nan_inf(42) is False

    def test_string(self) -> None:
        assert _check_no_nan_inf("hello") is False

    def test_dict_with_nan(self) -> None:
        assert _check_no_nan_inf({"a": float("nan")}) is True

    def test_dict_without_nan(self) -> None:
        assert _check_no_nan_inf({"a": 1.0, "b": 2}) is False

    def test_list_with_nan(self) -> None:
        assert _check_no_nan_inf([1.0, float("nan"), 3]) is True

    def test_list_without_nan(self) -> None:
        assert _check_no_nan_inf([1.0, 2.0, 3]) is False

    def test_nested_dict_with_nan(self) -> None:
        assert _check_no_nan_inf({"a": {"b": [float("inf")]}}) is True


# ============================================================
# validate_result
# ============================================================


class TestValidateResult:
    """validate_result 完整性校验测试。"""

    def test_valid_empty_data(self) -> None:
        """空 xrd_data 直接返回（不校验）。"""
        validate_result(
            metadata={},
            points=[],
            series=[],
            params={"xrd_data": []},
        )

    def test_count_mismatch_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """数据点数量不一致时记录 warning。"""
        with caplog.at_level("WARNING"):
            validate_result(
                metadata={},
                points=[],
                series=[],
                params={
                    "MEAS_DATA_COUNT": 10,
                    "xrd_data": [(1.0, 1.0), (2.0, 2.0)],
                },
            )
        assert any("数据点数量不一致" in r.message for r in caplog.records)

    def test_scan_start_mismatch_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            validate_result(
                metadata={},
                points=[],
                series=[],
                params={
                    "MEAS_SCAN_START": 0.0,
                    "xrd_data": [(10.0, 100.0), (20.0, 200.0)],
                },
            )
        assert any("首条 X 值" in r.message for r in caplog.records)

    def test_scan_stop_mismatch_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            validate_result(
                metadata={},
                points=[],
                series=[],
                params={
                    "MEAS_SCAN_STOP": 0.0,
                    "xrd_data": [(10.0, 100.0), (20.0, 200.0)],
                },
            )
        assert any("末条 X 值" in r.message for r in caplog.records)

    def test_non_monotonic_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            validate_result(
                metadata={},
                points=[],
                series=[],
                params={
                    "xrd_data": [(20.0, 100.0), (10.0, 200.0)],
                },
            )
        assert any("非单调递增" in r.message for r in caplog.records)

    def test_step_mismatch_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            validate_result(
                metadata={},
                points=[],
                series=[],
                params={
                    "MEAS_SCAN_STEP": 1.0,
                    "MEAS_SCAN_UNEQUALY_SPACED": "False",
                    "xrd_data": [(10.0, 100.0), (20.0, 200.0)],
                },
            )
        assert any("步长不一致" in r.message for r in caplog.records)

    def test_nan_in_result_raises(self) -> None:
        with pytest.raises(InvalidRasRawStructureError, match="NaN"):
            validate_result(
                metadata={"a": float("nan")},
                points=[],
                series=[],
                params={"xrd_data": [(1.0, 1.0)]},
            )


# ============================================================
# parse_ras_raw / convert_xrd_file_to_json
# ============================================================


def _make_valid_ras_raw(tmp_path: Path) -> Path:
    """创建一个有效的 RAS_RAW 测试文件。"""
    content = (
        '*FILE_TYPE "RAS_RAW"\n'
        '*FILE_SYSTEM_NAME "SmartLabXE"\n'
        '*FILE_VERSION "1.0"\n'
        '*MEAS_SCAN_AXIS_X "2Theta"\n'
        '*MEAS_SCAN_UNIT_X "deg"\n'
        '*MEAS_SCAN_START "10.0"\n'
        '*MEAS_SCAN_STOP "12.0"\n'
        '*MEAS_SCAN_STEP "1.0"\n'
        '*MEAS_DATA_COUNT "3"\n'
        '*MEAS_SCAN_UNEQUALY_SPACED "False"\n'
        '*MEAS_SCAN_SPEED "5.0"\n'
        '*MEAS_SCAN_SPEED_UNIT "deg/min"\n'
        "#Intensity_unit=count\n"
        "10.0 100.0\n"
        "11.0 200.0\n"
        "12.0 300.0\n"
    )
    f = tmp_path / "test.txt"
    f.write_text(content, encoding="utf-8")
    return f


class TestParseRasRaw:
    """parse_ras_raw 核心解析测试。"""

    def test_parse_valid_file(self, tmp_path: Path) -> None:
        f = _make_valid_ras_raw(tmp_path)
        result = parse_ras_raw(str(f))
        assert "metadata" in result
        assert "points" in result
        assert "series" in result
        assert result["metadata"]["filename"] == "test.txt"
        assert result["metadata"]["FILE_TYPE"] == "RAS_RAW"
        # points 应包含 star 和 hash 参数
        point_names = [p["name"] for p in result["points"]]
        assert "MEAS_SCAN_AXIS_X" in point_names
        assert "Intensity_unit" in point_names

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        content = '*FILE_TYPE "OTHER"\n*FILE_SYSTEM_NAME "OtherSystem"\n'
        f = tmp_path / "bad.txt"
        f.write_text(content, encoding="utf-8")
        with pytest.raises(UnsupportedFileFormatError):
            parse_ras_raw(str(f))

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileReadError):
            parse_ras_raw("/nonexistent/file.txt")

    def test_no_data_lines(self, tmp_path: Path) -> None:
        """无 XRD 数据行但格式正确。"""
        content = '*FILE_TYPE "RAS_RAW"\n*FILE_SYSTEM_NAME "SmartLabXE"\n'
        f = tmp_path / "nodata.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_ras_raw(str(f))
        # 空数据不抛异常
        assert "series" in result

    def test_with_numbered_fields(self, tmp_path: Path) -> None:
        """带编号字段的文件解析。"""
        content = (
            '*FILE_TYPE "RAS_RAW"\n'
            '*FILE_SYSTEM_NAME "SmartLabXE"\n'
            '*MEAS_COND_AXIS_NAME-0 "X"\n'
            '*MEAS_COND_AXIS_NAME-1 "Y"\n'
            '*MEAS_COND_AXIS_POSITION-0 "10.0"\n'
            '*MEAS_COND_AXIS_POSITION-1 "20.0"\n'
            "10.0 100.0\n"
        )
        f = tmp_path / "numbered.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_ras_raw(str(f))
        # 应有 MEAS_COND_AXIS 序列
        series_names = [s["name"] for s in result["series"]]
        assert "MEAS_COND_AXIS" in series_names


class TestConvertXrdFileToJson:
    """convert_xrd_file_to_json 入口函数测试。"""

    def test_convert_returns_dict(self, tmp_path: Path) -> None:
        f = _make_valid_ras_raw(tmp_path)
        result = convert_xrd_file_to_json(str(f))
        assert isinstance(result, dict)
        assert "metadata" in result
        assert "points" in result
        assert "series" in result

    def test_convert_json_string(self, tmp_path: Path) -> None:
        f = _make_valid_ras_raw(tmp_path)
        result_str = convert_xrd_file_to_json_string(str(f))
        parsed = json.loads(result_str)
        assert "metadata" in parsed


# ============================================================
# XrdConverter.execute
# ============================================================


class TestXrdConverterExecute:
    """XrdConverter 插件接口测试。"""

    async def test_execute_valid(self, tmp_path: Path) -> None:
        f = _make_valid_ras_raw(tmp_path)
        converter = XrdConverter()
        result = await converter.execute({"file_path": str(f)})
        assert "metadata" in result
        assert "points" in result
        assert "series" in result
        assert result["metadata"]["filename"] == "test.txt"

    async def test_execute_file_not_found(self) -> None:
        converter = XrdConverter()
        with pytest.raises((FileReadError, XRDConverterError)):
            await converter.execute({"file_path": "/nonexistent/file.txt"})


# ============================================================
# 异常体系
# ============================================================


class TestExceptionHierarchy:
    """异常继承体系验证。"""

    def test_all_inherit_from_xrd_converter_error(self) -> None:
        assert issubclass(UnsupportedFileFormatError, XRDConverterError)
        assert issubclass(FileReadError, XRDConverterError)
        assert issubclass(InvalidRasRawStructureError, XRDConverterError)
        assert issubclass(XrdDataParseError, XRDConverterError)
