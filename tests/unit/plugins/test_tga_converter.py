"""单元测试：TGA/STA 同步热分析文件解析器。

覆盖 ``packages/plugins/converters/tga_converter/converter.py``：
- _read_file：文件读取（UTF-16LE / UTF-8 BOM / GBK / latin-1 编码检测）
- _parse_header_line / _parse_data_line：行解析
- _convert_value：值类型转换
- _build_column_names：列名构建
- parse_tga / convert_tga_file_to_json：核心解析
- TgaConverter.execute：插件接口
- 异常路径：文件不存在、无标头、无数据
"""

import json
from pathlib import Path

import pytest

from packages.plugins.converters.tga_converter.converter import (
    FileReadError,
    InvalidFormatError,
    NoDataError,
    TgaConverter,
    TGAConverterError,
    _build_column_names,
    _convert_value,
    _parse_data_line,
    _parse_header_line,
    _read_file,
    convert_tga_file_to_json,
    convert_tga_file_to_json_string,
    parse_tga,
)

# ============================================================
# _parse_header_line
# ============================================================


class TestParseHeaderLine:
    """_parse_header_line 标头行解析测试。"""

    def test_chinese_colon(self) -> None:
        result = _parse_header_line("仪器：NETZSCH STA 449 F3")
        assert result == ("仪器", "NETZSCH STA 449 F3")

    def test_english_colon(self) -> None:
        result = _parse_header_line("Operator: John Doe")
        assert result == ("Operator", "John Doe")

    def test_colon_with_spaces(self) -> None:
        result = _parse_header_line("日期 / 时间： 2024-01-01 12:00:00")
        assert result is not None
        assert result[0] == "日期 / 时间"

    def test_empty_value(self) -> None:
        result = _parse_header_line("备注:")
        assert result == ("备注", "")

    def test_no_colon_returns_none(self) -> None:
        assert _parse_header_line("10.0 20.0 30.0") is None

    def test_empty_line_returns_none(self) -> None:
        assert _parse_header_line("") is None

    def test_empty_key_returns_none(self) -> None:
        result = _parse_header_line(": value")
        assert result is None


# ============================================================
# _parse_data_line
# ============================================================


