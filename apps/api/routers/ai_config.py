"""AI 大模型配置路由。

端点：
  GET    /api/v1/ai-config           — 获取当前配置（system:manage）
  PUT    /api/v1/ai-config           — 更新配置（system:manage）
  POST   /api/v1/ai-config/test      — 测试连接（system:manage）

配置存储在 ai_config 表中（单行设计，id=1），包含：
- base_url: API 基础地址
- api_key: API 密钥（加密存储，返回时脱敏）
- model_name: 模型名称
- enabled: 是否启用
"""

import os
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.ai.config_store import (
    get_active_ai_config as _get_active_ai_config_from_store,
)
from packages.ai.config_store import (
    get_config_row,
    upsert_config,
    upsert_meta_prompt,
)
from packages.ai.prompt_store import get_prompt as _get_prompt
from packages.common.clock import SystemClock
from packages.common.crypto import EnvelopeCrypto
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.safe_http import SafeHTTPClient, validate_url_host

#: 路由实例。
ai_config_router = APIRouter(prefix="/api/v1/ai-config", tags=["ai-config"])

#: 需 system:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("system:manage"))]
#: 需 assistant:use 权限的当前用户依赖。
UseUserDep = Annotated[CurrentUser, Depends(require_permission("assistant:use"))]


# ---- 请求/响应模型 ----


class AIConfigUpdateRequest(BaseModel):
    """更新 AI 配置请求。"""

    base_url: str = Field(
        ..., max_length=500, description="API 基础地址，如 https://api.openai.com/v1"
    )
    api_key: str = Field(..., max_length=500, description="API 密钥")
    model_name: str = Field(..., max_length=200, description="数据提取模型名称，如 gpt-4o")
    assistant_model_name: str = Field(
        "", max_length=200, description="AI助手模型名称，如 qwen-plus"
    )
    research_model_name: str = Field(
        "",
        max_length=200,
        description="研发助手模型名称（研究分析沙箱代码生成），留空则与数据提取模型相同",
    )
    enabled: bool = Field(True, description="是否启用")
    meta_prompt: str | None = Field(None, description="提示词推荐的系统提示词，留空则用内置默认")
    model_thinking_enabled: bool = Field(False, description="数据提取模型思考模式开关")
    assistant_thinking_enabled: bool = Field(True, description="AI助手模型思考模式开关")
    research_thinking_enabled: bool = Field(False, description="研发助手模型思考模式开关")


class AIConfigResponse(BaseModel):
    """AI 配置响应（密钥脱敏）。"""

    base_url: str
    api_key_masked: str
    model_name: str
    assistant_model_name: str = ""
    research_model_name: str = ""
    enabled: bool
    meta_prompt: str | None = None
    model_thinking_enabled: bool = False
    assistant_thinking_enabled: bool = True
    research_thinking_enabled: bool = False
    updated_at: str | None = None


class AITestRequest(BaseModel):
    """测试连接请求。"""

    base_url: str = Field(..., description="API 基础地址")
    api_key: str = Field(..., description="API 密钥")
    model_name: str = Field(..., description="模型名称")


class AITestResponse(BaseModel):
    """测试连接响应。"""

    success: bool
    message: str
    model_response: str | None = None


# ---- 辅助函数 ----

# 从 config/prompts.yaml 加载默认提示词（与 component_preview.py 统一来源）
_DEFAULT_META_PROMPT = _get_prompt("converter_meta_prompt.system_prompt")


def _mask_key(key: str) -> str:
    """脱敏 API 密钥。"""
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


# ---- 端点 ----


@ai_config_router.get("", response_model=AIConfigResponse)
async def get_ai_config(current_user: ManageUserDep) -> AIConfigResponse:
    """获取当前 AI 大模型配置（密钥脱敏）。"""
    async with session_scope(_get_session_factory()) as session:
        row = await get_config_row(session)
        if row is None:
            return AIConfigResponse(
                base_url="",
                api_key_masked="",
                model_name="",
                assistant_model_name="",
                enabled=False,
                meta_prompt=_DEFAULT_META_PROMPT,
                model_thinking_enabled=False,
                assistant_thinking_enabled=False,
                research_thinking_enabled=False,
            )
        # P2-C25: 先解密再掩码，确保掩码作用于明文而非密文
        crypto = EnvelopeCrypto.from_env()
        try:
            decrypted_key = crypto.decrypt(row["api_key"])
        except Exception:
            decrypted_key = ""
        return AIConfigResponse(
            base_url=row["base_url"],
            api_key_masked=_mask_key(decrypted_key),
            model_name=row["model_name"],
            assistant_model_name=row.get("assistant_model_name") or "",
            research_model_name=row.get("research_model_name") or "",
            enabled=row["enabled"],
            meta_prompt=row.get("meta_prompt") or _DEFAULT_META_PROMPT,
            model_thinking_enabled=row.get("model_thinking_enabled") or False,
            assistant_thinking_enabled=row.get("assistant_thinking_enabled") or False,
            research_thinking_enabled=row.get("research_thinking_enabled") or False,
            updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        )


