"""AI 工具白名单注册表。

定义 AI 助手可调用的工具集合，分为两类：
1. **白名单工具（只读，8 个）**: 可直接执行，不修改平台数据；
2. **候选工具（需审批，4 个）**: 产生写操作建议，必须经人工审批后才执行。

安全原则：
- 工具名称必须在白名单中，未知工具一律拒绝（防注入）；
- 候选工具标记为 ``candidate=True``，AIService 不会自动执行，
  只返回建议供用户审核；
- 每个工具声明所需权限（``required_permission``），执行前由 AIService
  通过授权服务检查当前用户是否拥有该权限。
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from packages.common.errors import AppError


@dataclass(frozen=True)
class ToolSpec:
    """工具规格（不可变值对象）。

    Attributes:
        name: 工具名称（唯一键，如 ``"search_standards"``）。
        display_name: 中文显示名（如 ``"搜索标准变量"``）。
        description: 工具描述（供 AI 理解工具用途）。
        required_permission: 执行此工具所需的权限字符串（如 ``"standard:read"``）。
        candidate: 是否为候选工具（需审批）。True 表示写操作建议工具，
            False 表示只读工具可直接执行。
        parameters_schema: 工具参数的 JSON Schema 描述（供 AI 生成参数）。
    """

    name: str
    display_name: str
    description: str
    required_permission: str
    candidate: bool = False
    parameters_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDefinition:
    """工具定义（不可变值对象，安全控制层用）。

    与 ``ToolSpec`` 对应，用于 ``validate_invocation`` 安全验证流程。
    ``auto_executable`` 对应 ``ToolSpec.candidate`` 的反值。

    Attributes:
        name: 工具唯一名称。
        description: 工具描述。
        required_permission: 调用此工具所需的权限字符串。
        auto_executable: 是否允许 AI 自动执行。True 表示低风险只读操作，
            False 表示需用户显式确认。
    """

    name: str
    description: str
    required_permission: str
    auto_executable: bool = False


@dataclass
class ToolInvocation:
    """工具调用请求（安全验证用）。

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


#: 8 个白名单工具（只读）。
WHITELIST_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_standards",
        display_name="搜索标准变量",
        description="按编码、名称或别名搜索已发布标准变量。",
        required_permission="standard:read",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="search_facts",
        display_name="搜索事实",
        description="按主体、类型或时间范围搜索实验事实及其最新修订。",
        required_permission="fact:read",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "fact_type": {"type": "string", "description": "事实类型过滤"},
            },
        },
    ),
    ToolSpec(
        name="search_parameters",
        display_name="搜索参数",
        description="按变量编码或状态搜索已发布参数及其版本。",
        required_permission="parameter:read",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "variable_code": {"type": "string", "description": "变量编码"},
            },
        },
    ),
    ToolSpec(
        name="explain_provenance",
        display_name="解释溯源链路",
        description="解释指定参数的溯源链路：事实修订 → 推导运行 → 参数版本。",
        required_permission="provenance:read",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "parameter_id": {"type": "string", "description": "参数 UUID"},
            },
            "required": ["parameter_id"],
        },
    ),
    ToolSpec(
        name="compare_experiments",
        display_name="对比实验",
        description="对比两个或多个实验事实的关键观察值差异。",
        required_permission="fact:read",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "fact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "事实 UUID 列表",
                },
            },
            "required": ["fact_ids"],
        },
    ),
    ToolSpec(
        name="run_published_model",
        display_name="运行已发布模型",
        description="使用已发布模型版本对给定输入执行预测。",
        required_permission="model:predict",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "model_id": {"type": "string", "description": "模型 UUID"},
                "inputs": {"type": "object", "description": "预测输入"},
            },
            "required": ["model_id", "inputs"],
        },
    ),
    ToolSpec(
        name="draft_report",
        display_name="生成报告草稿",
        description="根据给定事实和参数生成结构化报告草稿（只读，不落库）。",
        required_permission="fact:read",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "报告标题"},
                "fact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "引用的事实 UUID 列表",
                },
            },
        },
    ),
    ToolSpec(
        name="extract_data",
        display_name="数据提取",
        description="根据文件路径和提取指令，用大模型从文件中提取结构化数据",
        required_permission="ingestion:write",
        candidate=False,
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "prompt": {"type": "string", "description": "提取指令"},
                "schema": {"type": "array", "description": "目标字段定义"},
            },
            "required": ["path", "prompt", "schema"],
        },
    ),
)

