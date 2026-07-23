"""LLM 驱动的文档提取组件。

从 PDF 或文本文件中提取文本，调用大模型转为结构化 ObservationTable。
所有可配置项通过 manifest 参数传入，代码中无硬编码。

参数（全部在 manifest 定义，网页可编辑）：
- path: 文件路径（必填）
- prompt: 提取指令
- system_message: LLM system message
- schema: 输出列定义 [{name, type}, ...]
- pdf_engine: PDF 读取引擎（pymupdf / raw）
- max_content_chars: 最大内容字符数
- timeout: LLM 调用超时秒数
"""

import json
import re
from pathlib import Path
from typing import Any

import httpx

from apps.api.routers.ai_config import get_active_ai_config
from packages.common.errors import AppError
from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class EZScanExtractor:
    """LLM 驱动的文档提取组件。

    所有行为由参数控制，无硬编码默认值。
    """

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取文件 → 提取文本 → 调用 LLM → 返回 ObservationTable。"""
        path_str: str = params["path"]
        prompt: str = params.get("prompt", "")
        system_message: str = params.get("system_message", "")
        pdf_engine: str = params.get("pdf_engine", "pymupdf")
        image_dpi: int = params.get("image_dpi", 200)
        max_chars: int = params.get("max_content_chars", 50000)
        timeout: int = params.get("timeout", 120)

        if not prompt:
            raise AppError(
                code="validation_failed",
                message="缺少 prompt 参数",
                retryable=False,
            )
        if not system_message:
            raise AppError(
                code="validation_failed",
                message="缺少 system_message 参数",
                retryable=False,
            )

        file_path: Path = Path(path_str)

        # 1. 提取文件文本
        content = _extract_text(file_path, pdf_engine, image_dpi)
        if isinstance(content, str) and len(content) > max_chars:
            content = content[:max_chars]

        # 2. 判断是文本还是图片模式
        is_image_mode = isinstance(content, list)

        # 3. 空内容直接返回空表
        if (is_image_mode and len(content) == 0) or (not is_image_mode and not content.strip()):
            table = ObservationTable(
                columns=(),
                rows=(),
                source_locations=(),
            )
            return ComponentResult(
                outputs={"observations": table},
                summary="提取 0 行数据（空文件）",
                metadata={"row_count": 0, "header": {}, "all_rows": []},
            )

        # 5. 获取 AI 配置
        config: dict[str, Any] | None = await get_active_ai_config()
        if config is None:
            raise AppError(
                code="ai_not_configured",
                message="AI 大模型未配置，请在平台治理 → AI 配置中开启",
                retryable=False,
            )

        # 6. 构建 LLM 请求
        if is_image_mode:
            # 多模态：图片 + 文本指令
            user_content: list[dict[str, Any]] = [
                {"type": "text", "text": f"提取指令：{prompt}\n\n请根据以下图片内容提取数据。"},
            ]
            for img_data_url in content:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": img_data_url},
                })
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_content},
            ]
        else:
            # 纯文本模式
            user_message: str = (
                f"提取指令：{prompt}\n\n"
                f"文件内容：\n{content}"
            )
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ]

        request_body: dict[str, Any] = {
            "model": config["model_name"],
            "messages": messages,
            "chat_template_kwargs": {
                "enable_thinking": config.get("thinking_enabled", False),
            },
        }

        base_url: str = str(config["base_url"]).rstrip("/")
        url: str = f"{base_url}/chat/completions"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=float(timeout), proxy=None
            ) as client:
                resp = await client.post(url, headers=headers, json=request_body)
        except httpx.TimeoutException:
            raise AppError(
                code="ai_timeout",
                message=f"LLM 调用超时（{timeout} 秒）",
                retryable=True,
            )
        except httpx.HTTPError as exc:
            raise AppError(
                code="ai_request_failed",
                message=f"LLM 请求失败：{str(exc)[:200]}",
                retryable=True,
            )

        if resp.status_code != 200:
            raise AppError(
                code="ai_request_failed",
                message=f"LLM API 返回 {resp.status_code}: {resp.text[:200]}",
                retryable=True,
            )

        # 6. 解析返回
        resp_data: dict[str, Any] = resp.json()
        choices: list[dict[str, Any]] = resp_data.get("choices", [])
        if not choices:
            raise AppError(
                code="ai_empty_response",
                message="LLM 返回空响应",
                retryable=True,
            )
        llm_content: str = choices[0]["message"]["content"]

        extracted_data: dict[str, Any] = _parse_llm_json(llm_content)
        raw_rows: list[dict[str, Any]] = extracted_data.get("rows", [])
        header: dict[str, Any] = extracted_data.get("header", {})

        # 7. 从 LLM 返回的数据中自动推断列名
        if not raw_rows:
            columns: tuple[str, ...] = ()
            converted_rows: list[dict[str, Any]] = []
        else:
            columns = tuple(raw_rows[0].keys())
            converted_rows = raw_rows

        # 8. 构建 source_locations
        source_locs: list[dict[str, Any]] = [
            {"file": file_path.name, "row": idx}
            for idx in range(1, len(converted_rows) + 1)
        ]

        # 8. 构建 ObservationTable
        table = ObservationTable(
            columns=columns,
            rows=tuple(converted_rows),
            source_locations=tuple(source_locs),
        )

        return ComponentResult(
            outputs={"observations": table},
            summary=f"提取 {table.row_count()} 行数据",
            metadata={
                "row_count": table.row_count(),
                "header": header,
                "preview_rows": converted_rows[:5],
                "all_rows": converted_rows,
            },
        )


def _extract_text(file_path: Path, engine: str = "pymupdf", image_dpi: int = 200) -> str | list[str]:
    """从文件中提取文本内容。

    PDF 文件根据 engine 参数选择读取方式：
    - pymupdf: 使用 PyMuPDF 提取文字层
    - image: 使用 PyMuPDF 渲染为图片，返回 base64 列表（多模态）
    - raw: 直接以 UTF-8 读取

    非 PDF 文件直接以 UTF-8 读取。

    Args:
        file_path: 文件路径。
        engine: PDF 读取引擎（pymupdf / image / raw）。
        image_dpi: image 模式下的渲染分辨率。

    Returns:
        str | list[str]: 文本模式返回文本；image 模式返回 base64 图片列表。
    """
    if file_path.suffix.lower() != ".pdf":
        return file_path.read_text(encoding="utf-8", errors="ignore")

    if engine == "image":
        try:
            import fitz  # PyMuPDF
            import base64

            doc = fitz.open(str(file_path))
            images: list[str] = []
            for page in doc:
                pix = page.get_pixmap(dpi=image_dpi)
                img_bytes = pix.tobytes("png")
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                images.append(f"data:image/png;base64,{b64}")
            doc.close()
            return images
        except ImportError:
            # PyMuPDF 不可用，回退到文本提取
            return file_path.read_text(encoding="utf-8", errors="ignore")

    if engine == "pymupdf":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(file_path))
            text_parts: list[str] = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            return "\n".join(text_parts)
        except ImportError:
            # PyMuPDF 不可用，回退到直接读取
            return file_path.read_text(encoding="utf-8", errors="ignore")
    else:
        return file_path.read_text(encoding="utf-8", errors="ignore")


def _parse_llm_json(content: str) -> dict[str, Any]:
    """从 LLM 返回内容中提取 JSON 对象（3 级 fallback）。"""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    pattern: str = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    start: int = content.find("{")
    end: int = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise AppError(
        code="ai_parse_failed",
        message=f"无法从 LLM 响应中解析 JSON：{content[:200]}",
        retryable=True,
    )