class TestParseDataLine:
    """_parse_data_line 数据行解析测试。"""

    def test_six_columns(self) -> None:
        result = _parse_data_line("30.0 5.0 1.5 99.5 0.2 0.1")
        assert result == [30.0, 5.0, 1.5, 99.5, 0.2, 0.1]

    def test_negative_values(self) -> None:
        result = _parse_data_line("-10.0 -5.0 -1.5 -99.5 -0.2 -0.1")
        assert result == [-10.0, -5.0, -1.5, -99.5, -0.2, -0.1]

    def test_scientific_notation(self) -> None:
        result = _parse_data_line("1.0e2 2.0E-3 3.5e+1 4.0E0 5.0 6.0")
        assert result == [100.0, 0.002, 35.0, 4.0, 5.0, 6.0]

    def test_integer_values(self) -> None:
        result = _parse_data_line("30 5 1 99 0 0")
        assert result == [30.0, 5.0, 1.0, 99.0, 0.0, 0.0]

    def test_empty_line_returns_none(self) -> None:
        assert _parse_data_line("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _parse_data_line("   ") is None

    def test_non_data_line_returns_none(self) -> None:
        assert _parse_data_line("温度 时间 DSC") is None

    def test_single_value_returns_none(self) -> None:
        assert _parse_data_line("30.0") is None


# ============================================================
# _convert_value
# ============================================================


class TestConvertValueTga:
    """_convert_value 值类型转换测试。"""

    def test_empty_returns_none(self) -> None:
        assert _convert_value("") is None

    def test_double_dash_returns_none(self) -> None:
        assert _convert_value("--") is None

    def test_impossible_returns_none(self) -> None:
        assert _convert_value("不可能") is None

    def test_integer_string(self) -> None:
        assert _convert_value("42") == 42
        assert isinstance(_convert_value("42"), int)

    def test_float_string(self) -> None:
        assert _convert_value("3.14") == 3.14
        assert isinstance(_convert_value("3.14"), float)

    def test_scientific_notation_float(self) -> None:
        assert _convert_value("1.5e2") == 150.0

    def test_plain_string(self) -> None:
        assert _convert_value("hello") == "hello"

    def test_negative_int(self) -> None:
        assert _convert_value("-10") == -10

    def test_negative_float(self) -> None:
        assert _convert_value("-0.5") == -0.5


# ============================================================
# _build_column_names
# ============================================================


class TestBuildColumnNames:
    """_build_column_names 列名构建测试。"""

    def test_six_columns_with_units(self) -> None:
        col_line = "温度     时间    DSC       TG     DTG   灵敏度"
        unit_line = "℃      min   mW/mg     %      %/min μV/mW"
        result = _build_column_names(col_line, unit_line)
        assert result == [
            "温度 (℃)",
            "时间 (min)",
            "DSC (mW/mg)",
            "TG (%)",
            "DTG (%/min)",
            "灵敏度 (μV/mW)",
        ]

    def test_missing_unit(self) -> None:
        col_line = "A B C"
        unit_line = "x y"
        result = _build_column_names(col_line, unit_line)
        assert result == ["A (x)", "B (y)", "C"]

    def test_more_units_than_columns(self) -> None:
        col_line = "A B"
        unit_line = "x y z"
        result = _build_column_names(col_line, unit_line)
        assert result == ["A (x)", "B (y)"]


# ============================================================
# _read_file
# ============================================================


class TestReadFileTga:
    """_read_file 文件读取测试。"""

    def test_read_utf8_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        assert _read_file(str(f)) == "hello world"

    def test_read_utf8_bom_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello bom", encoding="utf-8-sig")
        result = _read_file(str(f))
        assert "hello bom" in result

    def test_read_utf16le_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_bytes(b"\xff\xfe" + "hello utf16".encode("utf-16-le"))
        result = _read_file(str(f))
        assert "hello utf16" in result

    def test_read_gbk_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("你好世界", encoding="gbk")
        result = _read_file(str(f))
        assert "你好" in result

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileReadError):
            _read_file("/nonexistent/file.txt")


# ============================================================
# parse_tga / convert_tga_file_to_json
# ============================================================


def _make_valid_tga_file(tmp_path: Path, encoding: str = "utf-8") -> Path:
    """创建一个有效的 TGA 测试文件。"""
    content = (
        "仪器：NETZSCH STA 449 F3\n"
        "项目：测试项目\n"
        "文件名：test.txt\n"
        "日期 / 时间：2024-01-01 12:00:00\n"
        "样品名称：Sample-1\n"
        "样品质量：10.5 mg\n"
        "气氛：N2\n"
        "范围：30-1000 ℃\n"
        "----------\n"
        "温度     时间    DSC       TG     DTG   灵敏度\n"
        "℃      min   mW/mg     %      %/min μV/mW\n"
        "----------\n"
        "30.0 0.0 0.0 100.0 0.0 0.1\n"
        "100.0 5.0 1.5 99.5 0.2 0.1\n"
        "200.0 10.0 3.0 98.0 0.5 0.2\n"
    )
    f = tmp_path / "PrnDat_test.txt"
    if encoding == "utf-16-le":
        f.write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))
    else:
        f.write_text(content, encoding=encoding)
    return f