#: 4 个候选工具（需审批，写操作建议）。
CANDIDATE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="suggest_mapping",
        display_name="建议映射",
        description="为原始数据字段建议标准变量映射（候选，需人工审批）。",
        required_permission="ingestion:write",
        candidate=True,
        parameters_schema={
            "type": "object",
            "properties": {
                "source_field": {"type": "string"},
                "target_variable_code": {"type": "string"},
            },
            "required": ["source_field", "target_variable_code"],
        },
    ),
    ToolSpec(
        name="suggest_fact_revision",
        display_name="建议事实修订",
        description="建议对指定事实创建新修订（候选，需人工审批）。",
        required_permission="fact:write",
        candidate=True,
        parameters_schema={
            "type": "object",
            "properties": {
                "fact_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["fact_id", "reason"],
        },
    ),
    ToolSpec(
        name="create_parameter_candidate",
        display_name="创建参数候选",
        description="根据推导结果创建参数候选（候选，需人工审批）。",
        required_permission="parameter:write",
        candidate=True,
        parameters_schema={
            "type": "object",
            "properties": {
                "variable_code": {"type": "string"},
                "value": {"type": "string"},
                "derivation_run_id": {"type": "string"},
            },
            "required": ["variable_code", "value"],
        },
    ),
    ToolSpec(
        name="create_model_publish_request",
        display_name="创建模型发布请求",
        description="为已验证模型版本创建发布请求（候选，需人工审批）。",
        required_permission="model:publish",
        candidate=True,
        parameters_schema={
            "type": "object",
            "properties": {
                "model_id": {"type": "string"},
                "version_id": {"type": "string"},
            },
            "required": ["model_id", "version_id"],
        },
    ),
)

#: 全部工具（白名单 + 候选）。
ALL_TOOLS: tuple[ToolSpec, ...] = WHITELIST_TOOLS + CANDIDATE_TOOLS

#: 白名单工具名称集合（只读，可直接执行）。
WHITELIST_TOOL_NAMES: frozenset[str] = frozenset(
    spec.name for spec in WHITELIST_TOOLS
)

#: 候选工具名称集合（需审批）。
CANDIDATE_TOOL_NAMES: frozenset[str] = frozenset(
    spec.name for spec in CANDIDATE_TOOLS
)

#: 全部合法工具名称集合。
ALL_TOOL_NAMES: frozenset[str] = frozenset(spec.name for spec in ALL_TOOLS)


