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
    prompt: str = Field("", description="当前 LLM 提示词（xrd_tool 模式下可空）")
    tool_type: str = Field("llm", description="解析工具类型：llm 或 xrd_tool")


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


@component_preview_router.post("/prompt-recommend", response_model=PromptRecommendResponse)
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
    file_path = await _download_artifact(artifact_service, body.artifact_id, body.filename)
    try:
        content = _extract_file_content(file_path)
    finally:
        file_path.unlink(missing_ok=True)

    # 构建大模型请求：让 AI 分析文件并生成提示词
    is_image_mode = isinstance(content, list)

    # 优先从数据库读自定义 meta_prompt，没有才用内置默认
    _default_meta_prompt = (
        "你是一个工业数据分析助手。请阅读上传文件的实际内容，"
        "并生成一段可直接使用的数据提取提示词，"
        "用于指导另一个大模型从该文件及同类文件中提取结构化数据。\n\n"
        f"文件名：{body.filename}\n\n"
        "文件名可能反映报告类型，但只能作为辅助判断依据。"
        "必须优先根据文件中的工作表、表头、字段名、合并单元格、数据分组和实际内容确定提取策略，"
        "不得套用固定报告模板。\n\n"
        "生成提示词前，应先识别：\n\n"
        "1. 文件包含哪些工作表、数据区域或独立表格；\n"
        "2. 哪些字段属于报告级公共信息；\n"
        "3. 哪些字段属于单值检测指标；\n"
        "4. 哪些数据属于连续多行、重复测量、分布曲线或成组结果；\n"
        "5. 是否存在合并单元格、空白继承、横向宽表、纵向长表、重复表头、单位列或单位写在字段名中的情况；\n"  # noqa: E501
        "6. 是否存在文本、空值、异常字符或数字与文本混合的结果。\n\n"
        "你生成的提示词必须包含以下内容：\n\n"
        "一、角色设定\n"
        "明确其为工业检测报告结构化数据抽取助手，要求忠实提取，不臆造、不修正源文件数据。\n\n"
        "二、结构识别与提取规则\n"
        "要求另一个模型根据当前文件实际结构执行以下分类：\n\n"
        "* metadata：仅存放报告级单值信息，例如委托单号、样品名称、客户名称、申请日期、检测日期、"
        "设备、试验员、检查项目、文件名等。\n"
        "* points：存放独立的单值检测指标，每项格式为：\n"
        '  {"name": "实际指标名称", "value": 实际值, "unit": "实际单位或空字符串"}\n'
        "* series：存放具有多行、多列、重复测量、连续序列或分组关系的数据，每项格式为：\n"
        '  {"name": "实际序列名称", "columns": ["实际列名"], "rows": [[实际值]]}\n\n'
        "分类时必须遵守：\n\n"
        "1. 所有检测结果必须进入 points 或 series，不得放入 metadata。\n"
        "2. 一个指标只有一个结果时，通常放入 points。\n"
        "3. 同一指标对应多个连续结果、多个测点、多个时间点或多次重复测量时，"
        "应整体放入 series，不得拆成互不相关的单值。\n"
        "4. 多行表格、粒径分布、元素含量、时间序列、曲线数据、工况数据及成组试验结果均放入 series。\n"  # noqa: E501
        '5. 若多个连续结果由合并的"检测项名称"或分组标签共同标识，应向下继承该名称，并保留原始顺序。\n'  # noqa: E501
        "6. 若一组数据只有一个结果列，也应作为单列序列提取，不得因缺少第二列而丢弃。\n"
        "7. 若表格为横向宽表，应根据字段对应关系转换为合理的点或序列，但不得改变数据含义。\n"
        "8. 文件中存在多个独立表格或多个工作表时，应分别生成多个 series，不得强行合并。\n"
        "9. 字段名、指标名、序列名和列名应优先使用源文件实际名称，不得根据示例臆造。\n"
        "10. 单位只能从源文件的单位列、字段名、表头或明确文本中提取；"
        "未出现单位时使用空字符串，不得推测。\n"
        "11. 数值保持数字类型和原始精度；文本保持原始字符串。\n"
        "12. 对空值、异常字符、数字与文本混合结果应原样保留，"
        "不得擅自删除、修正、补零或猜测含义。\n"
        "13. 必须保留原始数据顺序，不得排序、去重、汇总或只提取部分代表值。\n"
        "14. 合并单元格中的公共信息和分组名称，应应用到其覆盖的全部数据行。\n"
        "15. 若某些数据无法可靠判断属于单值还是序列，"
        "应优先依据其在文件中的分组结构和结果数量判断，而不是仅凭指标名称判断。\n\n"
        "三、输出格式要求\n"
        "要求返回合法 JSON，固定结构为：\n\n"
        "{\n"
        '"metadata": {},\n'
        '"points": [],\n'
        '"series": []\n'
        "}\n\n"
        "三类字段必须始终存在：\n\n"
        "* 无元数据时，metadata 为 {}；\n"
        "* 无单值指标时，points 为 []；\n"
        "* 无序列数据时，series 为 []。\n\n"
        "生成的提示词中应根据当前文件实际出现的字段和数据结构，"
        "给出简短、针对性的 JSON 示例。"
        "示例不得加入源文件中不存在的字段、指标或单位，也不得将示例值描述为真实结果。\n\n"
        "四、完整性要求\n"
        "要求另一个模型检查所有工作表和数据区域，确保：\n\n"
        "* 报告级信息未被误放入检测结果；\n"
        "* 检测结果未被误放入 metadata；\n"
        "* 单值指标未遗漏；\n"
        "* 多行序列未被拆散；\n"
        "* 合并单元格对应的数据未丢失；\n"
        "* 文本型或异常结果未被忽略；\n"
        "* 不因空白单元格、重复字段或格式差异而漏行。\n\n"
        "五、收尾要求\n"
        "生成的提示词必须明确要求：\n\n"
        "只返回最终 JSON，不要 Markdown 代码块，不要解释、前言、注释或后缀。\n\n"
        "最终只返回你生成的数据提取提示词本身，不要解释，不要添加任何额外说明。"
    )

    # 从数据库读自定义 meta_prompt
    meta_prompt = _default_meta_prompt
    try:
        ai_cfg = await get_active_ai_config()
        if ai_cfg and ai_cfg.get("meta_prompt"):
            meta_prompt = ai_cfg["meta_prompt"].replace("{body.filename}", body.filename)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "读取自定义 meta_prompt 失败，使用内置默认", exc_info=True
        )  # noqa: E501

    if is_image_mode:
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": meta_prompt},
        ]
        for img_data_url in content:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": img_data_url},
                }
            )
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


