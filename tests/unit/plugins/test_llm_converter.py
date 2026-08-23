"""单元测试：LLM 转换器插件（兜底）。

覆盖 ``packages/plugins/converters/llm_converter/converter.py``：
- LlmConverter.execute：完整流程（提取文本 → 调用 LLM → 返回结构化数据）
- 参数传递：prompt / file_engine / timeout / max_content_chars / image_dpi
- 默认 prompt 加载（从 prompt_store）
- 异常路径：提取失败、LLM 调用失败
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from packages.common.errors import AppError
from packages.plugins.converters.llm_converter.converter import LlmConverter, LlmConverterError

# ============================================================
# LlmConverter.execute
# ============================================================


class TestLlmConverterExecute:
    """LlmConverter 插件接口测试。"""

    async def test_execute_success(self, tmp_path: Path) -> None:
        """完整的 execute 流程：提取文本 → 调用 LLM → 返回结构化数据。"""
        f = tmp_path / "test.txt"
        f.write_text("sample content", encoding="utf-8")

        mock_result = {
            "metadata": {"key": "value"},
            "points": [{"name": "p1", "value": 1, "unit": "mm"}],
            "series": [{"name": "s1", "columns": ["x", "y"], "rows": [[1, 2]]}],
        }

        converter = LlmConverter()

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="sample content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "Extract data",
                    "ai_config": {
                        "base_url": "http://localhost:8000",
                        "api_key": "test-key",
                        "model_name": "test-model",
                    },
                }
            )

        assert result["metadata"] == {"key": "value"}
        assert len(result["points"]) == 1
        assert len(result["series"]) == 1

    async def test_execute_with_explicit_prompt(self, tmp_path: Path) -> None:
        """显式 prompt 被正确传递。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()
        captured_prompt: list[str] = []

        async def mock_call_llm(
            content: str,
            prompt: str,
            ai_config: dict | None,
            timeout: int = 300,  # noqa: ASYNC109
            max_chars: int = 999999999,
        ) -> dict:
            captured_prompt.append(prompt)
            return {"metadata": {}, "points": [], "series": []}

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                side_effect=mock_call_llm,
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "custom prompt",
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_prompt[0] == "custom prompt"

    async def test_execute_default_prompt_from_yaml(self, tmp_path: Path) -> None:
        """未提供 prompt 时从 YAML 加载默认提示词。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()
        captured_prompt: list[str] = []

        async def mock_call_llm(
            content: str,
            prompt: str,
            ai_config: dict | None,
            timeout: int = 300,  # noqa: ASYNC109
            max_chars: int = 999999999,
        ) -> dict:
            captured_prompt.append(prompt)
            return {"metadata": {}, "points": [], "series": []}

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch("packages.ai.prompt_store.get_prompt", return_value="default prompt"),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                side_effect=mock_call_llm,
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_prompt[0] == "default prompt"

    async def test_execute_with_custom_engine(self, tmp_path: Path) -> None:
        """file_engine 参数被传递给 extract_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("pdf content", encoding="utf-8")

        converter = LlmConverter()
        captured_engine: list[str] = []

        def mock_extract(file_path: Path, engine: str = "auto", image_dpi: int = 200) -> str:
            captured_engine.append(engine)
            return "extracted text"

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                side_effect=mock_extract,
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                new_callable=AsyncMock,
                return_value={"metadata": {}, "points": [], "series": []},
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "test",
                    "file_engine": "pymupdf",
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_engine[0] == "pymupdf"

    async def test_execute_with_custom_timeout(self, tmp_path: Path) -> None:
        """timeout 参数被传递给 call_llm_for_structured。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()
        captured_timeout: list[int] = []

        async def mock_call_llm(
            content: str,
            prompt: str,
            ai_config: dict | None,
            timeout: int = 300,  # noqa: ASYNC109
            max_chars: int = 999999999,
        ) -> dict:
            captured_timeout.append(timeout)
            return {"metadata": {}, "points": [], "series": []}

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                side_effect=mock_call_llm,
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "test",
                    "timeout": 120,
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_timeout[0] == 120

    async def test_execute_with_max_content_chars(self, tmp_path: Path) -> None:
        """max_content_chars 参数被传递。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()
        captured_max_chars: list[int] = []

        async def mock_call_llm(
            content: str,
            prompt: str,
            ai_config: dict | None,
            timeout: int = 300,  # noqa: ASYNC109
            max_chars: int = 999999999,
        ) -> dict:
            captured_max_chars.append(max_chars)
            return {"metadata": {}, "points": [], "series": []}

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                side_effect=mock_call_llm,
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "test",
                    "max_content_chars": 5000,
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_max_chars[0] == 5000

    async def test_execute_default_params(self, tmp_path: Path) -> None:
        """默认参数值正确。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()
        captured_args: dict = {}

        async def mock_call_llm(
            content: str,
            prompt: str,
            ai_config: dict | None,
            timeout: int = 300,  # noqa: ASYNC109
            max_chars: int = 999999999,
        ) -> dict:
            captured_args["timeout"] = timeout
            captured_args["max_chars"] = max_chars
            captured_args["ai_config"] = ai_config
            return {"metadata": {}, "points": [], "series": []}

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                side_effect=mock_call_llm,
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "test",
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_args["timeout"] == 300
        assert captured_args["max_chars"] == 999999999

    async def test_execute_image_dpi_passed(self, tmp_path: Path) -> None:
        """image_dpi 参数被传递给 extract_text。"""
        f = tmp_path / "test.pdf"
        f.write_text("pdf content", encoding="utf-8")

        converter = LlmConverter()
        captured_dpi: list[int] = []

        def mock_extract(file_path: Path, engine: str = "auto", image_dpi: int = 200) -> str:
            captured_dpi.append(image_dpi)
            return "extracted text"

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                side_effect=mock_extract,
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                new_callable=AsyncMock,
                return_value={"metadata": {}, "points": [], "series": []},
            ),
        ):
            await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "test",
                    "image_dpi": 300,
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert captured_dpi[0] == 300

    async def test_execute_empty_result(self, tmp_path: Path) -> None:
        """LLM 返回空结果时正常封装。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                new_callable=AsyncMock,
                return_value={"metadata": {}, "points": [], "series": []},
            ),
        ):
            result = await converter.execute(
                {
                    "file_path": str(f),
                    "prompt": "test",
                    "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                }
            )

        assert result["metadata"] == {}
        assert result["points"] == []
        assert result["series"] == []

    async def test_execute_llm_error_propagates(self, tmp_path: Path) -> None:
        """LLM 调用失败时异常正确传播。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                new_callable=AsyncMock,
                side_effect=AppError(
                    code="ai_request_failed",
                    message="LLM 请求失败",
                    retryable=True,
                ),
            ),
        ):
            with pytest.raises(AppError, match="LLM 请求失败"):
                await converter.execute(
                    {
                        "file_path": str(f),
                        "prompt": "test",
                        "ai_config": {"base_url": "x", "api_key": "y", "model_name": "z"},
                    }
                )

    async def test_execute_no_ai_config(self, tmp_path: Path) -> None:
        """无 ai_config 时 call_llm_for_structured 抛 AppError。"""
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        converter = LlmConverter()

        with (
            patch(
                "packages.plugins.converters.llm_converter.converter.extract_text",
                return_value="content",
            ),
            patch(
                "packages.plugins.converters.llm_converter.converter.call_llm_for_structured",
                new_callable=AsyncMock,
                side_effect=AppError(
                    code="ai_not_configured",
                    message="AI 大模型未配置",
                    retryable=False,
                ),
            ),
        ):
            with pytest.raises(AppError, match="未配置"):
                await converter.execute(
                    {
                        "file_path": str(f),
                        "prompt": "test",
                    }
                )


# ============================================================
# 异常体系
# ============================================================


class TestLlmExceptionHierarchy:
    """异常继承体系验证。"""

    def test_llm_converter_error_is_exception(self) -> None:
        assert issubclass(LlmConverterError, Exception)
