"""LLM 调用公共工具。

从 ``llm_converter/converter.py`` 提取的共享逻辑，供所有需要 LLM 分类的
converter 插件复用。XRD converter 不需要 LLM，不使用此模块。

提供：
- ``call_llm_for_structured``: 用提取的内容 + prompt 调用 LLM，返回 {metadata, points, series}
- ``_call_llm``: LLM API 调用（含断线重试，SSRF 防护）
- ``_parse_llm_json``: 从 LLM 返回中提取 JSON（3 级 fallback）

设计要点：
- 支持 text 模式（content 为 str）和 image 模式（content 为 list[str] base64 data URL）
- 所有 converter 共享同一套 LLM 调用逻辑，保持行为一致
"""

import asyncio
import json
import re
from typing import Any

import httpx

from packages.common.errors import AppError
from packages.common.safe_http import SafeHTTPClient

# ============================================================
# LLM API 调用
# ============================================================


async def _call_llm(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,  # noqa: ASYNC109
) -> httpx.Response:
    """调用 LLM API，含断线重试（H-05: 使用 SafeHTTPClient）。"""
    try:
        async with SafeHTTPClient(timeout=float(timeout), max_size=10 * 1024 * 1024) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        raise AppError(
            code="ai_timeout",
            message=f"LLM 调用超时（{timeout} 秒）",
            retryable=True,
        ) from None
    except httpx.HTTPError as exc:
        err_msg = str(exc)[:200]
        if "disconnected" in err_msg.lower() or "remote" in err_msg.lower():
            await asyncio.sleep(2)
            try:
                async with SafeHTTPClient(
                    timeout=float(timeout + 120), max_size=10 * 1024 * 1024
                ) as client2:
                    resp = await client2.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc2:
                raise AppError(
                    code="ai_request_failed",
                    message=f"LLM 请求失败（重试后）：{str(exc2)[:200]}",
                    retryable=True,
                ) from None
        else:
            raise AppError(
                code="ai_request_failed",
                message=f"LLM 请求失败：{err_msg}",
                retryable=True,
            ) from None

    if resp.status_code != 200:
        raise AppError(
            code="ai_request_failed",
            message=f"LLM API 返回 {resp.status_code}: {resp.text[:200]}",
            retryable=True,
        )
    return resp


# ============================================================
# LLM 响应解析
# ============================================================


def _parse_llm_json(content: str) -> dict[str, Any]:
    """从 LLM 返回内容中提取 JSON 对象（3 级 fallback）。"""
    try:
        return json.loads(content)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    pattern: str = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    start: int = content.find("{")
    end: int = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    raise AppError(
        code="ai_parse_failed",
        message=f"无法从 LLM 响应中解析 JSON：{content[:200]}",
        retryable=True,
    )


# ============================================================
# 公共入口：调用 LLM 做结构化分类
# ============================================================


async def call_llm_for_structured(
    content: str,
    prompt: str,
    ai_config: dict[str, Any] | None,
    timeout: int = 300,  # noqa: ASYNC109
    max_chars: int = 999999999,
) -> dict[str, Any]:
    """用提取的文本内容 + prompt 调用 LLM，返回 {metadata, points, series}。

    content 始终为 str（纯文本）。图片和扫描件已由 PaddleOCR 提取为文本，
    不依赖多模态 LLM。

    Args:
        content: 文件提取的文本内容（str）。
        prompt: LLM 提示词（必填）。
        ai_config: AI 配置字典（含 base_url/api_key/model_name，必填）。
        timeout: 超时秒数，默认 300。
        max_chars: 文本内容最大字符数，超出截断。

    Returns:
        dict: ``{"metadata": {...}, "points": [...], "series": [...]}``

    Raises:
        AppError: prompt 为空、ai_config 为 None、LLM 调用失败、JSON 解析失败。
    """
    if not prompt:
        raise AppError(
            code="validation_failed",
            message="缺少 prompt 参数",
            retryable=False,
        )

    if ai_config is None:
        raise AppError(
            code="ai_not_configured",
            message="AI 大模型未配置，请在平台治理 → AI 配置中开启",
            retryable=False,
        )

    # 截断超长文本
    if len(content) > max_chars:
        content = content[:max_chars]

    # 空内容直接返回空结果
    if not content.strip():
        return {"metadata": {}, "points": [], "series": []}

    # 构建 LLM 请求（纯文本模式，不使用多模态）
    user_message = f"{prompt}\n\n文件内容：\n{content}"
    messages = [{"role": "user", "content": user_message}]

    request_body: dict[str, Any] = {
        "model": ai_config["model_name"],
        "messages": messages,
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.0,
        "seed": 42,
    }

    base_url: str = str(ai_config["base_url"]).rstrip("/")
    url: str = f"{base_url}/chat/completions"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {ai_config['api_key']}",
        "Content-Type": "application/json",
    }

    # 调用 LLM
    resp = await _call_llm(url, headers, request_body, timeout)

    # 解析返回
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
    return {
        "metadata": extracted_data.get("metadata", {}),
        "points": extracted_data.get("points", []),
        "series": extracted_data.get("series", []),
    }
