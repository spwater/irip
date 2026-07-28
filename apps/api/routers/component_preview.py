"""组件预览路由：提示词推荐 + 数据抽取预览。

端点（需 flow:read 权限）：
  POST /api/v1/component-preview/prompt-recommend  — 根据预加载文件生成推荐提示词
  POST /api/v1/component-preview/extract-preview   — 用当前提示词预览数据抽取结果

工作流：
  1. 从 MinIO 下载预加载的 artifact 文件到临时目录
  2. 根据文件后缀自动提取文本/图片（复用 ez_scan_extractor 的 _extract_text）
  3. 调用大模型（复用 ai_config 的 get_active_ai_config）
  4. 返回结果
"""

import os
import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.routers.ai_config import get_active_ai_config
from apps.api.routers.uploads import get_artifact_service
from packages.common.artifacts import ArtifactService
from packages.common.errors import AppError

#: 路由实例。
component_preview_router = APIRouter(
    prefix="/api/v1/component-preview", tags=["component-preview"]
)

#: 需 flow:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("flow:read"))]

#: Artifact 服务依赖。
ArtifactServiceDep = Annotated[ArtifactService, Depends(get_artifact_service)]


# ---- 请求/响应模型 ----


class PromptRecommendRequest(BaseModel):
    """提示词推荐请求。"""
    artifact_id: str = Field(..., description="预加载文件的 artifact ID")
    filename: str = Field(..., description="原始文件名（用于推断后缀）")


class PromptRecommendResponse(BaseModel):
    """提示词推荐响应。"""
    prompt: str = Field(..., description="大模型生成的推荐提示词")


class ExtractPreviewRequest(BaseModel):
    """数据抽取预览请求。"""
    artifact_id: str = Field(..., description="预加载文件的 artifact ID")
    filename: str = Field(..., description="原始文件名（用于推断后缀）")
    prompt: str = Field(..., description="当前 LLM 提示词")


class ExtractPreviewResponse(BaseModel):
    """数据抽取预览响应。"""
    result: str = Field(..., description="大模型返回的抽取结果（JSON 文本）")


# ---- 辅助函数 ----


async def _download_artifact(
    artifact_service: ArtifactService,
    artifact_id: str,
    filename: str,
) -> Path:
    """从 MinIO 下载 artifact 到临时文件。

    Args:
        artifact_service: Artifact 服务实例。
        artifact_id: Artifact UUID 字符串。
        filename: 原始文件名（用于保留后缀）。

    Returns:
        Path: 临时文件路径（调用方负责清理）。
    """
    data = await artifact_service.get_bytes(UUID(artifact_id))
    suffix = Path(filename).suffix
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="preview_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        os.unlink(tmp_path)
        raise
    return Path(tmp_path)


def _extract_file_content(file_path: Path) -> str | list[str]:
    """根据文件后缀自动提取文本/图片（复用 ez_scan_extractor 逻辑）。

    Args:
        file_path: 临时文件路径。

    Returns:
        str | list[str]: 文本内容或 base64 图片列表。
    """
    from packages.components.builtin.ingestion.ez_scan_extractor import (
        _extract_text,
    )

    return _extract_text(file_path, engine="auto")


async def _call_llm(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    timeout: int = 120,
) -> str:
    """调用大模型，返回文本回答。

    Args:
        config: AI 配置（base_url / api_key / model_name）。
        messages: 消息列表。
        timeout: 超时秒数。

    Returns:
        str: 大模型回答文本。
    """
    base_url: str = str(config["base_url"]).rstrip("/")
    url: str = f"{base_url}/chat/completions"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": config["model_name"],
        "messages": messages,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with httpx.AsyncClient(timeout=float(timeout), proxy=None) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code != 200:
        raise AppError(
            code="ai_request_failed",
            message=f"大模型调用失败: HTTP {resp.status_code}",
            retryable=False,
        )
    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise AppError(
            code="ai_request_failed",
            message="大模型返回空响应",
            retryable=False,
        )
    return str(choices[0]["message"]["content"])