@ai_config_router.put("", response_model=AIConfigResponse)
async def update_ai_config(
    body: AIConfigUpdateRequest,
    current_user: ManageUserDep,
) -> AIConfigResponse:
    """更新 AI 大模型配置。

    安全约定（技术设计文档 F-13）：
    - base_url 提交时校验目标地址（SSRF 防护），不允许内网地址。
    """
    # SSRF 防护：校验 base_url 不指向内网地址
    # 本地开发环境可通过 IRIP_ALLOW_PRIVATE_NETWORK=1 跳过私网校验
    if os.environ.get("IRIP_ALLOW_PRIVATE_NETWORK") != "1":
        try:
            parsed = httpx.URL(body.base_url)
            if parsed.scheme not in ("http", "https"):
                raise AppError(
                    code="ssrf_blocked",
                    message=f"AI base_url 协议不允许: {parsed.scheme}（仅支持 http/https）",
                    retryable=False,
                    fields={"base_url": body.base_url},
                )
            validate_url_host(str(parsed.host), parsed.port)
        except ValueError as exc:
            raise AppError(
                code="ssrf_blocked",
                message=f"AI base_url 安全校验失败: {exc}",
                retryable=False,
                fields={"base_url": body.base_url},
            ) from exc

    clock = SystemClock()
    now = clock.now()

    # 如果前端传 __use_saved__，保留已保存的密钥不变
    async with session_scope(_get_session_factory()) as session:
        existing = await get_config_row(session)
        if body.api_key == "__use_saved__":
            if existing is None:
                raise AppError(
                    code="validation_failed",
                    message="无法保留密钥：尚未保存过任何配置",
                    retryable=False,
                )
            encrypted_api_key = existing["api_key"]
        else:
            # H-06: 使用单例 crypto（from_env 返回单例实例）
            crypto = EnvelopeCrypto.from_env()
            encrypted_api_key = crypto.encrypt(body.api_key)
    existing = await upsert_config(
        _get_session_factory(),
        base_url=body.base_url,
        api_key=encrypted_api_key,
        model_name=body.model_name,
        assistant_model_name=body.assistant_model_name,
        research_model_name=body.research_model_name,
        enabled=body.enabled,
        meta_prompt=body.meta_prompt,
        model_thinking_enabled=body.model_thinking_enabled,
        assistant_thinking_enabled=body.assistant_thinking_enabled,
        research_thinking_enabled=body.research_thinking_enabled,
        updated_at=now,
        updated_by=current_user.user_id,
    )

    # 返回时用已保存密钥的掩码值（如果是 __use_saved__ 的话）
    # P2-C25: __use_saved__ 时先解密再掩码
    if body.api_key != "__use_saved__":
        masked_key = _mask_key(body.api_key)
    elif existing:
        crypto = EnvelopeCrypto.from_env()
        try:
            decrypted_existing = crypto.decrypt(existing["api_key"])
        except Exception:
            decrypted_existing = ""
        masked_key = _mask_key(decrypted_existing)
    else:
        masked_key = "***"

    return AIConfigResponse(
        base_url=body.base_url,
        api_key_masked=masked_key,
        model_name=body.model_name,
        assistant_model_name=body.assistant_model_name,
        enabled=body.enabled,
        meta_prompt=body.meta_prompt,
        updated_at=str(now),
    )


class MetaPromptUpdateRequest(BaseModel):
    """单独更新提示词推荐的系统提示词。"""

    meta_prompt: str = Field("", description="提示词推荐的系统提示词，留空则用内置默认")


class MetaPromptResponse(BaseModel):
    """提示词响应。"""

    meta_prompt: str | None = None


