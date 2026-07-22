"""AI 工具注册表：安全控制层。

为 AI 助手的工具调用提供四道安全防线
（docs/arch-v0.md §7.5 AI 工具安全约定）：

1. **未知工具拒绝**：注册表外的工具名一律拒绝执行；
2. **候选工具确认**：``auto_executable=False`` 的工具需用户显式确认
   后才能执行，防止 AI 自动调用高危操作；
3. **参数脱敏**：工具参数中匹配脱敏字段名（password / token / secret /
   api_key / refresh_token / access_token）的值替换为 ``[REDACTED]``，
   防止秘密泄露到日志或审计记录；
4. **权限隔离**：每个工具声明 ``required_permission``，调用前基于用户角色
   检查权限，越权操作被拒绝。

设计意图：
- AI 工具继承用户权限，不能越权（与 docs/arch-v0.md §3.2 类图对齐）；
- 工具注册表为白名单模式，未注册的工具不可调用；
- 脱敏复用 ``packages.audit.redaction.redact`` 实现。
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from packages.audit.redaction import redact
from packages.auth.permissions import has_role_permission
from packages.common.errors import AppError


@dataclass(frozen=True)
class ToolDefinition:
    """工具定义（不可变值对象）。

    Attributes:
        name: 工具唯一名称（如 ``"fact.search"``）。
        description: 工具描述（供 AI 理解工具用途）。
        required_permission: 调用此工具所需的权限字符串
            （如 ``"fact:read"``，基于 BUILTIN_ROLES 权限矩阵检查）。
        auto_executable: 是否允许 AI 自动执行。``True`` 表示低风险操作
            （如只读查询），AI 可直接调用；``False`` 表示需用户显式确认。
    """

    name: str
    description: str
    required_permission: str
    auto_executable: bool = False


@dataclass
class ToolInvocation:
    """工具调用请求。

    Attributes:
        tool_name: 要调用的工具名称。
        parameters: 调用参数字典（可能含秘密，需脱敏）。
        user_id: 调用者用户 UUID。
        user_roles: 调用者角色代码列表（用于权限检查）。
        confirmed: 用户是否已显式确认此次调用。
    """

    tool_name: str
    parameters: dict[str, Any]
    user_id: UUID
    user_roles: list[str]
    confirmed: bool = False


class ToolRegistry:
    """AI 工具注册表（白名单模式）。

    安全控制流程（``validate_invocation``）：
    1. 查注册表 → 未知工具名 → ``AppError(unknown_tool)``；
    2. 非自动执行且未确认 → ``AppError(confirmation_required)``；
    3. 权限检查 → 越权 → ``AppError(forbidden)``；
    4. 全部通过 → 返回 ToolDefinition。

    用法::

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="fact.search",
            description="搜索实验事实",
            required_permission="fact:read",
            auto_executable=True,
        ))
        invocation = ToolInvocation(
            tool_name="fact.search",
            parameters={"query": "粒度"},
            user_id=user_id,
            user_roles=["researcher"],
            confirmed=True,
        )
        tool = registry.validate_invocation(invocation)
        safe_params = registry.redact_parameters(invocation.parameters)
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """注册工具定义。

        Args:
            tool: 工具定义（name 唯一，重复注册覆盖旧定义）。
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """按名称获取工具定义。

        Args:
            name: 工具名称。

        Returns:
            ToolDefinition | None: 找到返回定义，否则 None。
        """
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """列出所有已注册工具。

        Returns:
            list[ToolDefinition]: 已注册工具定义列表。
        """
        return list(self._tools.values())

    def is_registered(self, name: str) -> bool:
        """检查工具是否已注册。

        Args:
            name: 工具名称。

        Returns:
            bool: 已注册返回 True。
        """
        return name in self._tools

    def validate_invocation(self, invocation: ToolInvocation) -> ToolDefinition:
        """验证工具调用请求（四道安全防线）。

        Args:
            invocation: 工具调用请求。

        Returns:
            ToolDefinition: 验证通过的工具定义。

        Raises:
            AppError: code="unknown_tool"，当工具名不在注册表中。
            AppError: code="confirmation_required"，当工具需确认但未确认。
            AppError: code="forbidden"，当用户无所需权限。
        """
        # 1. 未知工具拒绝
        tool = self._tools.get(invocation.tool_name)
        if tool is None:
            raise AppError(
                code="unknown_tool",
                message=f"未知工具: {invocation.tool_name}",
                retryable=False,
                fields={"tool_name": invocation.tool_name},
            )

        # 2. 候选工具确认
        if not tool.auto_executable and not invocation.confirmed:
            raise AppError(
                code="confirmation_required",
                message=(
                    f"工具 {invocation.tool_name} 需要用户确认后才能执行"
                ),
                retryable=False,
                fields={"tool_name": invocation.tool_name},
            )

        # 3. 权限检查（基于用户角色）
        has_permission: bool = any(
            has_role_permission(role, tool.required_permission)
            for role in invocation.user_roles
        )
        if not has_permission:
            raise AppError(
                code="forbidden",
                message=(
                    f"用户无权执行工具 {invocation.tool_name}，"
                    f"需要权限: {tool.required_permission}"
                ),
                retryable=False,
                fields={"required_permission": tool.required_permission},
            )

        return tool

    @staticmethod
    def redact_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        """脱敏工具参数中的秘密。

        复用 ``packages.audit.redaction.redact`` 实现，
        将 password / token / secret / api_key / refresh_token /
        access_token 等字段的值替换为 ``[REDACTED]``。

        Args:
            parameters: 原始参数字典。

        Returns:
            dict[str, Any]: 脱敏后的参数字典（新对象，不修改原始字典）。
        """
        return redact(parameters)