# ---- 端点 ----


@component_preview_router.post(
    "/prompt-recommend", response_model=PromptRecommendResponse
)
async def recommend_prompt(
    body: PromptRecommendRequest,
    current_user: ReadUserDep,
    artifact_service: ArtifactServiceDep,
) -> PromptRecommendResponse:
    """根据预加载文件内容，由大模型生成推荐的 LLM 提示词。

    下载文件 → 提取文本 → 让大模型分析文件内容并生成最优提取提示词。
    """
    config = await get_active_ai_config()
    if config is None or not config.get("base_url") or not config.get("api_key"):
        raise AppError(
            code="ai_not_configured",
            message="AI 大模型未配置，请先在治理 → AI 配置中开启",
            retryable=False,
        )

    # 下载文件
    file_path = await _download_artifact(
        artifact_service, body.artifact_id, body.filename
    )
    try:
        content = _extract_file_content(file_path)
    finally:
        file_path.unlink(missing_ok=True)

    # 构建大模型请求：让 AI 分析文件并生成提示词
    is_image_mode = isinstance(content, list)
    meta_prompt = (
        "你是一个工业数据分析助手。请分析以下文件内容，"
        "生成一段最优的数据提取提示词（LLM prompt），用于指导另一个大模型从类似文件中提取结构化数据。\n\n"
        "要求：\n"
        "1. 提取报告头部信息（分析类型、分析日期、样品名、文件名等）\n"
        "2. 提取所有检测结果（编号、组分、单位、结果等）\n"
        '3. 返回 JSON 格式：{"metadata": {...}, "data": [...]}\n'
        "4. 只返回提示词本身，不要解释\n\n"
        "参考模板：\n"
        "根据用户提供的报告内容，提取头部信息和所有检测结果。"
        '返回JSON格式：{"metadata": {"analysis_type": "...", "analysis_date": "...", '
        '"sample_name": "...", "file_name": "..."}, "data": [{"No.": 1, "组分": "...", '
        '"单位": "...", "结果": 0.0}]}。只返回JSON，不要解释。'
    )

    if is_image_mode:
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": meta_prompt},
        ]
        for img_data_url in content:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": img_data_url},
            })
        messages = [{"role": "user", "content": user_content}]
    else:
        messages = [
            {
                "role": "user",
                "content": f"{meta_prompt}\n\n文件内容：\n{content[:30000]}",
            }
        ]

    answer = await _call_llm(config, messages)
    return PromptRecommendResponse(prompt=answer)


@component_preview_router.post(
    "/extract-preview", response_model=ExtractPreviewResponse
)
async def extract_preview(
    body: ExtractPreviewRequest,
    current_user: ReadUserDep,
    artifact_service: ArtifactServiceDep,
) -> ExtractPreviewResponse:
    """用当前提示词对预加载文件进行数据抽取预览。

    下载文件 → 提取文本 → 用用户提示词调用大模型 → 返回抽取结果。
    """
    config = await get_active_ai_config()
    if config is None or not config.get("base_url") or not config.get("api_key"):
        raise AppError(
            code="ai_not_configured",
            message="AI 大模型未配置，请先在治理 → AI 配置中开启",
            retryable=False,
        )

    # 下载文件
    file_path = await _download_artifact(
        artifact_service, body.artifact_id, body.filename
    )
    try:
        content = _extract_file_content(file_path)
    finally:
        file_path.unlink(missing_ok=True)

    is_image_mode = isinstance(content, list)

    if is_image_mode:
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": f"{body.prompt}\n\n请根据以下图片内容提取数据。"},
        ]
        for img_data_url in content:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": img_data_url},
            })
        messages = [{"role": "user", "content": user_content}]
    else:
        user_message = f"{body.prompt}\n\n文件内容：\n{content[:50000]}"
        messages = [{"role": "user", "content": user_message}]

    answer = await _call_llm(config, messages)
    return ExtractPreviewResponse(result=answer)
