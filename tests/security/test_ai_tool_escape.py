"""安全测试：AI 工具逃逸防护。

覆盖（docs/arch-v0.md §3.2 类图 AI 工具 + §7.5 AI 工具安全约定）：
- AI 工具注册表拒绝未知工具名（白名单模式）；
- 候选工具不能自动执行（需用户显式确认）；
- 工具参数中的秘密被脱敏（password/token/secret 等）；
- 用户权限范围外的操作被拒绝（基于角色权限矩阵）。

安全设计：
- ToolRegistry 为白名单模式，未注册的工具不可调用；
- auto_executable=False 的工具需 confirmed=True 才能执行；
- 参数脱敏复用 packages.audit.redaction.redact；
- 权限检查基于 packages.auth.permissions.BUILTIN_ROLES。
"""

from uuid import uuid4

import pytest

from packages.ai.tool_registry import (
    ToolDefinition,
    ToolInvocation,
    ToolRegistry,
)
from packages.common.errors import AppError

# ============================================================
# 测试用工具定义
# ============================================================


def _build_registry() -> ToolRegistry:
    """构建测试用工具注册表。"""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fact.search",
            description="搜索实验事实",
            required_permission="fact:read",
            auto_executable=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="fact.delete",
            description="删除实验事实",
            required_permission="fact:write",
            auto_executable=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="model.predict",
            description="模型预测",
            required_permission="model:predict",
            auto_executable=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="parameter.publish",
            description="发布参数",
            required_permission="parameter:publish",
            auto_executable=False,
        )
    )
    return registry


# ============================================================
# 1. AI 工具注册表拒绝未知工具名
# ============================================================


class TestUnknownToolRejection:
    """未知工具名一律拒绝执行（白名单模式）。"""

    def test_unknown_tool_rejected(self) -> None:
        """未注册的工具名 → AppError(unknown_tool)。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="system.shell",
            parameters={"command": "rm -rf /"},
            user_id=uuid4(),
            user_roles=["platform_administrator"],
            confirmed=True,
        )
        with pytest.raises(AppError, match="未知工具"):
            registry.validate_invocation(invocation)

    def test_empty_tool_name_rejected(self) -> None:
        """空工具名 → AppError(unknown_tool)。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="",
            parameters={},
            user_id=uuid4(),
            user_roles=["platform_administrator"],
            confirmed=True,
        )
        with pytest.raises(AppError, match="未知工具"):
            registry.validate_invocation(invocation)

    def test_typosquat_tool_name_rejected(self) -> None:
        """近似工具名（拼写攻击）→ 拒绝。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.search ",  # 末尾多空格
            parameters={},
            user_id=uuid4(),
            user_roles=["researcher"],
            confirmed=True,
        )
        with pytest.raises(AppError, match="未知工具"):
            registry.validate_invocation(invocation)

    def test_registered_tool_accepted(self) -> None:
        """已注册工具名 → 验证通过。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.search",
            parameters={"query": "粒度"},
            user_id=uuid4(),
            user_roles=["researcher"],
            confirmed=True,
        )
        tool = registry.validate_invocation(invocation)
        assert tool.name == "fact.search"

    def test_is_registered_checks(self) -> None:
        """is_registered 正确区分已注册与未注册工具。"""
        registry = _build_registry()
        assert registry.is_registered("fact.search")
        assert registry.is_registered("fact.delete")
        assert not registry.is_registered("system.shell")
        assert not registry.is_registered("")


# ============================================================
# 2. 候选工具不能自动执行
# ============================================================


class TestAutoExecutionPrevention:
    """非自动执行工具需用户显式确认。"""

    def test_non_auto_tool_without_confirmation_rejected(self) -> None:
        """auto_executable=False 且 confirmed=False → AppError(confirmation_required)。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.delete",
            parameters={"fact_id": "abc"},
            user_id=uuid4(),
            user_roles=["data_steward"],
            confirmed=False,
        )
        with pytest.raises(AppError, match="需要用户确认"):
            registry.validate_invocation(invocation)

    def test_non_auto_tool_with_confirmation_accepted(self) -> None:
        """auto_executable=False 且 confirmed=True → 验证通过。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.delete",
            parameters={"fact_id": "abc"},
            user_id=uuid4(),
            user_roles=["data_steward"],
            confirmed=True,
        )
        tool = registry.validate_invocation(invocation)
        assert tool.name == "fact.delete"

    def test_auto_tool_without_confirmation_accepted(self) -> None:
        """auto_executable=True 且 confirmed=False → 验证通过。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.search",
            parameters={"query": "粒度"},
            user_id=uuid4(),
            user_roles=["researcher"],
            confirmed=False,
        )
        tool = registry.validate_invocation(invocation)
        assert tool.auto_executable is True

    def test_publish_tool_requires_confirmation(self) -> None:
        """parameter.publish 工具需确认。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="parameter.publish",
            parameters={"parameter_id": "xyz"},
            user_id=uuid4(),
            user_roles=["reviewer"],
            confirmed=False,
        )
        with pytest.raises(AppError, match="需要用户确认"):
            registry.validate_invocation(invocation)


# ============================================================
# 3. 工具参数中的秘密被脱敏
# ============================================================


