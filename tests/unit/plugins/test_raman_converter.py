"""单元测试：Raman 拉曼光谱文件解析器。

覆盖 ``packages/plugins/converters/raman_converter/converter.py``：
- _read_file：文件读取（UTF-8 / GBK / latin-1 编码检测）
- _parse_data_line：两列数值行解析
- parse_raman / convert_raman_file_to_json：核心解析
- RamanConverter.execute：插件接口
- 异常路径：文件不存在、无数据
"""

import json
from pathlib import Path

import pytest

from packages.plugins.converters.raman_converter.converter import (
    FileReadError,
    NoDataError,
    RamanConverter,
    RamanConverterError,
    _parse_data_line,
    _read_file,
    convert_raman_file_to_json,
    convert_raman_file_to_json_string,
    parse_raman,
)

# ============================================================
# _parse_data_line
# ============================================================


class TestParseDataLineRaman:
    """_parse_data_line 两列数据行解析测试。"""

    def test_two_floats(self) -> None:
        result = _parse_data_line("100.0 500.0")
        assert result == (100.0, 500.0)

    def test_two_integers(self) -> None:
        result = _parse_data_line("100 500")
        assert result == (100.0, 500.0)

    def test_negative_values(self) -> None:
        result = _parse_data_line("-10.0 -50.0")
        assert result == (-10.0, -50.0)

    def test_tab_separated(self) -> None:
        result = _parse_data_line("100.0\t500.0")
        assert result == (100.0, 500.0)

    def test_scientific_notation(self) -> None:
        result = _parse_data_line("1.0e2 5.0E2")
        assert result == (100.0, 500.0)

    def test_empty_line_returns_none(self) -> None:
        assert _parse_data_line("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _parse_data_line("   ") is None

    def test_single_value_returns_none(self) -> None:
        assert _parse_data_line("100.0") is None

    def test_three_values_returns_none(self) -> None:
        assert _parse_data_line("100.0 500.0 300.0") is None

    def test_text_line_returns_none(self) -> None:
        assert _parse_data_line("Raman Shift Intensity") is None

    def test_comment_line_returns_none(self) -> None:
        assert _parse_data_line("# This is a comment") is None


# ============================================================
# _read_file
# ============================================================


class TestReadFileRaman:
    """_read_file 文件读取测试。"""

    def test_read_utf8_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello raman", encoding="utf-8")
        assert _read_file(str(f)) == "hello raman"

    def test_read_utf8_bom_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello bom", encoding="utf-8-sig")
        result = _read_file(str(f))
        assert "hello bom" in result

    def test_read_gbk_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("你好拉曼", encoding="gbk")
        result = _read_file(str(f))
        assert "你好" in result

    def test_read_latin1_file(self, tmp_path: Path) -> None:
        """latin-1 编码文件（可以解码任意字节）。"""
        f = tmp_path / "test.txt"
        f.write_text("café", encoding="latin-1")
        result = _read_file(str(f))
        assert "caf" in result

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileReadError):
            _read_file("/nonexistent/file.txt")


# ============================================================
# parse_raman / convert_raman_file_to_json
# ============================================================


def _make_valid_raman_file(tmp_path: Path) -> Path:
    """创建一个有效的 Raman 测试文件。"""
    content = (
        "# Raman Spectrum\n"
        "Raman Shift\tIntensity\n"
        "100.0 500.0\n"
        "200.0 300.0\n"
        "300.0 100.0\n"
        "\n"
        "# End of data\n"
    )
    f = tmp_path / "BL-1.txt"
    f.write_text(content, encoding="utf-8")
    return f


class TestParseRaman:
    """parse_raman 核心解析测试。"""

    def test_parse_valid_file(self, tmp_path: Path) -> None:
        f = _make_valid_raman_file(tmp_path)
        result = parse_raman(str(f))
        assert "metadata" in result
        assert "points" in result
        assert "series" in result
        assert result["metadata"]["filename"] == "BL-1.txt"
        assert result["metadata"]["data_points"] == 3
        assert result["metadata"]["x_min"] == 100.0
        assert result["metadata"]["x_max"] == 300.0
        assert result["metadata"]["x_unit"] == "cm⁻¹"
        assert len(result["series"]) == 1
        assert result["series"][0]["name"] == "拉曼光谱"
        assert len(result["series"][0]["rows"]) == 3
        assert result["series"][0]["rows"][0] == [100.0, 500.0]

    def test_parse_skips_non_data_lines(self, tmp_path: Path) -> None:
        """非数据行被跳过。"""
        content = "header line\n100.0 500.0\nanother comment\n200.0 300.0\n"
        f = tmp_path / "test.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_raman(str(f))
        assert result["metadata"]["data_points"] == 2

    def test_parse_no_data_raises(self, tmp_path: Path) -> None:
        """无有效数据行时抛 NoDataError。"""
        content = "only comments\nmore comments\n"
        f = tmp_path / "nodata.txt"
        f.write_text(content, encoding="utf-8")
        with pytest.raises(NoDataError):
            parse_raman(str(f))

    def test_parse_empty_file_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(NoDataError):
            parse_raman(str(f))

    def test_parse_file_not_found_raises(self) -> None:
        with pytest.raises(FileReadError):
            parse_raman("/nonexistent/file.txt")

    def test_parse_single_data_line(self, tmp_path: Path) -> None:
        content = "50.0 100.0\n"
        f = tmp_path / "single.txt"
        f.write_text(content, encoding="utf-8")
        result = parse_raman(str(f))
        assert result["metadata"]["data_points"] == 1
        assert result["metadata"]["x_min"] == 50.0
        assert result["metadata"]["x_max"] == 50.0


class TestConvertRamanFileToJson:
    """convert_raman_file_to_json 入口函数测试。"""

    def test_convert_returns_dict(self, tmp_path: Path) -> None:
        f = _make_valid_raman_file(tmp_path)
        result = convert_raman_file_to_json(str(f))
        assert isinstance(result, dict)
        assert "metadata" in result
        assert "points" in result
        assert "series" in result

    def test_convert_json_string(self, tmp_path: Path) -> None:
        f = _make_valid_raman_file(tmp_path)
        result_str = convert_raman_file_to_json_string(str(f))
        parsed = json.loads(result_str)
        assert "metadata" in parsed


# ============================================================
# RamanConverter.execute
# ============================================================


class TestRamanConverterExecute:
    """RamanConverter 插件接口测试。"""

    async def test_execute_valid(self, tmp_path: Path) -> None:
        f = _make_valid_raman_file(tmp_path)
        converter = RamanConverter()
        result = await converter.execute({"file_path": str(f)})
        assert "metadata" in result
        assert "points" in result
        assert "series" in result
        assert result["metadata"]["filename"] == "BL-1.txt"
        assert result["metadata"]["data_points"] == 3

    async def test_execute_file_not_found(self) -> None:
        converter = RamanConverter()
        with pytest.raises((FileReadError, RamanConverterError)):
            await converter.execute({"file_path": "/nonexistent/file.txt"})


# ============================================================
# 异常体系
# ============================================================


class TestRamanExceptionHierarchy:
    """异常继承体系验证。"""

    def test_all_inherit_from_raman_converter_error(self) -> None:
        assert issubclass(FileReadError, RamanConverterError)
        assert issubclass(NoDataError, RamanConverterError)

    def test_raman_converter_error_is_exception(self) -> None:
        assert issubclass(RamanConverterError, Exception)