class ToolRegistry:
    """工具注册表：管理工具规格，验证工具名在白名单中。

    职责：
    1. 注册工具规格（初始化时加载全部白名单 + 候选工具）；
    2. 按名称查找工具规格；
    3. 验证工具名合法性（拒绝未知工具，防注入）；
    4. 标记候选工具（candidate=True 的工具不会被自动执行）。

    Attributes:
        _tools: 工具名 → ToolSpec 映射。
    """

    def __init__(
        self,
        tools: tuple[ToolSpec, ...] = ALL_TOOLS,
    ) -> None:
        """初始化工具注册表。

        Args:
            tools: 工具规格元组，默认加载全部白名单 + 候选工具。
        """
        self._tools: dict[str, ToolSpec] = {}
        for spec in tools:
            self.register(spec)

    def register(self, spec: ToolSpec | ToolDefinition) -> None:
        """注册一个工具规格。

        接受 ``ToolSpec`` 或 ``ToolDefinition``（向后兼容 tool_registry.py）。
        ``ToolDefinition`` 会被转换为 ``ToolSpec``：``auto_executable`` 的反值
        映射为 ``candidate``。

        Args:
            spec: 工具规格（ToolSpec 或 ToolDefinition）。

        Raises:
            AppError: code="validation_failed"，当工具名重复注册时。
        """
        # ToolDefinition 向后兼容：转换为 ToolSpec
        if isinstance(spec, ToolDefinition):
            spec = ToolSpec(
                name=spec.name,
                display_name=spec.name,
                description=spec.description,
                required_permission=spec.required_permission,
                candidate=not spec.auto_executable,
                parameters_schema={},
            )
        if spec.name in self._tools:
            raise AppError(
                code="validation_failed",
                message=f"工具 '{spec.name}' 已注册",
                retryable=False,
                fields={"tool": spec.name},
            )
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        """按名称获取工具规格。

        Args:
            name: 工具名称。

        Returns:
            ToolSpec: 工具规格。

        Raises:
            AppError: code="validation_failed"，当工具名不在注册表中时。
        """
        spec = self._tools.get(name)
        if spec is None:
            raise AppError(
                code="validation_failed",
                message=f"未知工具 '{name}'，不在白名单中",
                retryable=False,
                fields={"tool": name},
            )
        return spec

    def validate(self, name: str) -> ToolSpec:
        """验证工具名合法性（拒绝未知工具）。

        与 ``get`` 相同，但语义上强调"拒绝未知工具"的安全检查。

        Args:
            name: 工具名称。

        Returns:
            ToolSpec: 工具规格。

        Raises:
            AppError: code="validation_failed"，当工具名不在注册表中时。
        """
        return self.get(name)

    def is_candidate(self, name: str) -> bool:
        """检查工具是否为候选工具（需审批）。

        Args:
            name: 工具名称。

        Returns:
            bool: 候选工具返回 True，只读工具返回 False。
            未知工具也返回 False（应先通过 validate 检查）。
        """
        spec = self._tools.get(name)
        if spec is None:
            return False
        return spec.candidate

    def is_whitelist(self, name: str) -> bool:
        """检查工具是否为白名单工具（只读，可直接执行）。

        Args:
            name: 工具名称。

        Returns:
            bool: 白名单工具返回 True。
        """
        spec = self._tools.get(name)
        if spec is None:
            return False
        return not spec.candidate

    def list_tools(self) -> list[ToolSpec]:
        """列出全部已注册工具。"""
        return list(self._tools.values())

    def list_whitelist_tools(self) -> list[ToolSpec]:
        """列出白名单工具（只读）。"""
        return [s for s in self._tools.values() if not s.candidate]

    def list_candidate_tools(self) -> list[ToolSpec]:
        """列出现选工具（需审批）。"""
        return [s for s in self._tools.values() if s.candidate]

    def names(self) -> tuple[str, ...]:
        """返回全部工具名称元组。"""
        return tuple(self._tools.keys())

    def to_definitions(self) -> list[ToolDefinition]:
        """将全部 ToolSpec 转为 ToolDefinition（安全验证用）。

        Returns:
            list[ToolDefinition]: 工具定义列表。
        """
        return [
            ToolDefinition(
                name=s.name,
                description=s.description,
                required_permission=s.required_permission,
                auto_executable=not s.candidate,
            )
            for s in self._tools.values()
        ]

    def validate_invocation(self, invocation: ToolInvocation) -> ToolDefinition:
        """验证工具调用请求（四道安全防线）。

        1. 未知工具拒绝：注册表外的工具名一律拒绝；
        2. 候选工具确认：``auto_executable=False`` 的工具需用户确认；
        3. 权限检查：基于用户角色检查所需权限；
        4. 全部通过 → 返回 ToolDefinition。

        Args:
            invocation: 工具调用请求。

        Returns:
            ToolDefinition: 验证通过的工具定义。

        Raises:
            AppError: code="unknown_tool"，当工具名不在注册表中。
            AppError: code="confirmation_required"，当工具需确认但未确认。
            AppError: code="forbidden"，当用户无所需权限。
        """
        spec = self._tools.get(invocation.tool_name)
        if spec is None:
            raise AppError(
                code="unknown_tool",
                message=f"未知工具: {invocation.tool_name}",
                retryable=False,
                fields={"tool_name": invocation.tool_name},
            )

        # 候选工具（非 auto_executable）需用户确认
        if spec.candidate and not invocation.confirmed:
            raise AppError(
                code="confirmation_required",
                message=(
                    f"工具 {invocation.tool_name} 需要用户确认后才能执行"
                ),
                retryable=False,
                fields={"tool_name": invocation.tool_name},
            )

        # 权限检查（基于用户角色）
        from packages.auth.permissions import has_role_permission

        has_permission: bool = any(
            has_role_permission(role, spec.required_permission)
            for role in invocation.user_roles
        )
        if not has_permission:
            raise AppError(
                code="forbidden",
                message=(
                    f"用户无权执行工具 {invocation.tool_name}，"
                    f"需要权限: {spec.required_permission}"
                ),
                retryable=False,
                fields={"required_permission": spec.required_permission},
            )

        return ToolDefinition(
            name=spec.name,
            description=spec.description,
            required_permission=spec.required_permission,
            auto_executable=not spec.candidate,
        )

    @staticmethod
    def redact_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        """脱敏工具参数中的秘密。

        复用 ``packages.audit.redaction.redact`` 实现，
        将 password / token / secret / api_key 等字段的值替换为 ``[REDACTED]``。

        Args:
            parameters: 原始参数字典。

        Returns:
            dict[str, Any]: 脱敏后的参数字典（新对象，不修改原始字典）。
        """
        from packages.audit.redaction import redact

        return redact(parameters)
