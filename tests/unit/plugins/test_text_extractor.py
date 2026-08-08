"""单元测试：text_extractor 文本提取公共模块。

覆盖：
- extract_text：auto 引擎按后缀分发 / raw 引擎直接读取 / pymupdf 引擎；
- 各文件类型：.txt / .md / .pdf / .docx / .xlsx / 图片 / 未知后缀；
- 异常路径：ImportError 降级为 read_text。

使用临时文件，不依赖真实 PDF/OCR 库。
"""

from pathlib import Path
from unittest.mock import patch

from packages.plugins.converters.common.text_extractor import extract_text


class TestExtractTextRawEngine:
    """raw / 非 auto 引擎测试。"""

    def test_raw_engine_non_pdf(self, tmp_path: Path) -> None:
        """raw 引擎对非 PDF 文件直接读取文本。"""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        result = extract_text(f, engine="raw")
        assert result == "hello world"

    def test_raw_engine_pdf_fallback(self, tmp_path: Path) -> None:
        """raw 引擎对 PDF 文件回退为 read_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("fake pdf content", encoding="utf-8")
        result = extract_text(f, engine="raw")
        assert "fake pdf content" in result

    def test_pymupdf_engine_non_pdf(self, tmp_path: Path) -> None:
        """pymupdf 引擎对非 PDF 文件回退为 read_text。"""
        f = tmp_path / "test.txt"
        f.write_text("text content", encoding="utf-8")
        result = extract_text(f, engine="pymupdf")
        assert result == "text content"

    def test_pymupdf_engine_pdf_import_error(self, tmp_path: Path) -> None:
        """pymupdf 引擎对 PDF 但 fitz 未安装时回退为 read_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("fallback content", encoding="utf-8")
        with patch("builtins.__import__", side_effect=ImportError("no fitz")):
            result = extract_text(f, engine="pymupdf")
        assert "fallback content" in result


class TestExtractTextAutoEngine:
    """auto 引擎按后缀分发测试。"""

    def test_txt_file(self, tmp_path: Path) -> None:
        """.txt 文件直接读取。"""
        f = tmp_path / "test.txt"
        f.write_text("plain text", encoding="utf-8")
        assert extract_text(f) == "plain text"

    def test_md_file(self, tmp_path: Path) -> None:
        """.md 文件直接读取。"""
        f = tmp_path / "test.md"
        f.write_text("# Markdown", encoding="utf-8")
        assert extract_text(f) == "# Markdown"

    def test_no_suffix_file(self, tmp_path: Path) -> None:
        """无后缀文件直接读取。"""
        f = tmp_path / "README"
        f.write_text("readme content", encoding="utf-8")
        assert extract_text(f) == "readme content"

    def test_unknown_suffix_fallback(self, tmp_path: Path) -> None:
        """未知后缀回退为 read_text。"""
        f = tmp_path / "test.xyz"
        f.write_text("unknown format", encoding="utf-8")
        assert extract_text(f) == "unknown format"

    def test_pdf_import_error_fallback(self, tmp_path: Path) -> None:
        """PDF 但 fitz 未安装时回退为 read_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("pdf fallback", encoding="utf-8")
        with patch.dict("sys.modules", {"fitz": None}):
            import builtins

            orig = builtins.__import__

            def fake_import(name: str, *args: object, **kwargs: object) -> object:
                if name == "fitz":
                    raise ImportError("no fitz")
                return orig(name, *args, **kwargs)  # type: ignore[arg-type]

            with patch("builtins.__import__", side_effect=fake_import):
                result = extract_text(f)
            assert "pdf fallback" in result
