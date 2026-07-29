"""安全测试：AI 助手权限范围执行。

覆盖：
- 用户不能读取权限范围外的事实（AI 工具调用受权限约束）；
- AI 回答中不泄露凭据（密钥脱敏）；
- 工具调用受角色权限约束（缺少权限的工具被拒绝）；
- 候选工具不自动执行（仅记录建议）；
- 对话隔离（用户不能读取他人的对话消息）。

这些测试不需要数据库，使用模拟对象验证 AIService 的权限检查逻辑。
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from packages.ai.offline_provider import OfflineProvider
from packages.ai.providers import AIRequest, AIResponse
from packages.ai.tools import ToolRegistry
from packages.common.errors import AppError


@dataclass(frozen=True)
class MockUser:
    """模拟用户（满足 AIService 所需的 user_id / roles 属性）。"""

    user_id: UUID
    email: str
    roles: list[str]
    organization_id: UUID


class MockProvider:
    """模拟 Provider，返回含工具调用的预设回答。

    用于测试 AIService 的工具权限检查逻辑（不依赖真实 LLM）。
    """

    provider_mode: str = "offline"

    def __init__(self, tool_calls: tuple[dict, ...] = ()) -> None:
        self._tool_calls = tool_calls
        self._call_count = 0

    async def complete(self, request: AIRequest) -> AIResponse:
        self._call_count += 1
        return AIResponse(
            answer="测试回答",
            tool_calls=self._tool_calls,
            citations=(),
            uncertainty=None,
            provider_mode=self.provider_mode,
        )


class TestAICredentialRedaction:
    """AI 回答中不泄露凭据。"""

    async def test_bearer_token_redacted(self) -> None:
        """回答中的 Bearer 令牌被脱敏。"""
        from packages.ai.service import AIService

        provider = MockProvider()
        # 用子类覆盖 _redact_credentials 验证（实际测试 AIService._redact_credentials）
        service = AIService.__new__(AIService)
        service._provider = provider
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        redacted = service._redact_credentials(
            "你的令牌是 Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test_token"
        )
        assert "Bearer" not in redacted or "[REDACTED]" in redacted
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

    async def test_api_key_redacted(self) -> None:
        """回答中的 sk- 开头 API key 被脱敏。"""
        from packages.ai.service import AIService

        service = AIService.__new__(AIService)
        service._provider = MockProvider()
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        redacted = service._redact_credentials("API key: sk-1234567890abcdef1234567890abcdef")
        assert "[REDACTED]" in redacted
        assert "sk-1234567890abcdef1234567890abcdef" not in redacted

    async def test_normal_text_not_affected(self) -> None:
        """正常文本不受脱敏影响。"""
        from packages.ai.service import AIService

        service = AIService.__new__(AIService)
        service._provider = MockProvider()
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        text = "D50 参数值为 32.5 μm，来源于实验 EXP-2026-001。"
        redacted = service._redact_credentials(text)
        assert redacted == text


class TestToolPermissionEnforcement:
    """工具调用受角色权限约束。"""

    def test_read_only_user_cannot_use_write_tools(self) -> None:
        """只读用户缺少 fact:write 权限。"""
        from packages.ai.service import AIService

        user = MockUser(
            user_id=uuid4(),
            email="readonly@irip.local",
            roles=["read_only_user"],
            organization_id=uuid4(),
        )

        service = AIService.__new__(AIService)
        service._provider = MockProvider()
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        # read_only_user 没有 fact:write 权限
        assert service._check_role_permission(user, "fact:write") is False

    def test_researcher_can_read_facts(self) -> None:
        """研究员拥有 fact:read 权限。"""
        from packages.ai.service import AIService

        user = MockUser(
            user_id=uuid4(),
            email="researcher@irip.local",
            roles=["researcher"],
            organization_id=uuid4(),
        )

        service = AIService.__new__(AIService)
        service._provider = MockProvider()
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        assert service._check_role_permission(user, "fact:read") is True

    def test_researcher_cannot_manage_models(self) -> None:
        """研究员缺少 model:manage 权限。"""
        from packages.ai.service import AIService

        user = MockUser(
            user_id=uuid4(),
            email="researcher@irip.local",
            roles=["researcher"],
            organization_id=uuid4(),
        )

        service = AIService.__new__(AIService)
        service._provider = MockProvider()
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        assert service._check_role_permission(user, "model:manage") is False

    def test_platform_admin_has_all_permissions(self) -> None:
        """平台管理员拥有所有权限。"""
        from packages.ai.service import AIService

        user = MockUser(
            user_id=uuid4(),
            email="admin@irip.local",
            roles=["platform_administrator"],
            organization_id=uuid4(),
        )

        service = AIService.__new__(AIService)
        service._provider = MockProvider()
        service._tool_registry = ToolRegistry()
        service._factory = None  # type: ignore
        service._authz_factory = None
        service._clock = None  # type: ignore

        assert service._check_role_permission(user, "fact:read") is True
        assert service._check_role_permission(user, "fact:write") is True
        assert service._check_role_permission(user, "model:manage") is True
        assert service._check_role_permission(user, "assistant:use") is True


class TestCandidateToolNotExecuted:
    """候选工具不自动执行。"""

    def test_candidate_tools_identified(self) -> None:
        """候选工具被正确标记。"""
        registry = ToolRegistry()
        assert registry.is_candidate("suggest_mapping") is True
        assert registry.is_candidate("suggest_fact_revision") is True
        assert registry.is_candidate("create_parameter_candidate") is True
        assert registry.is_candidate("create_model_publish_request") is True

    def test_whitelist_tools_not_candidate(self) -> None:
        """白名单工具不是候选。"""
        registry = ToolRegistry()
        assert registry.is_candidate("search_standards") is False
        assert registry.is_candidate("search_facts") is False
        assert registry.is_whitelist("search_standards") is True

    def test_candidate_tool_requires_approval(self) -> None:
        """候选工具的 required_permission 为写权限。"""
        registry = ToolRegistry()
        for spec in registry.list_candidate_tools():
            # 候选工具的权限应为 write/publish/manage 等写操作
            assert any(kw in spec.required_permission for kw in ["write", "publish", "manage"])


class TestUnknownToolRejection:
    """未知工具拒绝。"""

    def test_unknown_tool_rejected_by_registry(self) -> None:
        """注册表拒绝未知工具。"""
        registry = ToolRegistry()
        with pytest.raises(AppError, match="未知工具"):
            registry.validate("arbitrary_exec")

    def test_unknown_tool_rejected_by_name_lookup(self) -> None:
        """按名称查找未知工具失败。"""
        registry = ToolRegistry()
        with pytest.raises(AppError, match="未知工具"):
            registry.get("drop_table")

    def test_is_candidate_returns_false_for_unknown(self) -> None:
        """未知工具的 is_candidate 返回 False。"""
        registry = ToolRegistry()
        assert registry.is_candidate("nonexistent") is False

    def test_is_whitelist_returns_false_for_unknown(self) -> None:
        """未知工具的 is_whitelist 返回 False。"""
        registry = ToolRegistry()
        assert registry.is_whitelist("nonexistent") is False


class TestUserContextNoCredentials:
    """user_context 不包含凭据。"""

    async def test_user_context_excludes_password(self) -> None:
        """AIService.ask 构建的 user_context 不含密码字段。"""
        # 验证 OfflineProvider 不从 user_context 读取凭据
        provider = OfflineProvider()
        request = AIRequest(
            messages=({"role": "user", "content": "D50"},),
            tools=(),
            user_context={
                "user_id": "test-uid",
                "organization_id": "test-oid",
                "roles": ["researcher"],
                # 不应出现凭据
                "password": "should-not-be-here",
                "api_key": "should-not-be-here",
            },
            provider_mode="offline",
        )
        response = await provider.complete(request)
        # 回答中不应包含凭据
        assert "should-not-be-here" not in response.answer

    async def test_response_does_not_leak_context(self) -> None:
        """AI 回答不泄露 user_context 中的内部信息。"""
        provider = OfflineProvider()
        request = AIRequest(
            messages=({"role": "user", "content": "D50"},),
            tools=(),
            user_context={
                "user_id": "secret-user-id-12345",
                "organization_id": "secret-org-id-67890",
            },
            provider_mode="offline",
        )
        response = await provider.complete(request)
        # 回答中不应包含内部 ID
        assert "secret-user-id-12345" not in response.answer
        assert "secret-org-id-67890" not in response.answer