@component_preview_router.post("/extract-preview", response_model=ExtractPreviewResponse)
async def extract_preview(
    body: ExtractPreviewRequest,
    current_user: ReadUserDep,
    artifact_service: ArtifactServiceDep,
) -> ExtractPreviewResponse:
    """用当前提示词对预加载文件进行数据抽取预览。

    tool_type=llm：下载文件 → 提取文本 → 用用户提示词调用大模型 → 返回抽取结果。
    tool_type=xrd_tool：下载文件 → 调用 XRD 确定性解析器 → 返回 JSON 结果。
    """
    # XRD 工具模式：直接调 Python 解析器，不走 LLM
    if body.tool_type == "xrd_tool":
        file_path = await _download_artifact(artifact_service, body.artifact_id, body.filename)
        try:
            import asyncio
            import json

            from packages.components.builtin.ingestion.xrd_converter.convert import (
                convert_xrd_file_to_json,
            )

            result = await asyncio.to_thread(convert_xrd_file_to_json, str(file_path))
            return ExtractPreviewResponse(result=json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            file_path.unlink(missing_ok=True)

    # LLM 模式
    config = await get_active_ai_config()
    if config is None or not config.get("base_url") or not config.get("api_key"):
        raise AppError(
            code="ai_not_configured",
            message="AI 大模型未配置，请先在治理 → AI 配置中开启",
            retryable=False,
        )

    # 下载文件
    file_path = await _download_artifact(artifact_service, body.artifact_id, body.filename)
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
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": img_data_url},
                }
            )
        messages = [{"role": "user", "content": user_content}]
    else:
        user_message = f"{body.prompt}\n\n文件内容：\n{content[:50000]}"
        messages = [{"role": "user", "content": user_message}]

    answer = await _call_llm(config, messages)
    return ExtractPreviewResponse(result=answer)
