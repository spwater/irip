"""AI 工具白名单注册表。

定义 AI 助手可调用的工具集合，分为两类：
1. **白名单工具（只读，8 个）**: 可直接执行，不修改平台数据；
2. **候选工具（需审批，4 个）**: 产生写操作建议，必须经人工审批后才执行。

安全原则：
- 工具名称必须在白名单中，未知工具一律拒绝（防注入）；
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
        parameters_schema: 工具参数的 JSON Schema 描述（供 AI 生成参数）。
    """

    name: str
    display_name: str
    description: str
    required_permission: str
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    category: str = "ai_tool"


@dataclass(frozen=True)
class ToolDefinition:
    """工具定义（不可变值对象，安全控制层用）。

    与 ``ToolSpec`` 对应，用于 ``validate_invocation`` 安全验证流程。

    Attributes:
        name: 工具唯一名称。
        description: 工具描述。
        required_permission: 调用此工具所需的权限字符串。
    """

    name: str
    description: str
    required_permission: str


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

#: 3 个候选工具（需审批，写操作建议）。
CANDIDATE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="suggest_mapping",
        display_name="建议映射",
        description="为原始数据字段建议标准变量映射（候选，需人工审批）。",
        required_permission="ingestion:write",
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
        name="create_parameter_candidate",
        display_name="创建参数候选",
        description="根据推导结果创建参数候选（候选，需人工审批）。",
        required_permission="parameter:write",
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

#: 插件工具（专门编写的解析器，只读，可编辑描述）。
PLUGIN_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="xrd_converter",
        display_name="XRD 解析器",
        description="解析 XRD RAS/RAW 文件，提取衍射数据（metadata/points/series）。"
        "支持 Rigaku 等仪器的原始数据格式，输出结构化 JSON。",
        required_permission="",
        parameters_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "XRD RAS/RAW 文件路径（artifact: 前缀或本地路径）",
                },
            },
            "required": ["file_path"],
        },
        category="ingestion",
    ),
    ToolSpec(
        name="llm_converter",
        display_name="大模型解析器",
        description="用于大模型对数据的解析。",
        required_permission="",
        parameters_schema={},
        category="ingestion",
    ),
)

#: 全部工具（AI 工具 + 插件）。
ALL_TOOLS: tuple[ToolSpec, ...] = WHITELIST_TOOLS + CANDIDATE_TOOLS + PLUGIN_TOOLS