@ai_config_router.put("/meta-prompt", response_model=MetaPromptResponse)
async def update_meta_prompt(
    body: MetaPromptUpdateRequest,
    current_user: ManageUserDep,
) -> MetaPromptResponse:
    """单独更新提示词推荐的系统提示词。"""
    clock = SystemClock()
    now = clock.now()
    await upsert_meta_prompt(
        _get_session_factory(),
        meta_prompt=body.meta_prompt,
        updated_at=now,
        updated_by=current_user.user_id,
    )
    return MetaPromptResponse(meta_prompt=body.meta_prompt)


@ai_config_router.get("/meta-prompt", response_model=MetaPromptResponse)
async def get_meta_prompt(
    current_user: ManageUserDep,
) -> MetaPromptResponse:
    """获取提示词推荐的系统提示词。"""
    async with session_scope(_get_session_factory()) as session:
        row = await get_config_row(session)
        if row is None:
            return MetaPromptResponse(meta_prompt=_DEFAULT_META_PROMPT)
        return MetaPromptResponse(meta_prompt=row.get("meta_prompt") or _DEFAULT_META_PROMPT)


@ai_config_router.post("/test", response_model=AITestResponse)
async def test_ai_connection(
    body: AITestRequest,
    current_user: ManageUserDep,
) -> AITestResponse:
    """测试 AI 连接（发送一条简单消息验证配置）。

    安全约定（技术设计文档 F-13）：
    - 使用 SafeHTTPClient 发起请求（SSRF 防护）；
    - 测试前校验 base_url 不指向内网地址。
    """
    # SSRF 防护：校验 base_url 不指向内网地址
    # 本地开发环境可通过 IRIP_ALLOW_PRIVATE_NETWORK=1 跳过私网校验
    if os.environ.get("IRIP_ALLOW_PRIVATE_NETWORK") != "1":
        try:
            parsed = httpx.URL(body.base_url)
            if parsed.scheme not in ("http", "https"):
                return AITestResponse(
                    success=False,
                    message=f"协议不允许: {parsed.scheme}（仅支持 http/https）",
                )
            validate_url_host(str(parsed.host), parsed.port)
        except ValueError as exc:
            return AITestResponse(
                success=False,
                message=f"SSRF 防护阻断: {exc}",
            )

    # 如果前端传 __use_saved__，从数据库读已保存的密钥
    api_key = body.api_key
    if api_key == "__use_saved__":
        saved = await get_active_ai_config()
        if saved is None:
            # 未启用，直接从表读
            async with session_scope(_get_session_factory()) as session:
                row = await get_config_row(session)
                if row is None:
                    return AITestResponse(success=False, message="未找到已保存的配置")
                # H-06: 使用单例 crypto，解密失败直接 raise
                crypto = EnvelopeCrypto.from_env()
                try:
                    api_key = crypto.decrypt(row["api_key"])
                except ValueError:
                    return AITestResponse(success=False, message="API key 解密失败，请重新配置")
        else:
            api_key = saved["api_key"]

    try:
        async with SafeHTTPClient(timeout=15.0, max_size=1024 * 1024) as client:
            resp = await client.post(
                body.base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": body.model_name,
                    "messages": [
                        {"role": "user", "content": "Hello, respond with 'OK' in one word."},
                    ],
                    "max_tokens": 10,
                },
            )
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            answer = choices[0]["message"]["content"] if choices else ""
            return AITestResponse(
                success=True,
                message="连接成功",
                model_response=answer,
            )
        else:
            return AITestResponse(
                success=False,
                message=f"API 返回 {resp.status_code}: {resp.text[:200]}",
            )
    except httpx.TimeoutException:
        return AITestResponse(success=False, message="连接超时")
    except ValueError as exc:
        return AITestResponse(success=False, message=f"安全校验失败: {str(exc)[:200]}")
    except Exception as exc:
        return AITestResponse(success=False, message=f"连接失败: {str(exc)[:200]}")


# ---- 供其他模块调用的配置读取函数 ----


async def get_active_ai_config() -> dict[str, str] | None:
    """读取已启用的大模型配置（供 AIService 使用）。

    F-12: 读取时解密 API key（envelope encryption）。

    Returns:
        dict | None: 包含 base_url/api_key/model_name 的字典，未配置或未启用时返回 None。
    """
    return await _get_active_ai_config_from_store(_get_session_factory())


# ---- DI 占位 ----

_session_factory: Any = None


def set_session_factory(factory: Any) -> None:
    """设置会话工厂（由 main.py lifespan 调用）。"""
    global _session_factory
    _session_factory = factory


def _get_session_factory() -> Any:
    if _session_factory is None:
        raise RuntimeError("Session factory not set. Call set_session_factory() first.")
    return _session_factory
