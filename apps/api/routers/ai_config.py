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


# ---- 表定义（内联，避免迁移依赖） ----

import packages.common.database as db_mod  # noqa: E402
from packages.common.db_types import GUID, UTCDateTime  # noqa: E402

_ai_config_table = sa.Table(
    "ai_config",
    db_mod.Base.metadata,
    sa.Column("id", sa.Integer, primary_key=True, server_default=sa.text("1")),
    sa.Column("base_url", sa.Text, nullable=False),
    sa.Column("api_key", sa.Text, nullable=False),
    sa.Column("model_name", sa.Text, nullable=False),
    sa.Column("assistant_model_name", sa.Text, nullable=True),
    sa.Column("research_model_name", sa.Text, nullable=True),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("meta_prompt", sa.Text, nullable=True),
    sa.Column("updated_at", UTCDateTime, server_default=sa.func.now(), nullable=False),
    sa.Column("updated_by", GUID, nullable=True),
    extend_existing=True,
)


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
        "", max_length=200, description="研发助手模型名称（研究分析沙箱代码生成），留空则与数据提取模型相同"
    )
    enabled: bool = Field(True, description="是否启用")
    meta_prompt: str | None = Field(None, description="提示词推荐的系统提示词，留空则用内置默认")
    model_thinking_enabled: bool = Field(False, description="数据提取模型思考模式开关")
    assistant_thinking_enabled: bool = Field(False, description="AI助手模型思考模式开关")
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
    assistant_thinking_enabled: bool = False
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

# 内置默认提示词（与 component_preview.py 中的 _default_meta_prompt 一致，去掉文件名占位符）
_DEFAULT_META_PROMPT = (
    "你是一个工业数据分析助手。请阅读上传文件的实际内容，"
    "并生成一段可直接使用的数据提取提示词，"
    "用于指导另一个大模型从该文件及同类文件中提取结构化数据。\n\n"
    "文件名：{body.filename}\n\n"
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
    "4. 多行表格、粒径分布、元素含量、时间序列、曲线数据、工况数据及成组试验结果均放入 series。\n"
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
    async with session_scope(_get_session_factory()) as session:
        row = await _get_config_row(session)
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
        return AIConfigResponse(
            base_url=row["base_url"],
            api_key_masked=_mask_key(row["api_key"]),
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
        existing = await _get_config_row(session)
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
        if existing is None:
            await session.execute(
                _ai_config_table.insert().values(
                    id=1,
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
            )
        else:
            await session.execute(
                _ai_config_table.update()
                .where(_ai_config_table.c.id == 1)
                .values(
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
            )

    # 返回时用已保存密钥的掩码值（如果是 __use_saved__ 的话）
    masked_key = (
        _mask_key(body.api_key)
        if body.api_key != "__use_saved__"
        else _mask_key(existing["api_key"])
        if existing
        else "***"
    )

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
    async with session_scope(_get_session_factory()) as session:
        existing = await _get_config_row(session)
        if existing is None:
            await session.execute(
                _ai_config_table.insert().values(
                    id=1,
                    base_url="",
                    api_key="",
                    model_name="",
                    enabled=False,
                    meta_prompt=body.meta_prompt,
                    updated_at=now,
                    updated_by=current_user.user_id,
                )
            )
        else:
            await session.execute(
                _ai_config_table.update()
                .where(_ai_config_table.c.id == 1)
                .values(
                    meta_prompt=body.meta_prompt,
                    updated_at=now,
                    updated_by=current_user.user_id,
                )
            )
    return MetaPromptResponse(meta_prompt=body.meta_prompt)


@ai_config_router.get("/meta-prompt", response_model=MetaPromptResponse)
async def get_meta_prompt(
    current_user: ManageUserDep,
) -> MetaPromptResponse:
    """获取提示词推荐的系统提示词。"""
    async with session_scope(_get_session_factory()) as session:
        row = await _get_config_row(session)
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
                row = await _get_config_row(session)
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
    async with session_scope(_get_session_factory()) as session:
        row = await _get_config_row(session)
        if row is None or not row["enabled"]:
            return None
        # H-06: 使用单例 crypto，解密失败直接 raise（不回退明文）
        crypto = EnvelopeCrypto.from_env()
        decrypted_key = crypto.decrypt(row["api_key"])
        return {
            "base_url": row["base_url"],
            "api_key": decrypted_key,
            "model_name": row["model_name"],
            "assistant_model_name": row.get("assistant_model_name") or row["model_name"],
            "research_model_name": row.get("research_model_name") or row["model_name"],
            "meta_prompt": row.get("meta_prompt") or "",
            "model_thinking_enabled": row.get("model_thinking_enabled") or False,
            "assistant_thinking_enabled": row.get("assistant_thinking_enabled") or False,
            "research_thinking_enabled": row.get("research_thinking_enabled") or False,
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
