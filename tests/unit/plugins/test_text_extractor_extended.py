"""单元测试：text_extractor 文本提取公共模块（扩展）。

补充覆盖 ``packages/plugins/converters/common/text_extractor.py``：
- _extract_pdf：PDF 提取（文字层足够 / 不足触发 OCR）
- _extract_image_with_ocr：图片 OCR（PaddleOCR 未安装 / 识别失败）
- _extract_docx：Word 文档提取（ImportError 降级）
- _extract_xlsx：Excel 文件提取（ImportError 降级）
- _get_ocr_engine：OCR 引擎单例
- _extract_pdf_with_ocr：PDF OCR 提取
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import packages.plugins.converters.common.text_extractor as text_extractor_module
from packages.common.errors import AppError
from packages.plugins.converters.common.text_extractor import (
    _extract_docx,
    _extract_image_with_ocr,
    _extract_pdf,
    _extract_pdf_with_ocr,
    _extract_xlsx,
    _get_ocr_engine,
    extract_text,
)

# ============================================================
# _get_ocr_engine
# ============================================================


class TestGetOcrEngine:
    """_get_ocr_engine 单例测试。"""

    def test_returns_none_when_paddleocr_not_installed(self) -> None:
        """PaddleOCR 未安装时返回 None。"""
        # 重置全局单例
        original = text_extractor_module._ocr_engine
        text_extractor_module._ocr_engine = None
        try:
            with patch.dict("sys.modules", {"paddleocr": None}):
                import builtins

                orig = builtins.__import__

                def fake_import(name: str, *args: object, **kwargs: object) -> object:
                    if name == "paddleocr":
                        raise ImportError("no paddleocr")
                    return orig(name, *args, **kwargs)  # type: ignore[arg-type]

                with patch("builtins.__import__", side_effect=fake_import):
                    result = _get_ocr_engine()
                assert result is None
        finally:
            text_extractor_module._ocr_engine = original

    def test_returns_cached_instance(self) -> None:
        """已设置的单例被直接返回。"""
        mock_engine = MagicMock()
        text_extractor_module._ocr_engine = mock_engine
        try:
            result = _get_ocr_engine()
            assert result is mock_engine
        finally:
            text_extractor_module._ocr_engine = None


# ============================================================
# _extract_image_with_ocr
# ============================================================


class TestExtractImageWithOcr:
    """_extract_image_with_ocr 图片 OCR 测试。"""

    def test_returns_empty_when_no_engine(self, tmp_path: Path) -> None:
        """OCR 引擎未安装时返回空字符串。"""
        f = tmp_path / "test.png"
        f.write_text("fake image", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._get_ocr_engine",
            return_value=None,
        ):
            result = _extract_image_with_ocr(f)
        assert result == ""

    def test_returns_text_when_engine_available(self, tmp_path: Path) -> None:
        """OCR 引擎可用时返回提取的文本。"""
        f = tmp_path / "test.png"
        f.write_text("fake image", encoding="utf-8")

        mock_engine = MagicMock()
        mock_engine.predict.return_value = [{"rec_texts": ["line1", "line2"]}]

        with patch(
            "packages.plugins.converters.common.text_extractor._get_ocr_engine",
            return_value=mock_engine,
        ):
            result = _extract_image_with_ocr(f)
        assert "line1" in result
        assert "line2" in result

    def test_returns_empty_on_ocr_exception(self, tmp_path: Path) -> None:
        """OCR 识别异常时返回空字符串。"""
        f = tmp_path / "test.png"
        f.write_text("fake image", encoding="utf-8")

        mock_engine = MagicMock()
        mock_engine.predict.side_effect = RuntimeError("OCR error")

        with patch(
            "packages.plugins.converters.common.text_extractor._get_ocr_engine",
            return_value=mock_engine,
        ):
            result = _extract_image_with_ocr(f)
        assert result == ""

    def test_handles_empty_result(self, tmp_path: Path) -> None:
        """OCR 返回空结果时返回空字符串。"""
        f = tmp_path / "test.png"
        f.write_text("fake image", encoding="utf-8")

        mock_engine = MagicMock()
        mock_engine.predict.return_value = []

        with patch(
            "packages.plugins.converters.common.text_extractor._get_ocr_engine",
            return_value=mock_engine,
        ):
            result = _extract_image_with_ocr(f)
        assert result == ""

    def test_handles_result_without_rec_texts(self, tmp_path: Path) -> None:
        """OCR 返回结果不含 rec_texts 字段时返回空字符串。"""
        f = tmp_path / "test.png"
        f.write_text("fake image", encoding="utf-8")

        mock_engine = MagicMock()
        mock_engine.predict.return_value = [{"other_field": "val"}]

        with patch(
            "packages.plugins.converters.common.text_extractor._get_ocr_engine",
            return_value=mock_engine,
        ):
            result = _extract_image_with_ocr(f)
        assert result == ""

    def test_handles_non_dict_page(self, tmp_path: Path) -> None:
        """OCR 返回结果含非 dict 元素时跳过。"""
        f = tmp_path / "test.png"
        f.write_text("fake image", encoding="utf-8")

        mock_engine = MagicMock()
        mock_engine.predict.return_value = ["not_a_dict", {"rec_texts": ["ok"]}]

        with patch(
            "packages.plugins.converters.common.text_extractor._get_ocr_engine",
            return_value=mock_engine,
        ):
            result = _extract_image_with_ocr(f)
        assert "ok" in result


# ============================================================
# _extract_pdf
# ============================================================


class TestExtractPdf:
    """_extract_pdf PDF 提取测试。"""

    def test_pdf_import_error_fallback(self, tmp_path: Path) -> None:
        """fitz 未安装时回退为 read_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("pdf text content", encoding="utf-8")

        with patch("builtins.__import__", side_effect=ImportError("no fitz")):
            result = _extract_pdf(f, 200)
        assert "pdf text content" in result

    def test_pdf_with_sufficient_text(self, tmp_path: Path) -> None:
        """PDF 文字层足够时直接返回。"""
        f = tmp_path / "test.pdf"
        f.write_text("dummy", encoding="utf-8")

        mock_page = MagicMock()
        mock_page.get_text.return_value = "A" * 100
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.close = MagicMock()

        with patch("fitz.open", return_value=mock_doc):
            result = _extract_pdf(f, 200)
        assert len(result) >= 100

    def test_pdf_low_text_triggers_ocr(self, tmp_path: Path) -> None:
        """PDF 文字层不足时触发 OCR。"""
        f = tmp_path / "test.pdf"
        f.write_text("dummy", encoding="utf-8")

        mock_page = MagicMock()
        mock_page.get_text.return_value = "short"
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.close = MagicMock()

        with (
            patch("fitz.open", return_value=mock_doc),
            patch(
                "packages.plugins.converters.common.text_extractor._extract_pdf_with_ocr",
                return_value="ocr text",
            ) as mock_ocr,
        ):
            result = _extract_pdf(f, 200)
        assert result == "ocr text"
        mock_ocr.assert_called_once()