class TestParseTga:
    """parse_tga 核心解析测试。"""

    def test_parse_valid_file(self, tmp_path: Path) -> None:
        f = _make_valid_tga_file(tmp_path)
        result = parse_tga(str(f))
        assert "metadata" in result
        assert "points" in result
        assert "series" in result
        assert result["metadata"]["filename"] == "PrnDat_test.txt"
        assert result["metadata"]["instrument"] == "NETZSCH STA 449 F3"
        assert result["metadata"]["sample_name"] == "Sample-1"
        assert result["metadata"]["data_points"] == 3
        assert len(result["series"]) == 1
        assert result["series"][0]["name"] == "热分析曲线"
        assert len(result["series"][0]["rows"]) == 3

    def test_parse_utf16le_file(self, tmp_path: Path) -> None:
        f = _make_valid_tga_file(tmp_path, encoding="utf-16-le")
        result = parse_tga(str(f))
        assert result["metadata"]["sample_name"] == "Sample-1"
        assert len(result["series"][0]["rows"]) == 3

    def test_no_header_raises(self, tmp_path: Path) -> None:
        """无标头信息时抛 InvalidFormatError。"""
        content = "10.0 20.0 30.0\n"
        f = tmp_path / "noheader.txt"
        f.write_text(content, encoding="utf-8")
        with pytest.raises(InvalidFormatError):
            parse_tga(str(f))

    def test_no_data_raises(self, tmp_path: Path) -> None:
        """有标头但无数据行时抛 NoDataError。"""
        content = "仪器：NETZSCH\n----------\n温度 时间\n℃ min\n----------\n"
        f = tmp_path / "nodata.txt"
        f.write_text(content, encoding="utf-8")
        with pytest.raises(NoDataError):
            parse_tga(str(f))

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileReadError):
            parse_tga("/nonexistent/file.txt")

    def test_fallback_columns(self, tmp_path: Path) -> None:
        """无列名行时使用默认列名。"""
        content = "仪器：NETZSCH\n----------\n30.0 0.0 0.0 100.0 0.0 0.1\n"
        f = tmp_path / "nocols.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_tga(str(f))
        assert len(result["series"][0]["columns"]) == 6
        assert "温度 (℃)" in result["series"][0]["columns"]

    def test_data_truncated_to_column_count(self, tmp_path: Path) -> None:
        """数据行超过列数时截断。"""
        content = (
            "仪器：NETZSCH\n"
            "----------\n"
            "温度 时间\n"
            "℃ min\n"
            "----------\n"
            "30.0 0.0 0.0 100.0 0.0 0.1 99.9 88.8\n"
        )
        f = tmp_path / "extra.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_tga(str(f))
        assert len(result["series"][0]["rows"][0]) == 2


class TestConvertTgaFileToJson:
    """convert_tga_file_to_json 入口函数测试。"""

    def test_convert_returns_dict(self, tmp_path: Path) -> None:
        f = _make_valid_tga_file(tmp_path)
        result = convert_tga_file_to_json(str(f))
        assert isinstance(result, dict)
        assert "metadata" in result
        assert "points" in result
        assert "series" in result

    def test_convert_json_string(self, tmp_path: Path) -> None:
        f = _make_valid_tga_file(tmp_path)
        result_str = convert_tga_file_to_json_string(str(f))
        parsed = json.loads(result_str)
        assert "metadata" in parsed


# ============================================================
# TgaConverter.execute
# ============================================================


class TestTgaConverterExecute:
    """TgaConverter 插件接口测试。"""

    async def test_execute_valid(self, tmp_path: Path) -> None:
        f = _make_valid_tga_file(tmp_path)
        converter = TgaConverter()
        result = await converter.execute({"file_path": str(f)})
        assert "metadata" in result
        assert "points" in result
        assert "series" in result
        assert result["metadata"]["filename"] == "PrnDat_test.txt"

    async def test_execute_file_not_found(self) -> None:
        converter = TgaConverter()
        with pytest.raises((FileReadError, TGAConverterError)):
            await converter.execute({"file_path": "/nonexistent/file.txt"})


# ============================================================
# 异常体系
# ============================================================


class TestTgaExceptionHierarchy:
    """异常继承体系验证。"""

    def test_all_inherit_from_tga_converter_error(self) -> None:
        assert issubclass(FileReadError, TGAConverterError)
        assert issubclass(NoDataError, TGAConverterError)
        assert issubclass(InvalidFormatError, TGAConverterError)

    def test_tga_converter_error_is_exception(self) -> None:
        assert issubclass(TGAConverterError, Exception)
