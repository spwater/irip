"""组件管理路由：发布 / 列表 / 详情。

端点（IRIP V2-T01）：
  POST   /api/v1/components              — 发布组件版本（component:manage）
  GET    /api/v1/components              — 列表（component:read）
  GET    /api/v1/components/{component_id} — 详情（component:read）

安全约定：
- 发布需 require_permission("component:manage")；
- 列表/详情需 require_permission("component:read")。

DI 约定（与 V1 standards 路由一致）：
- get_component_registry_service() 抛 NotImplementedError，
  生产环境通过 dependency_overrides 注入按请求构造的实例。
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.dependencies.dept_scope import (
    get_visible_department_ids,
    should_filter_by_department,
)
from packages.common.errors import AppError
from packages.components.manifest import ManifestValidator
from packages.components.registry import ComponentRegistryService

#: JSON Schema 路径（相对项目根目录）。
_SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "component-manifest"
    / "v1.schema.json"
)

#: 路由实例。
components_router = APIRouter(
    prefix="/api/v1/components", tags=["components"]
)

#: 需 component:manage 权限的当前用户依赖。
ManageUserDep = Annotated[
    CurrentUser, Depends(require_permission("component:manage"))
]

#: 需 component:read 权限的当前用户依赖。
ReadUserDep = Annotated[
    CurrentUser, Depends(require_permission("component:read"))
]


def get_component_registry_service() -> ComponentRegistryService:
    """获取 ComponentRegistryService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError(
        "get_component_registry_service must be overridden "
        "via dependency_overrides"
    )


#: ComponentRegistryService 依赖类型别名。
ComponentRegistryServiceDep = Annotated[
    ComponentRegistryService, Depends(get_component_registry_service)
]


# ---- 请求模型 ----


class PublishComponentRequest(BaseModel):
    """发布组件请求。"""

    manifest_yaml: str = Field(
        ...,
        min_length=1,
        max_length=200000,
        description="组件清单 YAML 文本",
    )
    experimental_object_code: str | None = Field(
        None,
        description="关联实验对象编码（独立字段，不再从 YAML 解析）",
    )


# ---- 响应模型 ----


class ComponentVersionResponse(BaseModel):
    """组件版本响应（发布端点返回）。"""

    id: str
    name: str
    display_name: str
    version: str
    kind: str
    runtime: str
    engine: str
    status: str
    manifest_sha256: str
    published_at: datetime | None
    created_at: datetime


class ComponentListItemResponse(BaseModel):
    """组件列表项响应。"""

    id: str
    name: str
    display_name: str
    description: str
    version: str
    kind: str
    runtime: str
    engine: str
    experimental_object_code: str
    status: str
    manifest_sha256: str
    published_at: datetime | None
    created_at: datetime


class ComponentListResponse(BaseModel):
    """组件列表响应。"""

    items: list[ComponentListItemResponse]


class ComponentDetailResponse(BaseModel):
    """组件详情响应（含 manifest_yaml 全文）。"""

    id: str
    name: str
    display_name: str
    version: str
    kind: str
    runtime: str
    status: str
    experimental_object_code: str | None = None
    manifest_sha256: str
    manifest_yaml: str
    published_at: datetime | None
    created_at: datetime


def _detect_engine(manifest_yaml: str) -> str:
    """从 manifest YAML 判断组件引擎类型。

    LLM 驱动的组件 parameters 里必然包含 prompt 参数。
    返回 "llm" 或 "code"。
    """
    import re

    if re.search(r"^\s+prompt:\s*$", manifest_yaml, re.MULTILINE):
        return "llm"
    return "code"


def _parse_display_name(manifest_yaml: str) -> str:
    """从 manifest YAML 提取 display_name 字段。"""
    import re

    match = re.search(r'^display_name:\s*["\']?(.*?)["\']?\s*$', manifest_yaml, re.MULTILINE)
    return match.group(1) if match else ""


def _parse_description(manifest_yaml: str) -> str:
    """从 manifest YAML 提取 description 字段。"""
    import re

    match = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', manifest_yaml, re.MULTILINE)
    return match.group(1) if match else ""


