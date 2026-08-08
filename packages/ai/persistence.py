"""AI 消息持久化服务。

从 ``service.py`` 提取的消息持久化逻辑。
职责：凭据脱敏、用户消息持久化、AI 消息持久化、自动生成对话标题。

依赖注入：
- session_factory: 异步会话工厂
- clock: 时钟依赖
- provider: AI Provider（auto_generate_title 读取 provider 的 API 配置）

注意：
- ``AppUser`` 的 import 保持为函数内延迟 import（2 处）。
- ``SafeHTTPClient`` 的 import 保持为函数内延迟 import（1 处）。
- 方法名去掉 ``_`` 前缀（``_redact_credentials`` → ``redact_credentials`` 等）。
- ``auto_generate_title`` 读取 ``self._provider._api_key/_base_url/_model``，保持不变。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.citation import SignedCitation
from packages.ai.citations import Citation
from packages.ai.entities import AIConversation, AIMessage
from packages.ai.providers import AIResponse
from packages.common.clock import Clock
from packages.common.database import scoped_session
from packages.common.ids import new_id


class MessagePersistence:
    """AI 消息持久化服务。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
        _provider: AI Provider（auto_generate_title 读取 API 配置）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        clock: Clock,
        provider: Any,
    ) -> None:
        """初始化消息持久化服务。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟依赖。
            provider: AI Provider 实例（用于读取 API 配置生成标题）。
        """
        self._factory = session_factory
        self._clock = clock
        self._provider = provider

    def redact_credentials(self, text: str) -> str:
        """凭据脱敏：移除回答中可能出现的密钥模式。

        扫描回答文本，将疑似 API 密钥、Bearer 令牌的模式替换为 [REDACTED]。
        确保 AI 回答不泄露凭据。

        Args:
            text: 原始回答文本。

        Returns:
            str: 脱敏后的回答文本。
        """
        import re

        # 替换 Bearer token 模式
        text = re.sub(r"[Bb]earer\s+[A-Za-z0-9\-_\.]{20,}", "[REDACTED]", text)
        # 替换疑似 API key 模式（sk- 开头或长 hex/base64 串）
        text = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED]", text)
        return text

    async def persist_user_message_only(
        self,
        conversation_id: UUID,
        user_id: UUID,
        question: str,
        mentions: list[str] | None = None,
        sender_display_name: str | None = None,
        sender_avatar_url: str | None = None,
    ) -> None:
        """仅持久化用户消息（不创建 AI 回复消息）。

        irip-ai-collab: 当消息仅 @人（不触发 AI）时调用此方法，
        只保存用户消息和 mentions，不生成 assistant 消息。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID。
            question: 用户问题文本。
            mentions: @ 人的 user_id 字符串数组。
            sender_display_name: 发送者显示名（从 app_user 快照）。
            sender_avatar_url: 发送者头像 URL（从 app_user 快照）。
        """
        now = self._clock.now()
        async with scoped_session(self._factory, None, user_id) as session:
            actual_display_name = sender_display_name
            actual_avatar_url = sender_avatar_url
            try:
                from packages.auth.entities import AppUser

                sender = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
                if sender is not None:
                    actual_display_name = sender.display_name
                    actual_avatar_url = sender.avatar_url
            except Exception:
                pass

            user_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="user",
                content=question,
                tool_calls_json=[],
                citations_json=[],
                uncertainty=None,
                created_at=now,
                mentions=list(mentions) if mentions else [],
                sender_user_id=user_id,
                sender_display_name=actual_display_name,
                sender_avatar_url=actual_avatar_url,
            )
            session.add(user_msg)

            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def persist_messages(
        self,
        conversation_id: UUID,
        user_id: UUID,
        question: str,
        response: AIResponse,
        mentions: list[str] | None = None,
        sender_display_name: str | None = None,
        sender_avatar_url: str | None = None,
    ) -> None:
        """持久化用户消息与 AI 消息到数据库。

        irip-ai-collab: 用户消息填充 mentions + sender_user_id + sender_display_name
        + sender_avatar_url；AI 消息 sender 字段为 None。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID。
            question: 用户问题。
            response: AI 回答。
            mentions: @ 人的 user_id 字符串数组。
            sender_display_name: 发送者显示名（从 app_user 快照）。
            sender_avatar_url: 发送者头像 URL（从 app_user 快照）。
        """
        now = self._clock.now()
        async with scoped_session(self._factory, None, user_id) as session:
            # irip-ai-collab: 从数据库获取发送者 display_name 和 avatar_url 快照
            actual_display_name = sender_display_name
            actual_avatar_url = sender_avatar_url
            try:
                from packages.auth.entities import AppUser

                sender = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
                if sender is not None:
                    actual_display_name = sender.display_name
                    actual_avatar_url = sender.avatar_url
            except Exception:
                pass

            # 用户消息（irip-ai-collab: 填充 mentions + sender 字段）
            user_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="user",
                content=question,
                tool_calls_json=[],
                citations_json=[],
                uncertainty=None,
                created_at=now,
                mentions=list(mentions) if mentions else [],
                sender_user_id=user_id,
                sender_display_name=actual_display_name,
                sender_avatar_url=actual_avatar_url,
            )
            session.add(user_msg)

            # AI 消息 — 持久化审计数据，裁剪大型 inline 数组
            tool_calls_list: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                raw_args = tc.get("args", {})
                # 裁剪大型 inline 数组：只保留摘要信息
                trimmed_args: dict[str, Any] = {}
                if isinstance(raw_args, dict):
                    for k, v in raw_args.items():
                        if isinstance(v, list) and len(v) > 20:
                            trimmed_args[k] = f"[{len(v)} items]"
                        elif isinstance(v, dict):
                            # 检查 variables 里的 inline 数组
                            if k == "variables" and isinstance(v, list):
                                trimmed_vars: list[Any] = []
                                for var in v:
                                    if isinstance(var, dict):
                                        trimmed_var: dict[str, Any] = {}
                                        for vk, vv in var.items():
                                            if isinstance(vv, list) and len(vv) > 20:
                                                trimmed_var[vk] = f"[{len(vv)} items]"
                                            else:
                                                trimmed_var[vk] = vv
                                        trimmed_vars.append(trimmed_var)
                                    else:
                                        trimmed_vars.append(var)
                                trimmed_args[k] = trimmed_vars
                            else:
                                trimmed_args[k] = v
                        else:
                            trimmed_args[k] = v
                tool_calls_list.append(
                    {
                        "tool": tc.get("tool", ""),
                        "args": trimmed_args,
                        "summary": tc.get("summary", ""),
                        "status": tc.get("status", ""),
                        "audit": tc.get("audit"),
                    }
                )
            citations_list: list[dict[str, str]] = []
            for c in response.citations:
                if isinstance(c, Citation):
                    citations_list.append(c.to_dict())
                elif isinstance(c, SignedCitation):
                    citations_list.append(c.to_dict())
                elif isinstance(c, dict):
                    citations_list.append(c)

            ai_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="assistant",
                content=response.answer,
                tool_calls_json=tool_calls_list,
                citations_json=citations_list,
                uncertainty=response.uncertainty,
                created_at=now,
            )
            session.add(ai_msg)

            # 更新对话 updated_at
            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def auto_generate_title(
        self,
        conversation_id: UUID,
        question: str,
        answer: str,
    ) -> None:
        """首次对话后自动生成标题。

        直接用 httpx 调用 LLM API 生成标题（不走 thinking 模式），
        然后更新数据库中的对话标题。失败时静默跳过。

        Args:
            conversation_id: 对话 ID。
            question: 用户问题。
            answer: AI 回答。
        """
        # 从 provider 提取 API 配置
        api_key = getattr(self._provider, "_api_key", None)
        base_url = getattr(self._provider, "_base_url", None)
        model = getattr(self._provider, "_model", None)

        if not api_key or not base_url or not model:
            # 离线模式或其他无 API 配置的 provider，用问题前 30 字做标题
            title = question[:30].strip()
            if not title:
                return
        else:
            # H-05: 使用 SafeHTTPClient（SSRF 防护）
            from packages.common.safe_http import SafeHTTPClient

            try:
                async with SafeHTTPClient(timeout=15.0, max_size=1024 * 1024) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "请用一句话概括以下对话的主题，不超过15个字。"
                                        "直接返回标题文本，不要解释、不要引号。"
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": (
                                        f"用户问题：{question[:500]}\nAI回答：{answer[:500]}"
                                    ),
                                },
                            ],
                            "max_tokens": 200,
                            # 关闭思考模式，避免 token 浪费在思考过程
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    )
                if resp.status_code != 200:
                    return
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return
                msg = choices[0].get("message", {})
                # 优先取 content，回退到 reasoning_content（Qwen3 思考模式）
                title = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            except Exception:
                return

        # 清理标题
        title = title.strip("\"'''「」『』 \n\r\t")  # noqa: B005
        title = title.split("\n")[0].strip()
        if len(title) > 60:
            title = title[:60]
        if not title:
            return

        # 更新数据库
        now = self._clock.now()
        async with scoped_session(self._factory, None, None) as session:
            await session.execute(
                sa.update(AIConversation)
                .values(title=title, updated_at=now)
                .where(AIConversation.id == conversation_id)
            )
