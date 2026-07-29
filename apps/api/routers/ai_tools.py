"""AI 工具管理 REST 端点。

端点（全部需 ``system:manage`` 权限，D-1）：
  GET    /api/v1/ai-tools           — 列出全部工具
  GET    /api/v1/ai-tools/{name}    — 获取工具详情
  POST   /api/v1/ai-tools           — 新建工具
  PATCH  /api/v1/ai-tools/{name}    — 编辑工具声明（乐观锁）
  PATCH  /api/v1/ai-tools/{name}/enabled — 启用/禁用工具（乐观锁）

设计约定（架构设计文档 §3.3 / §4.2 / §4.3 / §7.2 / §7.3）：
- 权限守卫：``require_permission("system:manage")``；
- 乐观锁：PATCH 校验 ``lock_version``，冲突返回 409；
- 审计：写端点调 ``AuditRecorder.record``，action 为
  ``ai_tool.create`` / ``ai_tool.update`` / ``ai_tool.toggle``；
- 不支持删除（D-5）。
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.routers.components import ComponentRegistryServiceDep
from packages.ai.tool_repository import AIToolRow, ToolRepository
from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import session_scope
from packages.common.errors import AppError

#: 路由实例。
ai_tools_router = APIRouter(prefix="/api/v1/ai-tools", tags=["ai-tools"])

#: 需 system:manage 权限的当前用户依赖（D-1）。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("system:manage"))]


# ---- 请求/响应模型（架构设计文档 §3.3） ----


class AIToolDTO(BaseModel):
    """工具 DTO（列表 + 详情共用）。"""

    name: str
    display_name: str
    description: str
    required_permission: str
    candidate: bool
    parameters_schema: dict[str, Any]
    enabled: bool
    lock_version: int
    updated_at: str
    updated_by: str | None = None


class AIToolCreateRequest(BaseModel):
    """新建工具请求。"""

    name: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=64,
        description="工具唯一键，小写字母开头，仅含小写字母/数字/下划线",
    )
    display_name: str = Field(..., max_length=128, description="中文显示名")
    description: str = Field(..., max_length=2000, description="工具描述")
    required_permission: str = Field(..., max_length=64, description="执行此工具所需权限")
    candidate: bool = Field(False, description="是否为候选工具（需审批）")
    parameters_schema: dict[str, Any] = Field(
        default_factory=dict, description="工具参数 JSON Schema"
    )


class AIToolUpdateRequest(BaseModel):
    """编辑工具请求（不含 name，name 不可改）。"""

    display_name: str = Field(..., max_length=128)
    description: str = Field(..., max_length=2000)
    required_permission: str = Field(..., max_length=64)
    candidate: bool
    parameters_schema: dict[str, Any]
    lock_version: int = Field(..., description="乐观锁版本号")


class AIToolToggleRequest(BaseModel):
    """启用/禁用工具请求。"""

    enabled: bool
    lock_version: int = Field(..., description="乐观锁版本号")


class UnifiedToolDTO(BaseModel):
    """统一工具/插件 DTO（AI 工具 + 组件插件汇总）。

    将 ``ai_tool`` 表的工具与 ``component`` 表的已发布组件汇总为统一格式，
    供 AI 工具管理页面统一展示。

    Attributes:
        name: 工具/插件唯一键。
        display_name: 显示名。
        description: 描述。
        source: 数据来源（``"ai_tool"`` 或 ``"component"``）。
        enabled: 是否启用（AI 工具为真实状态；组件为 status==published）。
        status: 状态字符串
            （AI 工具: enabled/disabled；组件: published/deprecated）。
        kind: 类型
            （AI 工具: readonly/candidate；组件: ingestion 等）。
        candidate: 是否为候选工具（仅 AI 工具有意义，组件固定 False）。
        lock_version: 乐观锁版本号（仅 AI 工具有意义）。
        updated_at: 更新时间 ISO 字符串。
        updated_by: 最后修改人（仅 AI 工具有意义）。
        required_permission: 所需权限（仅 AI 工具有意义）。
        parameters_schema: 参数 JSON Schema（仅 AI 工具有意义）。
        version: 组件版本号（仅组件有意义）。
        runtime: 组件运行时类型（仅组件有意义）。
    """

    name: str
    display_name: str
    description: str
    source: str = Field(..., description='"ai_tool" 或 "component"')
    enabled: bool
    status: str = Field(
        ...,
        description="AI 工具: enabled/disabled; 组件: published/deprecated",
    )
    kind: str = Field(..., description="AI 工具: readonly/candidate; 组件: ingestion 等")
    candidate: bool = False
    lock_version: int = 0
    updated_at: str = ""
    updated_by: str | None = None
    required_permission: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    runtime: str = ""
    component_id: str = Field(
        default="", description="组件主表 UUID（仅组件有意义，用于归档/恢复操作）"
    )
    version_id: str = Field(default="", description="组件版本 UUID（仅组件有意义，用于编辑跳转）")


# ---- 辅助函数 ----


def _to_dto(row: AIToolRow) -> AIToolDTO:
    """AIToolRow 领域对象 → AIToolDTO Pydantic 模型。

    Args:
        row: 仓库返回的领域对象。

    Returns:
        AIToolDTO: API 响应模型。
    """
    return AIToolDTO(
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        required_permission=row.required_permission,
        candidate=row.candidate,
        parameters_schema=row.parameters_schema,
        enabled=row.enabled,
        lock_version=row.lock_version,
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        updated_by=str(row.updated_by) if row.updated_by else None,
    )


def _parse_manifest_display_name(manifest_yaml: str) -> str:
    """从 manifest YAML 提取 display_name 字段。

    Args:
        manifest_yaml: 组件清单 YAML 文本。

    Returns:
        str: display_name 值，未找到返回空字符串。
    """
    import re

    match = re.search(
        r'^display_name:\s*["\']?(.*?)["\']?\s*$',
        manifest_yaml,
        re.MULTILINE,
    )
    return match.group(1) if match else ""


def _parse_manifest_description(manifest_yaml: str) -> str:
    """从 manifest YAML 提取 description 字段。

    Args:
        manifest_yaml: 组件清单 YAML 文本。

    Returns:
        str: description 值，未找到返回空字符串。
    """
    import re

    match = re.search(
        r'^description:\s*["\']?(.*?)["\']?\s*$',
        manifest_yaml,
        re.MULTILINE,
    )
    return match.group(1) if match else ""


def _row_to_audit_payload(row: AIToolRow) -> dict[str, Any]:
    """AIToolRow → 审计 payload 中的工具快照（可序列化）。"""
    return {
        "name": row.name,
        "display_name": row.display_name,
        "description": row.description,
        "required_permission": row.required_permission,
        "candidate": row.candidate,
        "parameters_schema": row.parameters_schema,
        "enabled": row.enabled,
        "lock_version": row.lock_version,
    }


async def _record_audit(
    session: Any,
    action: str,
    actor: CurrentUser,
    resource_id: UUID,
    payload: dict[str, Any],
) -> None:
    """记录审计事件（ai_tool.* action）。

    复用 governance.py 的 organization_id 占位约定（V3 简化）。

    Args:
        session: 异步会话（与业务写操作同事务）。
        action: 审计动作（ai_tool.create / ai_tool.update / ai_tool.toggle）。
        actor: 当前操作用户。
        resource_id: 工具 UUID。
        payload: 审计载荷（before/after/diff 等）。
    """
    event = AuditEventData(
        organization_id=actor.user_id,  # V3 简化：暂用 user_id 作 org 占位
        action=action,
        actor_user_id=actor.user_id,
        resource_type="ai_tool",
        resource_id=resource_id,
        payload=payload,
    )
    await AuditRecorder.record(session, event)


# ---- 端点 ----


@ai_tools_router.get("", response_model=list[AIToolDTO])
async def list_ai_tools(
    current_user: ManageUserDep,
) -> list[AIToolDTO]:
    """列出全部 AI 工具（含禁用工具，供管理端展示）。"""
    async with session_scope(_get_session_factory()) as session:
        rows = await ToolRepository.list_all(session)
        return [_to_dto(r) for r in rows]


@ai_tools_router.get("/unified", response_model=list[UnifiedToolDTO])
async def list_unified_tools(
    current_user: ManageUserDep,
    component_service: ComponentRegistryServiceDep,
) -> list[UnifiedToolDTO]:
    """列出统一工具/插件（AI 工具 + 组件插件汇总）。

    汇总两个数据源：
    - AI 工具白名单（``ai_tool`` 表全部工具）；
    - 已发布组件（``component`` 表 kind=ingestion, status=published）。

    组件插件在列表中只读展示，不可编辑/启用禁用。

    Args:
        current_user: 当前操作用户（需 ``system:manage`` 权限）。
        component_service: 组件注册表服务（DI 注入）。

    Returns:
        list[UnifiedToolDTO]: 统一工具列表，按 name 排序。
    """
    # 1. AI 工具（ai_tool 表全部工具）
    ai_tools: list[UnifiedToolDTO] = []
    async with session_scope(_get_session_factory()) as session:
        ai_rows = await ToolRepository.list_all(session)
    for row in ai_rows:
        ai_tools.append(
            UnifiedToolDTO(
                name=row.name,
                display_name=row.display_name,
                description=row.description,
                source="ai_tool",
                enabled=row.enabled,
                status="enabled" if row.enabled else "disabled",
                kind="candidate" if row.candidate else "readonly",
                candidate=row.candidate,
                lock_version=row.lock_version,
                updated_at=row.updated_at.isoformat() if row.updated_at else "",
                updated_by=str(row.updated_by) if row.updated_by else None,
                required_permission=row.required_permission,
                parameters_schema=row.parameters_schema,
                version="",
                runtime="",
            )
        )

    # 2. 组件插件（kind=ingestion，含 published 和 deprecated）
    components = await component_service.list(kind="ingestion")
    component_items: list[UnifiedToolDTO] = []
    for comp, ver in components:
        display_name = _parse_manifest_display_name(ver.manifest_yaml) or comp.name
        description = _parse_manifest_description(ver.manifest_yaml)
        component_items.append(
            UnifiedToolDTO(
                name=comp.name,
                display_name=display_name,
                description=description,
                source="component",
                enabled=comp.status == "published",
                status=comp.status,
                kind=comp.kind,
                candidate=False,
                lock_version=0,
                updated_at=comp.updated_at.isoformat() if comp.updated_at else "",
                updated_by=None,
                required_permission="",
                parameters_schema={},
                version=ver.version,
                runtime=ver.runtime,
                component_id=str(comp.id),
                version_id=str(comp.active_version_id or ver.id),
            )
        )

    # 合并并按 name 排序
    all_tools = ai_tools + component_items
    all_tools.sort(key=lambda t: t.name)
    return all_tools


@ai_tools_router.get("/{name}", response_model=AIToolDTO)
async def get_ai_tool(
    name: str,
    current_user: ManageUserDep,
) -> AIToolDTO:
    """获取单个 AI 工具详情。"""
    async with session_scope(_get_session_factory()) as session:
        row = await ToolRepository.get_by_name(session, name)
        if row is None:
            raise AppError(
                code="not_found",
                message=f"工具 '{name}' 不存在",
                retryable=False,
                fields={"name": name},
            )
        return _to_dto(row)


@ai_tools_router.post("", response_model=AIToolDTO, status_code=201)
async def create_ai_tool(
    body: AIToolCreateRequest,
    current_user: ManageUserDep,
) -> AIToolDTO:
    """新建 AI 工具（仅创建声明层，不含执行逻辑）。

    新建的工具默认 enabled=True、lock_version=0。
    """
    async with session_scope(_get_session_factory()) as session:
        data = body.model_dump()
        row = await ToolRepository.create(session, data, current_user.user_id)
        await _record_audit(
            session,
            action="ai_tool.create",
            actor=current_user,
            resource_id=row.id,
            payload={"after": _row_to_audit_payload(row)},
        )
        return _to_dto(row)


@ai_tools_router.patch("/{name}", response_model=AIToolDTO)
async def update_ai_tool(
    name: str,
    body: AIToolUpdateRequest,
    current_user: ManageUserDep,
) -> AIToolDTO:
    """编辑 AI 工具声明字段（name 不可改，乐观锁校验）。"""
    async with session_scope(_get_session_factory()) as session:
        before = await ToolRepository.get_by_name(session, name)
        if before is None:
            raise AppError(
                code="not_found",
                message=f"工具 '{name}' 不存在",
                retryable=False,
                fields={"name": name},
            )
        data = body.model_dump(exclude={"lock_version"})
        row = await ToolRepository.update(
            session,
            name=name,
            data=data,
            lock_version=body.lock_version,
            updated_by=current_user.user_id,
        )
        diff = _compute_diff(before, row)
        await _record_audit(
            session,
            action="ai_tool.update",
            actor=current_user,
            resource_id=row.id,
            payload={
                "before": _row_to_audit_payload(before),
                "after": _row_to_audit_payload(row),
                "diff": diff,
            },
        )
        return _to_dto(row)


@ai_tools_router.patch("/{name}/enabled", response_model=AIToolDTO)
async def toggle_ai_tool(
    name: str,
    body: AIToolToggleRequest,
    current_user: ManageUserDep,
) -> AIToolDTO:
    """启用/禁用 AI 工具（乐观锁校验）。

    禁用后下次 AI 对话（ask）即不可见、不可调用（D-4 热更新）。
    """
    async with session_scope(_get_session_factory()) as session:
        before = await ToolRepository.get_by_name(session, name)
        if before is None:
            raise AppError(
                code="not_found",
                message=f"工具 '{name}' 不存在",
                retryable=False,
                fields={"name": name},
            )
        row = await ToolRepository.set_enabled(
            session,
            name=name,
            enabled=body.enabled,
            lock_version=body.lock_version,
            updated_by=current_user.user_id,
        )
        await _record_audit(
            session,
            action="ai_tool.toggle",
            actor=current_user,
            resource_id=row.id,
            payload={
                "before": _row_to_audit_payload(before),
                "after": _row_to_audit_payload(row),
                "diff": {"enabled": [before.enabled, row.enabled]},
            },
        )
        return _to_dto(row)


def _compute_diff(before: AIToolRow, after: AIToolRow) -> dict[str, Any]:
    """计算 before/after 的字段差异（供审计 payload）。

    Args:
        before: 更新前的工具行。
        after: 更新后的工具行。

    Returns:
        dict: 变更字段 → [旧值, 新值]。
    """
    diff: dict[str, Any] = {}
    fields_to_compare = [
        "display_name",
        "description",
        "required_permission",
        "candidate",
        "parameters_schema",
    ]
    for field_name in fields_to_compare:
        old_val = getattr(before, field_name)
        new_val = getattr(after, field_name)
        if old_val != new_val:
            diff[field_name] = [old_val, new_val]
    return diff


# ---- DI 占位 ----

_session_factory: Any = None


def set_session_factory(factory: Any) -> None:
    """设置会话工厂（由 composition provider 调用）。"""
    global _session_factory
    _session_factory = factory


def _get_session_factory() -> Any:
    if _session_factory is None:
        raise RuntimeError("Session factory not set. Call set_session_factory() first.")
    return _session_factory