class TestParameterRedaction:
    """工具参数中的秘密被脱敏。"""

    def test_password_redacted(self) -> None:
        """password 字段被替换为 [REDACTED]。"""
        params = {"password": "secret123", "query": "test"}
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["password"] == "[REDACTED]"
        assert redacted["query"] == "test"

    def test_token_redacted(self) -> None:
        """token 字段被替换为 [REDACTED]。"""
        params = {"access_token": "jwt-token-123", "data": [1, 2, 3]}
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["access_token"] == "[REDACTED]"
        assert redacted["data"] == [1, 2, 3]

    def test_secret_redacted(self) -> None:
        """secret 字段被替换为 [REDACTED]。"""
        params = {"secret": "abc", "name": "test"}
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["secret"] == "[REDACTED]"
        assert redacted["name"] == "test"

    def test_api_key_redacted(self) -> None:
        """api_key 字段被替换为 [REDACTED]。"""
        params = {"api_key": "sk-12345", "endpoint": "/v1/search"}
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["endpoint"] == "/v1/search"

    def test_refresh_token_redacted(self) -> None:
        """refresh_token 字段被替换为 [REDACTED]。"""
        params = {"refresh_token": "rt-abc", "user_id": "123"}
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["refresh_token"] == "[REDACTED]"
        assert redacted["user_id"] == "123"

    def test_nested_secret_redacted(self) -> None:
        """嵌套字典中的秘密也被脱敏。"""
        params = {
            "config": {
                "password": "nested-secret",
                "host": "localhost",
            },
            "query": "test",
        }
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["config"]["password"] == "[REDACTED]"
        assert redacted["config"]["host"] == "localhost"
        assert redacted["query"] == "test"

    def test_case_insensitive_redaction(self) -> None:
        """脱敏字段名大小写不敏感。"""
        params = {"Password": "secret", "API_KEY": "key123", "Name": "test"}
        redacted = ToolRegistry.redact_parameters(params)
        assert redacted["Password"] == "[REDACTED]"
        assert redacted["API_KEY"] == "[REDACTED]"
        assert redacted["Name"] == "test"

    def test_original_parameters_not_modified(self) -> None:
        """脱敏不修改原始参数字典。"""
        params = {"password": "secret", "data": "test"}
        original_password = params["password"]
        _redacted = ToolRegistry.redact_parameters(params)
        assert params["password"] == original_password


# ============================================================
# 4. 用户权限范围外的操作被拒绝
# ============================================================


class TestPermissionScopeEnforcement:
    """用户权限范围外的工具操作被拒绝。"""

    def test_researcher_cannot_delete_fact(self) -> None:
        """researcher 无 fact:write 权限 → 不能调用 fact.delete。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.delete",
            parameters={"fact_id": "abc"},
            user_id=uuid4(),
            user_roles=["researcher"],
            confirmed=True,
        )
        with pytest.raises(AppError, match="无权"):
            registry.validate_invocation(invocation)

    def test_read_only_user_cannot_predict(self) -> None:
        """read_only_user 无 model:predict 权限 → 不能调用 model.predict。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="model.predict",
            parameters={"input": [1, 2, 3]},
            user_id=uuid4(),
            user_roles=["read_only_user"],
            confirmed=True,
        )
        with pytest.raises(AppError, match="无权"):
            registry.validate_invocation(invocation)

    def test_researcher_can_search_facts(self) -> None:
        """researcher 有 fact:read 权限 → 可以调用 fact.search。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.search",
            parameters={"query": "粒度"},
            user_id=uuid4(),
            user_roles=["researcher"],
            confirmed=False,  # auto_executable=True
        )
        tool = registry.validate_invocation(invocation)
        assert tool.name == "fact.search"

    def test_data_steward_can_delete_fact(self) -> None:
        """data_steward 有 fact:write 权限 → 可以调用 fact.delete（需确认）。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.delete",
            parameters={"fact_id": "abc"},
            user_id=uuid4(),
            user_roles=["data_steward"],
            confirmed=True,
        )
        tool = registry.validate_invocation(invocation)
        assert tool.name == "fact.delete"

    def test_model_engineer_can_predict(self) -> None:
        """model_engineer 有 model:predict 权限 → 可以调用 model.predict。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="model.predict",
            parameters={"input": [1, 2, 3]},
            user_id=uuid4(),
            user_roles=["model_engineer"],
            confirmed=False,  # auto_executable=True
        )
        tool = registry.validate_invocation(invocation)
        assert tool.name == "model.predict"

    def test_no_roles_user_denied(self) -> None:
        """无角色的用户 → 所有工具调用被拒绝。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.search",
            parameters={"query": "test"},
            user_id=uuid4(),
            user_roles=[],
            confirmed=True,
        )
        with pytest.raises(AppError, match="无权"):
            registry.validate_invocation(invocation)

    def test_unknown_role_denied(self) -> None:
        """未知角色 → 无权限。"""
        registry = _build_registry()
        invocation = ToolInvocation(
            tool_name="fact.search",
            parameters={"query": "test"},
            user_id=uuid4(),
            user_roles=["superadmin"],
            confirmed=True,
        )
        with pytest.raises(AppError, match="无权"):
            registry.validate_invocation(invocation)

    def test_permission_checked_before_execution(self) -> None:
        """权限检查先于执行（确认检查之后）。"""
        registry = _build_registry()
        # 需确认但未确认 + 无权限 → 先报确认错误
        invocation = ToolInvocation(
            tool_name="fact.delete",
            parameters={},
            user_id=uuid4(),
            user_roles=["read_only_user"],
            confirmed=False,
        )
        with pytest.raises(AppError, match="需要用户确认"):
            registry.validate_invocation(invocation)

        # 需确认且已确认 + 无权限 → 报权限错误
        invocation.confirmed = True
        with pytest.raises(AppError, match="无权"):
            registry.validate_invocation(invocation)