def _parse_experimental_object_code(manifest_yaml: str) -> str:
    """从 manifest YAML 提取 experimental_object_code 参数的默认值。"""
    import re

    # 匹配 experimental_object_code: 下面的 default: "xxx"
    match = re.search(
        r'^\s+experimental_object_code:\s*\n\s*type:\s*string.*?\n\s*default:\s*["\']?(.*?)["\']?\s*$',
        manifest_yaml,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


# ---- 端点 ----


@components_router.post(
    "/",
    response_model=ComponentVersionResponse,
    status_code=201,
)
async def publish_component(
    body: PublishComponentRequest,
    current_user: ManageUserDep,
    service: ComponentRegistryServiceDep,
) -> ComponentVersionResponse:
    """发布组件版本。

    校验清单 YAML → 发布到注册表 → 返回版本信息。

    Args:
        body: 发布请求（含 manifest_yaml）。
        current_user: 当前认证用户（需 component:manage 权限）。
        service: 组件注册表服务。

    Returns:
        ComponentVersionResponse: 新发布的版本信息（201 Created）。

    Raises:
        AppError: code="invalid_manifest"，当清单校验失败。
        AppError: code="conflict"，当版本已存在或 kind 不一致。
    """
    validator = ManifestValidator(_SCHEMA_PATH)
    manifest = validator.validate(body.manifest_yaml)

    # 优先用请求体的 experimental_object_code，fallback 到 YAML 解析
    exp_code = body.experimental_object_code or _parse_experimental_object_code(body.manifest_yaml)
    if exp_code:
        import sqlalchemy as sa
        from packages.common.database import session_scope
        from packages.standards.objects import IndustrialObject
        async with session_scope(service.session_factory) as sess:
            obj = await sess.scalar(
                sa.select(IndustrialObject).where(
                    IndustrialObject.code == exp_code,
                    IndustrialObject.organization_id == service.organization_id,
                )
            )
            if obj is None:
                raise AppError(
                    code="validation_failed",
                    message=f"实验对象编码不存在: {exp_code}",
                    retryable=False,
                    fields={"experimental_object_code": exp_code},
                )

    version = await service.publish(manifest, experimental_object_code=exp_code or None)

    return ComponentVersionResponse(
        id=str(version.id),
        name=manifest.name,
        display_name=manifest.display_name,
        version=version.version,
        kind=manifest.kind,
        runtime=version.runtime,
        engine=_detect_engine(version.manifest_yaml),
        status=version.status,
        manifest_sha256=version.manifest_sha256,
        published_at=version.published_at,
        created_at=version.created_at,
    )


@components_router.get(
    "/", response_model=ComponentListResponse
)
async def list_components(
    current_user: ReadUserDep,
    service: ComponentRegistryServiceDep,
    kind: str | None = Query(None, description="按类别过滤"),
    status: str | None = Query(None, description="按状态过滤"),
) -> ComponentListResponse:
    """列表查询组件。

    Args:
        current_user: 当前认证用户（需 component:read 权限）。
        service: 组件注册表服务。
        kind: 可选，按类别过滤
            （ingestion/transform/quality/statistics/output/model）。
        status: 可选，按组件状态过滤
            （draft/published/deprecated）。

    Returns:
        ComponentListResponse: 组件列表。
    """
    items = await service.list(kind=kind, status=status)

    # 部门级数据隔离：非管理员用户只能看到自己实验室及后代实验室的数据接口。
    # 数据接口通过 experimental_object_code → industrial_object.code →
    # industrial_object.department_id 间接关联到所属部门。
    if should_filter_by_department(current_user):
        visible_dept_ids = await get_visible_department_ids(
            current_user, service.session_factory
        )
        if visible_dept_ids:
            # 查出可见部门内的实验对象 code 列表
            import sqlalchemy as sa
            from packages.common.database import session_scope
            from packages.standards.objects import IndustrialObject

            async with session_scope(service.session_factory) as session:
                visible_codes_result = await session.execute(
                    sa.select(IndustrialObject.code).where(
                        IndustrialObject.department_id.in_(visible_dept_ids)
                    )
                )
                visible_codes = {
                    row[0] for row in visible_codes_result.fetchall()
                }

            # 过滤 items，只保留 experimental_object_code 在 visible_codes 内的。
            # experimental_object_code 为 NULL 的组件不在可见范围内，不显示。
            items = [
                (comp, ver) for comp, ver in items
                if ver.experimental_object_code in visible_codes
            ]
        else:
            # 无实验室用户（非管理员且 department_id 为 NULL）：看不到任何数据接口
            items = []

    return ComponentListResponse(
        items=[
            ComponentListItemResponse(
                id=str(ver.id),
                name=comp.name,
                display_name=_parse_display_name(ver.manifest_yaml),
                description=_parse_description(ver.manifest_yaml),
                version=ver.version,
                kind=comp.kind,
                runtime=ver.runtime,
                engine=_detect_engine(ver.manifest_yaml),
                experimental_object_code=ver.experimental_object_code or _parse_experimental_object_code(ver.manifest_yaml),
                status=comp.status,
                manifest_sha256=ver.manifest_sha256,
                published_at=ver.published_at,
                created_at=ver.created_at,
            )
            for comp, ver in items
        ]
    )


@components_router.get(
    "/{component_id}", response_model=ComponentDetailResponse
)
async def get_component(
    component_id: UUID,
    current_user: ReadUserDep,
    service: ComponentRegistryServiceDep,
) -> ComponentDetailResponse:
    """获取组件版本详情。

    Args:
        component_id: 组件版本 UUID。
        current_user: 当前认证用户（需 component:read 权限）。
        service: 组件注册表服务。

    Returns:
        ComponentDetailResponse: 组件详情（含 manifest_yaml 全文）。

    Raises:
        AppError: code="not_found"，当版本不存在。
    """
    comp, ver = await service.get_version_by_id(component_id)
    return ComponentDetailResponse(
        id=str(ver.id),
        name=comp.name,
        display_name=_parse_display_name(ver.manifest_yaml),
        version=ver.version,
        kind=comp.kind,
        runtime=ver.runtime,
        status=comp.status,
        experimental_object_code=ver.experimental_object_code or _parse_experimental_object_code(ver.manifest_yaml),
        manifest_sha256=ver.manifest_sha256,
        manifest_yaml=ver.manifest_yaml,
        published_at=ver.published_at,
        created_at=ver.created_at,
    )


class ComponentVersionListItem(BaseModel):
    """组件版本列表项响应。"""

    id: str
    version: str
    status: str
    manifest_sha256: str
    created_at: datetime


@components_router.get(
    "/{component_id}/versions",
    response_model=list[ComponentVersionListItem],
)
async def list_component_versions(
    component_id: UUID,
    current_user: ReadUserDep,
    service: ComponentRegistryServiceDep,
) -> list[ComponentVersionListItem]:
    """列出指定组件的所有版本（按创建时间降序）。

    Args:
        component_id: 组件版本 UUID（通过 get_version_by_id 获取主记录 ID，
                       然后列出同组件的所有版本）。
        current_user: 当前认证用户（需 component:read 权限）。
        service: 组件注册表服务。

    Returns:
        list[ComponentVersionListItem]: 版本列表。
    """
    comp, ver = await service.get_version_by_id(component_id)
    versions = await service.list_versions(comp.id)
    return [
        ComponentVersionListItem(
            id=str(v.id),
            version=v.version,
            status=v.status,
            manifest_sha256=v.manifest_sha256,
            created_at=v.created_at,
        )
        for v in versions
    ]


# ---- 端点：归档 / 恢复 / 删除 ----


@components_router.patch("/{component_id}/archive")
async def archive_component(
    component_id: UUID,
    current_user: ManageUserDep,
    service: ComponentRegistryServiceDep,
) -> dict[str, str]:
    """归档组件（status → deprecated）。

    Args:
        component_id: 组件版本 UUID（通过 get_version_by_id 获取主记录）。
        current_user: 当前认证用户（需 component:manage 权限）。
        service: 组件注册表服务。

    Returns:
        dict: {"status": "deprecated"}
    """
    comp, _ = await service.get_version_by_id(component_id)
    await service.deprecate(comp.name)
    return {"status": "deprecated"}


@components_router.patch("/{component_id}/restore")
async def restore_component(
    component_id: UUID,
    current_user: ManageUserDep,
    service: ComponentRegistryServiceDep,
) -> dict[str, str]:
    """恢复组件（deprecated → published）。

    Args:
        component_id: 组件版本 UUID。
        current_user: 当前认证用户（需 component:manage 权限）。
        service: 组件注册表服务。

    Returns:
        dict: {"status": "published"}
    """
    comp, _ = await service.get_version_by_id(component_id)
    await service.restore(comp.name)
    return {"status": "published"}


@components_router.post("/{component_id}/activate")
async def activate_version(
    component_id: UUID,
    current_user: ManageUserDep,
    service: ComponentRegistryServiceDep,
) -> dict[str, str]:
    """切换组件的当前活跃版本（回滚）。

    Args:
        component_id: 要激活的组件版本 UUID。
        current_user: 当前认证用户（需 component:manage 权限）。
        service: 组件注册表服务。

    Returns:
        dict: {"status": "activated"}
    """
    await service.activate_version(component_id)
    return {"status": "activated"}


@components_router.delete("/{component_id}")
async def delete_component(
    component_id: UUID,
    current_user: ManageUserDep,
    service: ComponentRegistryServiceDep,
) -> dict[str, str]:
    """删除组件及其所有版本。

    Args:
        component_id: 组件版本 UUID（通过 get_version_by_id 获取主记录）。
        current_user: 当前认证用户（需 component:manage 权限）。
        service: 组件注册表服务。

    Returns:
        dict: {"status": "deleted"}
    """
    comp, _ = await service.get_version_by_id(component_id)
    await service.delete_component(comp.id)
    return {"status": "deleted"}
