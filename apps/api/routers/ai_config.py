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
import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.common.crypto import EnvelopeCrypto
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.clock import SystemClock
from packages.common.safe_http import SafeHTTPClient, validate_url_host

#: 路由实例。
ai_config_router = APIRouter(prefix="/api/v1/ai-config", tags=["ai-config"])

#: 需 system:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("system:manage"))]
#: 需 assistant:use 权限的当前用户依赖。
UseUserDep = Annotated[CurrentUser, Depends(require_permission("assistant:use"))]


# ---- 表定义（内联，避免迁移依赖） ----

import packages.common.database as db_mod
from packages.common.db_types import GUID, UTCDateTime

_ai_config_table = sa.Table(
    "ai_config",
    db_mod.Base.metadata,
    sa.Column("id", sa.Integer, primary_key=True, server_default=sa.text("1")),
    sa.Column("base_url", sa.Text, nullable=False),
    sa.Column("api_key", sa.Text, nullable=False),
    sa.Column("model_name", sa.Text, nullable=False),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("updated_at", UTCDateTime, server_default=sa.func.now(), nullable=False),
    sa.Column("updated_by", GUID, nullable=True),
    extend_existing=True,
)


# ---- 请求/响应模型 ----


class AIConfigUpdateRequest(BaseModel):
    """更新 AI 配置请求。"""

    base_url: str = Field(..., max_length=500, description="API 基础地址，如 https://api.openai.com/v1")
    api_key: str = Field(..., max_length=500, description="API 密钥")
    model_name: str = Field(..., max_length=200, description="模型名称，如 gpt-4o")
    enabled: bool = Field(True, description="是否启用")


class AIConfigResponse(BaseModel):
    """AI 配置响应（密钥脱敏）。"""

    base_url: str
    api_key_masked: str
    model_name: str
    enabled: bool
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


def _mask_key(key: str) -> str:
    """脱敏 API 密钥。"""
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


async def _get_config_row(session: Any) -> dict[str, Any] | None:
    """读取配置行。"""
    result = await session.execute(sa.select(_ai_config_table).where(_ai_config_table.c.id == 1))
    row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)


# ---- 端点 ----


@ai_config_router.get("", response_model=AIConfigResponse)
async def get_ai_config(current_user: ManageUserDep) -> AIConfigResponse:
    """获取当前 AI 大模型配置（密钥脱敏）。"""
    async with session_scope(
        _get_session_factory()
    ) as session:
        row = await _get_config_row(session)
        if row is None:
            return AIConfigResponse(
                base_url="",
                api_key_masked="",
                model_name="",
                enabled=False,
            )
        return AIConfigResponse(
            base_url=row["base_url"],
            api_key_masked=_mask_key(row["api_key"]),
            model_name=row["model_name"],
            enabled=row["enabled"],
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

    # F-12: API key 加密存储（envelope encryption）
    crypto = EnvelopeCrypto.from_env()
    encrypted_api_key = crypto.encrypt(body.api_key)

    async with session_scope(
        _get_session_factory()
    ) as session:
        existing = await _get_config_row(session)
        if existing is None:
            await session.execute(
                _ai_config_table.insert().values(
                    id=1,
                    base_url=body.base_url,
                    api_key=encrypted_api_key,
                    model_name=body.model_name,
                    enabled=body.enabled,
                    updated_at=now,
                    updated_by=current_user.user_id,
                )
            )
        else:
            await session.execute(
                _ai_config_table.update()
                .where(_ai_config_table.c.id == 1)
                .values(
                    base_url=body.base_url,
                    api_key=encrypted_api_key,
                    model_name=body.model_name,
                    enabled=body.enabled,
                    updated_at=now,
                    updated_by=current_user.user_id,
                )
            )

    return AIConfigResponse(
        base_url=body.base_url,
        api_key_masked=_mask_key(body.api_key),
        model_name=body.model_name,
        enabled=body.enabled,
        updated_at=str(now),
    )


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

    try:
        async with SafeHTTPClient(timeout=15.0, max_size=1024 * 1024) as client:
            resp = await client.post(
                body.base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {body.api_key}",
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
    async with session_scope(_get_session_factory()) as session:
        row = await _get_config_row(session)
        if row is None or not row["enabled"]:
            return None
        # F-12: 解密 API key
        crypto = EnvelopeCrypto.from_env()
        try:
            decrypted_key = crypto.decrypt(row["api_key"])
        except ValueError:
            # 兼容旧版明文存储（迁移期间）
            decrypted_key = row["api_key"]
        return {
            "base_url": row["base_url"],
            "api_key": decrypted_key,
            "model_name": row["model_name"],
        }


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
