"""组件预览路由：提示词推荐 + 数据抽取预览。

端点（需 flow:read 权限）：
  POST /api/v1/component-preview/prompt-recommend  — 根据预加载文件生成推荐提示词
  POST /api/v1/component-preview/extract-preview   — 用当前提示词预览数据抽取结果

工作流：
  1. 从 MinIO 下载预加载的 artifact 文件到临时目录
  2. 根据文件后缀自动提取文本/图片（复用 ez_scan_extractor 的 _extract_text）
  3. 调用大模型（从 YAML 配置读取 data_extraction 场景配置）
  4. 返回结果
"""

import os
import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.routers.uploads import get_artifact_service
from packages.ai.yaml_config import get_scenario_config
from packages.common.artifacts import ArtifactService
from packages.common.errors import AppError
from packages.common.safe_http import SafeHTTPClient

#: 路由实例。
component_preview_router = APIRouter(prefix="/api/v1/component-preview", tags=["component-preview"])

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
    prompt: str = Field("", description="当前 LLM 提示词（xrd_converter 模式下可空）")
    tool_type: str = Field(  # noqa: E501
        "llm_converter",
        description="解析工具类型：llm_converter 或 xrd_converter",
    )


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
    """根据文件后缀自动提取文本（复用公共 text_extractor）。

    Args:
        file_path: 临时文件路径。

    Returns:
        str: 文本内容（图片通过 PaddleOCR 提取为文本）。
    """
    from packages.plugins.converters.common.text_extractor import extract_text

    return extract_text(file_path, engine="auto")


async def _call_llm(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    timeout: int = 120,  # noqa: ASYNC109
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
        "temperature": 0.0,
        "seed": 42,
    }
    # H-05: 使用 SafeHTTPClient（SSRF 防护 + 流式大小限制）
    async with SafeHTTPClient(timeout=float(timeout), max_size=10 * 1024 * 1024) as client:
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


@component_preview_router.post("/prompt-recommend", response_model=PromptRecommendResponse)
async def recommend_prompt(
    body: PromptRecommendRequest,
    current_user: ReadUserDep,
    artifact_service: ArtifactServiceDep,
) -> PromptRecommendResponse:
    """根据预加载文件内容，由大模型生成推荐的 LLM 提示词。

    下载文件 → 提取文本 → 让大模型分析文件内容并生成最优提取提示词。
    """
    try:
        config = get_scenario_config("data_extraction")
    except Exception:
        raise AppError(
            code="ai_not_configured",
            message="AI 大模型未配置，请检查 config/ai-usage.yaml",
            retryable=False,
        ) from None

    # 下载文件
    file_path = await _download_artifact(artifact_service, body.artifact_id, body.filename)
    try:
        content = _extract_file_content(file_path)
    finally:
        file_path.unlink(missing_ok=True)

    # 构建大模型请求：让 AI 分析文件并生成提示词（纯文本模式，不使用多模态）

    # 从 YAML 加载 meta_prompt（含 {filename} 模板变量）
    from packages.ai.prompt_store import get_prompt

    meta_prompt = get_prompt("converter_meta_prompt.system_prompt").replace(
        "{filename}", body.filename
    )

    # 转换为 dict 格式供 _call_llm 使用
    config_dict: dict[str, str] = {
        "base_url": config.base_url,
        "api_key": config.api_key,
        "model_name": config.model,
    }

    messages = [
        {
            "role": "user",
            "content": f"{meta_prompt}\n\n文件内容：\n{content}",
        }
    ]

    answer = await _call_llm(config_dict, messages)
    return PromptRecommendResponse(prompt=answer)


@component_preview_router.post("/extract-preview", response_model=ExtractPreviewResponse)
async def extract_preview(
    body: ExtractPreviewRequest,
    current_user: ReadUserDep,
    artifact_service: ArtifactServiceDep,
) -> ExtractPreviewResponse:
    """用当前提示词对预加载文件进行数据抽取预览。

    通过插件注册表统一调用解析器：
    - llm_converter：下载文件 → 调用插件（含文本提取+LLM调用）→ 返回 JSON。
    - xrd_converter：下载文件 → 调用插件（确定性解析）→ 返回 JSON。
    """
    import json

    from packages.plugins import registry as plugin_registry

    # 下载文件
    file_path = await _download_artifact(artifact_service, body.artifact_id, body.filename)
    try:
        # 获取 AI 配置（llm_converter 需要）
        ai_config: dict[str, Any] | None = None
        if body.tool_type == "llm_converter":
            scenario_config = get_scenario_config("data_extraction")
            ai_config = {
                "base_url": scenario_config.base_url,
                "api_key": scenario_config.api_key,
                "model_name": scenario_config.model,
            }

        # 通过插件注册表调用解析器
        converter = plugin_registry.get(body.tool_type)
        if converter is None:
            raise AppError(
                code="missing_dependency",
                message=f"解析器插件 '{body.tool_type}' 未注册",
                retryable=False,
                fields={"tool_type": body.tool_type},
            )

        result: dict[str, Any] = await converter.execute(
            {
                "file_path": str(file_path),
                "prompt": body.prompt,
                "ai_config": ai_config,
            }
        )

        return ExtractPreviewResponse(result=json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        file_path.unlink(missing_ok=True)