#: AI 工具名称集合。
AI_TOOL_NAMES: frozenset[str] = frozenset(spec.name for spec in WHITELIST_TOOLS + CANDIDATE_TOOLS)

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

        初始状态下全部已注册工具均为启用（``_enabled`` 包含全部 name）。
        ``reload_from_db`` 后 ``_enabled`` 由数据库 ``enabled`` 列决定。

        Args:
            tools: 工具规格元组，默认加载全部白名单 + 候选工具。
        """
        self._tools: dict[str, ToolSpec] = {}
        self._enabled: set[str] = set()
        for spec in tools:
            self.register(spec)
        # 初始状态：全部已注册工具默认启用（reload_from_db 前的兜底）
        self._enabled = set(self._tools.keys())

    def register(self, spec: ToolSpec | ToolDefinition) -> None:
        """注册一个工具规格。

        接受 ``ToolSpec`` 或 ``ToolDefinition``（向后兼容 tool_registry.py）。

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
        """验证工具名合法性（拒绝未知工具 + 禁用工具）。

        与 ``get`` 不同：``validate`` 对禁用工具也抛 ``unknown_tool``
        （D-3），实现"禁用后 AI 不可见、不可调用"。``get`` 仍可返回
        禁用工具的 ToolSpec（供管理 API 列出）。

        Args:
            name: 工具名称。

        Returns:
            ToolSpec: 工具规格。

        Raises:
            AppError: code="unknown_tool"，当工具名不在注册表中或已被禁用时。
        """
        spec = self._tools.get(name)
        if spec is None:
            raise AppError(
                code="unknown_tool",
                message=f"未知工具: {name}",
                retryable=False,
                fields={"tool_name": name},
            )
        if name not in self._enabled:
            raise AppError(
                code="unknown_tool",
                message=f"工具 '{name}' 已被禁用，不可调用",
                retryable=False,
                fields={"tool_name": name},
            )
        return spec

    def list_tools(self) -> list[ToolSpec]:
        """列出全部已注册工具（含禁用工具，供管理 API 使用）。"""
        return list(self._tools.values())

    def is_registered(self, name: str) -> bool:
        """检查工具名是否已注册。

        Args:
            name: 工具名称。

        Returns:
            bool: 已注册返回 True，未注册返回 False。
        """
        return name in self._tools

    def list_enabled_tools(self) -> list[ToolSpec]:
        """列出全部已启用工具（禁用工具不包含，供 AI 工具 schema 构建）。

        Returns:
            list[ToolSpec]: 已启用工具规格列表。
        """
        return [s for n, s in self._tools.items() if n in self._enabled]

    def names(self) -> tuple[str, ...]:
        """返回全部已启用工具名称元组（D-3：禁用工具不包含）。

        Returns:
            tuple[str, ...]: 已启用工具名称元组。
        """
        return tuple(self._enabled)

    def enabled_names(self) -> tuple[str, ...]:
        """返回全部已启用工具名称元组（``names`` 的显式别名）。

        Returns:
            tuple[str, ...]: 已启用工具名称元组。
        """
        return tuple(self._enabled)

    async def reload_from_db(self, session: Any) -> None:
        """从数据库全量重新加载工具声明，重建 ``_tools`` 与 ``_enabled``。

        热更新入口（D-4）：在 ``AIService.ask`` 入口处调用，每次问答
        从 DB 重新加载工具声明层。reload 为全量替换：
        - ``_tools`` 字典清空后重建（name → ToolSpec）；
        - ``_enabled`` 集合重新计算（仅含 ``enabled=True`` 的工具）；
        - 禁用工具在 ``_tools`` 中保留 ToolSpec（供管理 API 通过 ``get``
          返回），但 ``_enabled`` 不含其 name。

        Args:
            session: 异步数据库会话（由调用方管理事务）。
        """
        from packages.ai.tool_repository import ToolRepository

        rows = await ToolRepository.list_all(session)
        self._tools = {}
        self._enabled = set()
        for row in rows:
            spec = ToolSpec(
                name=row.name,
                display_name=row.display_name,
                description=row.description,
                required_permission=row.required_permission,
                parameters_schema=row.parameters_schema,
                category=row.category,
            )
            self._tools[row.name] = spec
            if row.enabled:
                self._enabled.add(row.name)

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
            )
            for s in self._tools.values()
        ]

    def validate_invocation(self, invocation: ToolInvocation) -> ToolDefinition:
        """验证工具调用请求（三道安全防线）。

        1. 未知工具拒绝：注册表外的工具名一律拒绝；
        2. 权限检查：基于用户角色检查所需权限；
        3. 全部通过 → 返回 ToolDefinition。

        Args:
            invocation: 工具调用请求。

        Returns:
            ToolDefinition: 验证通过的工具定义。

        Raises:
            AppError: code="unknown_tool"，当工具名不在注册表中。
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

        # 权限检查（基于用户角色）
        from packages.auth.permissions import has_role_permission

        has_permission: bool = any(
            has_role_permission(role, spec.required_permission) for role in invocation.user_roles
        )
        if not has_permission:
            raise AppError(
                code="forbidden",
                message=(
                    f"用户无权执行工具 {invocation.tool_name}，需要权限: {spec.required_permission}"
                ),
                retryable=False,
                fields={"required_permission": spec.required_permission},
            )

        return ToolDefinition(
            name=spec.name,
            description=spec.description,
            required_permission=spec.required_permission,
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
