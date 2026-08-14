"""大模型解析器插件（兜底）。

从文件中提取文本，调用大模型转为结构化数据 {metadata, points, series}。
支持 PDF、图片、Word、Excel、纯文本等多种文件格式。

重构说明（v2.0）：
- ``_extract_text`` 及子函数迁移到 ``common.text_extractor``
- ``_call_llm`` / ``_parse_llm_json`` 迁移到 ``common.llm_utils``
- 使用公共 ``call_llm_for_structured`` 统一 LLM 调用逻辑
- 保留 ``LlmConverterError`` 异常定义

与其他 converter 的区别：
- 其他 converter 有专项提取器（openpyxl / python-docx / PaddleOCR / pymupdf）
- llm_converter 使用通用 ``text_extractor``（支持所有格式，但粒度粗）
- 所有 converter 共享同一套 LLM 分类逻辑（``common.llm_utils``）
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from packages.plugins.converters.common.llm_utils import call_llm_for_structured
from packages.plugins.converters.common.text_extractor import extract_text
from packages.plugins.protocol import ConverterResult

logger = logging.getLogger(__name__)


# ============================================================
# 异常定义
# ============================================================


class LlmConverterError(Exception):
    """大模型转换器基础异常类。"""


# ============================================================
# 插件类
# ============================================================


class LlmConverter:
    """大模型解析器。

    从文件提取文本 → 调用 LLM → 返回结构化数据。
    作为其他确定性插件的兜底方案（fallback）。
    """

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """提取文件文本 → 调用大模型 → 返回结构化数据。

        Args:
            params: 参数字典，包含:
                - file_path: 文件路径（必填）
                - prompt: LLM 提示词（必填）
                - file_engine: 文件读取方式（auto/pymupdf/raw），默认 auto
                - ai_config: AI 配置字典（base_url/api_key/model_name）
                - timeout: 超时秒数，默认 300
                - max_content_chars: 最大内容字符数，默认 999999999
                - image_dpi: PDF 转图片 DPI，默认 200

        Returns:
            dict: ``{"metadata": {...}, "points": [...], "series": [...]}``
        """
        file_path = Path(params["file_path"])
        prompt: str = params.get("prompt", "")
        # 组件未配置 prompt 时，从 config/prompts.yaml 加载默认提示词
        if not prompt:
            from packages.ai.prompt_store import get_prompt

            prompt = get_prompt("converter_default_prompt.system_prompt")
            logger.info("组件未配置 prompt，使用 YAML 默认提示词")
        engine: str = params.get("file_engine", "auto")
        image_dpi: int = params.get("image_dpi", 200)
        timeout: int = params.get("timeout", 300)
        max_chars: int = params.get("max_content_chars", 999999999)
        ai_config: dict[str, Any] | None = params.get("ai_config")

        # 1. 提取文件文本（从公共模块导入）
        content = await asyncio.to_thread(extract_text, file_path, engine, image_dpi)

        # 2. 调用 LLM 做结构化分类（使用公共模块）
        result: dict[str, Any] = await call_llm_for_structured(
            content=content,
            prompt=prompt,
            ai_config=ai_config,
            timeout=timeout,
            max_chars=max_chars,
        )

        logger.info(
            "LLM 解析完成: metadata=%d 项, points=%d 项, series=%d 项",
            len(result["metadata"]),
            len(result["points"]),
            len(result["series"]),
        )

        return ConverterResult(
            metadata=result.get("metadata", {}),
            points=result.get("points", []),
            series=result.get("series", []),
        ).to_dict()
