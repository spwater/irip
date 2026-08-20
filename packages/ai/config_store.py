"""AI 大模型配置存储层（从 router 下沉的 ORM 操作）。

封装 ai_config 表定义与全部数据库读写操作，供 router 和其他模块调用。
router 层不再直接 import sqlalchemy。

关键函数：
- get_config_row(session): 读取单行配置（id=1）；
- get_active_ai_config(): 读取已启用配置并解密 API key；
- upsert_config(...): 插入或更新配置行；
- upsert_meta_prompt(...): 单独更新提示词字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import packages.common.database as db_mod
from packages.common.crypto import EnvelopeCrypto
from packages.common.db_types import GUID, UTCDateTime

#: ai_config 表定义（单行设计，id=1）。
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
    sa.Column(
        "model_thinking_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")
    ),
    sa.Column(
        "assistant_thinking_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")
    ),
    sa.Column(
        "research_thinking_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")
    ),
    sa.Column("updated_at", UTCDateTime, server_default=sa.func.now(), nullable=False),
    sa.Column("updated_by", GUID, nullable=True),
    extend_existing=True,
)


async def get_config_row(session: AsyncSession) -> dict[str, Any] | None:
    """读取配置行（id=1）。

    Args:
        session: 数据库异步会话。

    Returns:
        dict | None: 配置行字典，不存在时返回 None。
    """
    result = await session.execute(sa.select(_ai_config_table).where(_ai_config_table.c.id == 1))
    row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)


async def upsert_config(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    base_url: str,
    api_key: str,
    model_name: str,
    assistant_model_name: str = "",
    research_model_name: str = "",
    enabled: bool = True,
    meta_prompt: str | None = None,
    model_thinking_enabled: bool = False,
    assistant_thinking_enabled: bool = True,
    research_thinking_enabled: bool = False,
    updated_at: datetime,
    updated_by: UUID | None = None,
) -> dict[str, Any] | None:
    """插入或更新配置行（id=1）。

    Args:
        session_factory: 异步会话工厂。
        base_url: API 基础地址。
        api_key: 加密后的 API 密钥。
        model_name: 模型名称。
        assistant_model_name: AI 助手模型名称。
        research_model_name: 研发助手模型名称。
        enabled: 是否启用。
        meta_prompt: 系统提示词。
        model_thinking_enabled: 数据提取模型思考模式。
        assistant_thinking_enabled: AI 助手模型思考模式。
        research_thinking_enabled: 研发助手模型思考模式。
        updated_at: 更新时间。
        updated_by: 更新者用户 ID。

    Returns:
        dict | None: 更新前的配置行（用于判断是 insert 还是 update）。
    """
    from packages.common.database import session_scope

    async with session_scope(session_factory) as session:
        existing = await get_config_row(session)
        if existing is None:
            await session.execute(
                _ai_config_table.insert().values(
                    id=1,
                    base_url=base_url,
                    api_key=api_key,
                    model_name=model_name,
                    assistant_model_name=assistant_model_name,
                    research_model_name=research_model_name,
                    enabled=enabled,
                    meta_prompt=meta_prompt,
                    model_thinking_enabled=model_thinking_enabled,
                    assistant_thinking_enabled=assistant_thinking_enabled,
                    research_thinking_enabled=research_thinking_enabled,
                    updated_at=updated_at,
                    updated_by=updated_by,
                )
            )
        else:
            await session.execute(
                _ai_config_table.update()
                .where(_ai_config_table.c.id == 1)
                .values(
                    base_url=base_url,
                    api_key=api_key,
                    model_name=model_name,
                    assistant_model_name=assistant_model_name,
                    research_model_name=research_model_name,
                    enabled=enabled,
                    meta_prompt=meta_prompt,
                    model_thinking_enabled=model_thinking_enabled,
                    assistant_thinking_enabled=assistant_thinking_enabled,
                    research_thinking_enabled=research_thinking_enabled,
                    updated_at=updated_at,
                    updated_by=updated_by,
                )
            )
        return existing


async def upsert_meta_prompt(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    meta_prompt: str,
    updated_at: datetime,
    updated_by: UUID | None = None,
) -> None:
    """单独插入或更新提示词字段。

    Args:
        session_factory: 异步会话工厂。
        meta_prompt: 系统提示词。
        updated_at: 更新时间。
        updated_by: 更新者用户 ID。
    """
    from packages.common.database import session_scope

    async with session_scope(session_factory) as session:
        existing = await get_config_row(session)
        if existing is None:
            await session.execute(
                _ai_config_table.insert().values(
                    id=1,
                    base_url="",
                    api_key="",
                    model_name="",
                    enabled=False,
                    meta_prompt=meta_prompt,
                    updated_at=updated_at,
                    updated_by=updated_by,
                )
            )
        else:
            await session.execute(
                _ai_config_table.update()
                .where(_ai_config_table.c.id == 1)
                .values(
                    meta_prompt=meta_prompt,
                    updated_at=updated_at,
                    updated_by=updated_by,
                )
            )


async def get_active_ai_config(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str] | None:
    """读取已启用的大模型配置（供 AIService 使用）。

    F-12: 读取时解密 API key（envelope encryption）。

    Args:
        session_factory: 异步会话工厂。

    Returns:
        dict | None: 包含 base_url/api_key/model_name 的字典，未配置或未启用时返回 None。
    """
    from packages.common.database import session_scope

    async with session_scope(session_factory) as session:
        row = await get_config_row(session)
        if row is None or not row["enabled"]:
            return None
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


# 保持向后兼容的类型导出
from uuid import UUID  # noqa: E402