# ============================================================
# _extract_pdf_with_ocr
# ============================================================


class TestExtractPdfWithOcr:
    """_extract_pdf_with_ocr PDF OCR 测试。"""

    def test_import_error_fallback(self, tmp_path: Path) -> None:
        """fitz 未安装时回退为 read_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("pdf content", encoding="utf-8")

        with patch("builtins.__import__", side_effect=ImportError("no fitz")):
            result = _extract_pdf_with_ocr(f, 200)
        assert "pdf content" in result

    def test_ocr_extraction_success(self, tmp_path: Path) -> None:
        """成功渲染页面并 OCR 提取。"""
        f = tmp_path / "test.pdf"
        f.write_text("dummy", encoding="utf-8")

        mock_pixmap = MagicMock()
        mock_pixmap.tobytes.return_value = b"fake_png_bytes"
        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pixmap
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.close = MagicMock()

        with (
            patch("fitz.open", return_value=mock_doc),
            patch(
                "packages.plugins.converters.common.text_extractor._extract_image_with_ocr",
                return_value="ocr result",
            ),
        ):
            result = _extract_pdf_with_ocr(f, 200)
        assert "ocr result" in result

    def test_ocr_empty_pages(self, tmp_path: Path) -> None:
        """所有页面 OCR 结果为空时返回空字符串。"""
        f = tmp_path / "test.pdf"
        f.write_text("dummy", encoding="utf-8")

        mock_pixmap = MagicMock()
        mock_pixmap.tobytes.return_value = b"fake_png_bytes"
        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pixmap
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.close = MagicMock()

        with (
            patch("fitz.open", return_value=mock_doc),
            patch(
                "packages.plugins.converters.common.text_extractor._extract_image_with_ocr",
                return_value="",
            ),
        ):
            result = _extract_pdf_with_ocr(f, 200)
        assert result == ""


# ============================================================
# _extract_docx
# ============================================================


class TestExtractDocx:
    """_extract_docx Word 文档提取测试。"""

    def test_import_error_raises_app_error(self, tmp_path: Path) -> None:
        """python-docx 未安装时抛 AppError。"""
        f = tmp_path / "test.docx"
        f.write_text("dummy", encoding="utf-8")

        with patch("builtins.__import__", side_effect=ImportError("no docx")):
            with pytest.raises(AppError, match="python-docx"):
                _extract_docx(f)

    def test_extract_paragraphs_and_tables(self, tmp_path: Path) -> None:
        """提取段落和表格文本。"""
        f = tmp_path / "test.docx"
        f.write_text("dummy", encoding="utf-8")

        mock_para1 = MagicMock()
        mock_para1.text = "First paragraph"
        mock_para2 = MagicMock()
        mock_para2.text = ""  # 空段落应被跳过
        mock_para3 = MagicMock()
        mock_para3.text = "Third paragraph"

        mock_cell1 = MagicMock()
        mock_cell1.text = "Cell1"
        mock_cell2 = MagicMock()
        mock_cell2.text = "Cell2"
        mock_row = MagicMock()
        mock_row.cells = [mock_cell1, mock_cell2]
        mock_table = MagicMock()
        mock_table.rows = [mock_row]

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2, mock_para3]
        mock_doc.tables = [mock_table]

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict("sys.modules", {"docx": mock_docx_module}):
            result = _extract_docx(f)
        assert "First paragraph" in result
        assert "Third paragraph" in result
        assert "Cell1\tCell2" in result


# ============================================================
# _extract_xlsx
# ============================================================


class TestExtractXlsx:
    """_extract_xlsx Excel 文件提取测试。"""

    def test_import_error_raises_app_error(self, tmp_path: Path) -> None:
        """openpyxl 未安装时抛 AppError。"""
        f = tmp_path / "test.xlsx"
        f.write_text("dummy", encoding="utf-8")

        with patch("builtins.__import__", side_effect=ImportError("no openpyxl")):
            with pytest.raises(AppError, match="openpyxl"):
                _extract_xlsx(f)

    def test_extract_cells(self, tmp_path: Path) -> None:
        """提取单元格文本。"""
        f = tmp_path / "test.xlsx"
        f.write_text("dummy", encoding="utf-8")

        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            ("A1", "B1", "C1"),
            ("A2", None, "C2"),
        ]
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_ws]
        mock_wb.close = MagicMock()

        mock_openpyxl_module = MagicMock()
        mock_openpyxl_module.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl_module}):
            result = _extract_xlsx(f)
        assert "A1\tB1\tC1" in result
        assert "A2\t\tC2" in result


# ============================================================
# extract_text auto engine dispatch
# ============================================================


class TestExtractTextAutoDispatch:
    """auto 引擎按后缀分发测试。"""

    def test_docx_dispatch(self, tmp_path: Path) -> None:
        """.docx 文件分发到 _extract_docx。"""
        f = tmp_path / "test.docx"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_docx",
            return_value="docx text",
        ) as mock_func:
            result = extract_text(f)
        assert result == "docx text"
        mock_func.assert_called_once_with(f)

    def test_xlsx_dispatch(self, tmp_path: Path) -> None:
        """.xlsx 文件分发到 _extract_xlsx。"""
        f = tmp_path / "test.xlsx"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_xlsx",
            return_value="xlsx text",
        ) as mock_func:
            result = extract_text(f)
        assert result == "xlsx text"
        mock_func.assert_called_once_with(f)

    def test_image_dispatch(self, tmp_path: Path) -> None:
        """.jpg 文件分发到 _extract_image_with_ocr。"""
        f = tmp_path / "test.jpg"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_image_with_ocr",
            return_value="image text",
        ) as mock_func:
            result = extract_text(f)
        assert result == "image text"
        mock_func.assert_called_once_with(f)

    def test_png_dispatch(self, tmp_path: Path) -> None:
        """.png 文件分发到 _extract_image_with_ocr。"""
        f = tmp_path / "test.png"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_image_with_ocr",
            return_value="png text",
        ):
            result = extract_text(f)
        assert result == "png text"

    def test_jpeg_dispatch(self, tmp_path: Path) -> None:
        """.jpeg 文件分发到 _extract_image_with_ocr。"""
        f = tmp_path / "test.jpeg"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_image_with_ocr",
            return_value="jpeg text",
        ):
            result = extract_text(f)
        assert result == "jpeg text"

    def test_xls_dispatch(self, tmp_path: Path) -> None:
        """.xls 文件分发到 _extract_xlsx。"""
        f = tmp_path / "test.xls"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_xlsx",
            return_value="xls text",
        ):
            result = extract_text(f)
        assert result == "xls text"

    def test_pdf_dispatch(self, tmp_path: Path) -> None:
        """.pdf 文件分发到 _extract_pdf。"""
        f = tmp_path / "test.pdf"
        f.write_text("dummy", encoding="utf-8")

        with patch(
            "packages.plugins.converters.common.text_extractor._extract_pdf",
            return_value="pdf text",
        ) as mock_func:
            result = extract_text(f)
        assert result == "pdf text"
        mock_func.assert_called_once_with(f, 200)
