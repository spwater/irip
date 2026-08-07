"""实验项目管理路由：创建、列表、详情、编辑、状态切换。

端点：
  POST   /api/v1/experiment-projects                  — 创建项目（experiment_project:manage）
  GET    /api/v1/experiment-projects                  — 分页列表（experiment_project:read）
  GET    /api/v1/experiment-projects/{id}             — 详情含 task_count（experiment_project:read）
  PATCH  /api/v1/experiment-projects/{id}             — 编辑名称/描述（experiment_project:manage）
  PATCH  /api/v1/experiment-projects/{id}/status      — 归档/恢复（experiment_project:manage）

安全约定：
- 创建/编辑/状态切换需 require_permission("experiment_project:manage")；
- 列表/详情需 require_permission("experiment_project:read")；
- code 创建后锁定：UpdateProjectBody 不含 code 字段；
- 乐观锁：编辑/状态切换请求必须携带 lock_version。

风格参考 apps/api/routers/equipment.py。
"""

from datetime import datetime
from typing import Any, Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.experiment_project.service import ExperimentProjectService

#: 路由实例。
experiment_projects_router = APIRouter(
    prefix="/api/v1/experiment-projects", tags=["experiment-projects"]
)

#: 需 experiment_project:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("experiment_project:manage"))]

#: 需 experiment_project:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("experiment_project:read"))]


def get_experiment_project_service() -> ExperimentProjectService:
    """获取 ExperimentProjectService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 department_id）。
    """
    raise NotImplementedError(
        "get_experiment_project_service must be overridden via dependency_overrides"
    )


#: ExperimentProjectService 依赖类型别名。
ExperimentProjectServiceDep = Annotated[
    ExperimentProjectService, Depends(get_experiment_project_service)
]


# ---- 请求模型 ----


class CreateProjectBody(BaseModel):
    """创建实验项目请求。"""

    department_id: str = Field(..., description="所属部门 UUID")
    code: str | None = Field(None, max_length=200, description="项目编码（留空则自动生成）")
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    visible_departments: list[str] = Field(default_factory=list, description="可见单位 UUID 列表")
    owner_user_id: str = Field(..., description="项目负责人 UUID")


class UpdateProjectBody(BaseModel):
    """编辑实验项目请求（code 不可修改，不在请求体中）。"""

    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    visible_departments: list[str] | None = Field(
        None, description="新可见单位 UUID 列表（None 表示不修改）"
    )
    owner_user_id: str | None = Field(None, description="项目负责人 UUID（None 表示不修改）")
    lock_version: int = Field(..., ge=0)


class UpdateProjectStatusBody(BaseModel):
    """归档/恢复实验项目请求。"""

    status: Literal["active", "archived"]
    lock_version: int = Field(..., ge=0)


# ---- 响应模型 ----


class ExperimentProjectResponse(BaseModel):
    """实验项目详情响应。"""

    id: str
    department_id: str
    code: str
    display_name: str
    description: str | None
    status: str
    visible_departments: list[str]
    visibility_scope: str
    owner_user_id: str
    owner_display_name: str | None = None
    created_at: datetime
    updated_at: datetime
    lock_version: int


class ExperimentProjectListItem(BaseModel):
    """实验项目列表项（含部门名 + 任务数 + 数据数 + 负责人）。"""

    id: str
    code: str
    display_name: str
    description: str | None
    department_id: str
    department_name: str
    visible_departments: list[str]
    status: str
    task_count: int
    owner_display_name: str | None = None
    fact_count: int = 0
    created_at: datetime


class ExperimentProjectListResponse(BaseModel):
    """实验项目分页列表响应。"""

    items: list[ExperimentProjectListItem]
    next_cursor: str | None
    has_more: bool


class ExperimentProjectDetailResponse(BaseModel):
    """实验项目详情响应（含任务统计）。"""

    id: str
    department_id: str
    code: str
    display_name: str
    description: str | None
    status: str
    visible_departments: list[str]
    visibility_scope: str
    owner_user_id: str
    owner_display_name: str | None = None
    task_count: int
    fact_count: int = 0
    created_at: datetime
    updated_at: datetime
    lock_version: int


# ---- 辅助函数 ----


def _to_response(project: object) -> ExperimentProjectResponse:
    """将 ExperimentProject ORM 实体转换为响应模型。"""
    return ExperimentProjectResponse(
        id=str(project.id),
        department_id=str(project.department_id),
        code=project.code,
        display_name=project.display_name,
        description=project.description,
        status=project.status,
        visible_departments=list(getattr(project, "visible_departments", []) or []),
        visibility_scope=project.visibility_scope,
        owner_user_id=str(project.owner_user_id),
        owner_display_name=getattr(project, "_owner_display_name", None),
        created_at=project.created_at,
        updated_at=project.updated_at,
        lock_version=project.lock_version,
    )


async def _check_ownership(
    current_user: CurrentUser,
    project_department_id: UUID | None,
    project_owner_user_id: UUID | None,
    service: ExperimentProjectServiceDep,
) -> None:
    """检查当前用户是否可以编辑/归档项目（所有者+上级模型）。

    权限规则：
    - 数据所有者可管理自己的数据；
    - 上级部门可管理下级部门的数据（单向向下，不含本部门）；
    - 同部门非所有者不可管理他人的数据；
    - 平台管理员不受限制。
    """
    from apps.api.dependencies.dept_scope import check_management_permission

    await check_management_permission(
        current_user=current_user,
        entity_department_id=project_department_id,
        entity_owner_user_id=project_owner_user_id,
        session_factory=service.session_factory,
    )


# ---- 端点 ----


@experiment_projects_router.post("", response_model=ExperimentProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectBody,
    current_user: ManageUserDep,
    service: ExperimentProjectServiceDep,
) -> ExperimentProjectResponse:
    """创建实验项目。

    code 创建后锁定不可修改。编码在部门内唯一。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 experiment_project:manage 权限）。
        service: 项目服务。

    Returns:
        ExperimentProjectResponse: 新创建的项目（201 Created）。

    Raises:
        AppError: code="conflict"，当编码已存在时。
    """
    project = await service.create(
        department_id=UUID(body.department_id),
        code=body.code,
        display_name=body.display_name,
        description=body.description,
        visible_departments=body.visible_departments,
        owner_user_id=UUID(body.owner_user_id),
    )
    return _to_response(project)


@experiment_projects_router.get("", response_model=ExperimentProjectListResponse)
async def list_projects(
    current_user: ReadUserDep,
    service: ExperimentProjectServiceDep,
    status: str | None = Query(None, description="状态筛选"),
    department_id: str | None = Query(None, description="部门 ID 筛选"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> ExperimentProjectListResponse:
    """分页查询项目列表（含部门名 + 任务统计）。

    Args:
        current_user: 当前认证用户（需 experiment_project:read 权限）。
        service: 项目服务。
        status: 状态筛选（active / archived）。
        department_id: 部门 ID 筛选。
        cursor: 分页游标。
        limit: 每页数量。

    Returns:
        ExperimentProjectListResponse: 分页列表。
    """
    # RLS 自动处理可见性，不再需要应用层部门过滤
    dept_id = UUID(department_id) if department_id is not None else None
    visible_dept_id = None
    result = await service.list(
        department_id=dept_id,
        visible_dept_id=visible_dept_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    items = [
        ExperimentProjectListItem(
            id=str(project.id),
            code=project.code,
            display_name=project.display_name,
            description=project.description,
            department_id=str(project.department_id),
            department_name=dept_name,
            visible_departments=list(getattr(project, "visible_departments", []) or []),
            status=project.status,
            task_count=task_count,
            owner_display_name=owner_name,
            fact_count=fact_count,
            created_at=project.created_at,
        )
        for project, dept_name, task_count, owner_name, fact_count in result.items
    ]
    return ExperimentProjectListResponse(
        items=items,
        next_cursor=result.next_cursor,
        has_more=result.has_more,
    )


@experiment_projects_router.get("/{project_id}", response_model=ExperimentProjectDetailResponse)
async def get_project(
    project_id: UUID,
    current_user: ReadUserDep,
    service: ExperimentProjectServiceDep,
) -> ExperimentProjectDetailResponse:
    """查询单个项目详情（含任务统计）。

    Args:
        project_id: 项目 UUID。
        current_user: 当前认证用户（需 experiment_project:read 权限）。
        service: 项目服务。

    Returns:
        ExperimentProjectDetailResponse: 项目详情 + 任务数。

    Raises:
        AppError: code="not_found"，当项目不存在时。
    """
    project, task_count, fact_count = await service.get_with_stats(project_id)
    # 查负责人 display_name
    owner_display_name: str | None = None
    async with service._scoped_session() as session:  # noqa: SLF001
        from packages.auth.entities import AppUser

        owner = await session.scalar(
            sa.select(AppUser.display_name).where(AppUser.id == project.owner_user_id)
        )
        owner_display_name = owner
    return ExperimentProjectDetailResponse(
        id=str(project.id),
        department_id=str(project.department_id),
        code=project.code,
        display_name=project.display_name,
        description=project.description,
        status=project.status,
        visible_departments=list(getattr(project, "visible_departments", []) or []),
        visibility_scope=project.visibility_scope,
        owner_user_id=str(project.owner_user_id),
        owner_display_name=owner_display_name,
        task_count=task_count,
        fact_count=fact_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
        lock_version=project.lock_version,
    )


@experiment_projects_router.patch("/{project_id}", response_model=ExperimentProjectResponse)
async def update_project(
    project_id: UUID,
    body: UpdateProjectBody,
    current_user: ManageUserDep,
    service: ExperimentProjectServiceDep,
) -> ExperimentProjectResponse:
    """编辑项目（code 不可修改）。

    Args:
        project_id: 项目 UUID。
        body: 编辑请求体（不含 code）。
        current_user: 当前认证用户（需 experiment_project:manage 权限）。
        service: 项目服务。

    Returns:
        ExperimentProjectResponse: 更新后的项目。

    Raises:
        AppError: code="not_found"，当项目不存在时。
        AppError: code="conflict"，当 lock_version 不匹配时。
    """
    # 先查询当前项目以获取 department_id 和 owner_user_id
    existing = await service.get(project_id)
    # 归属检查：所有者+上级模型
    await _check_ownership(current_user, existing.department_id, existing.owner_user_id, service)

    project = await service.update(
        project_id=project_id,
        display_name=body.display_name,
        description=body.description,
        lock_version=body.lock_version,
        visible_departments=body.visible_departments,
        owner_user_id=UUID(body.owner_user_id) if body.owner_user_id else None,
    )
    return _to_response(project)


@experiment_projects_router.patch("/{project_id}/status", response_model=ExperimentProjectResponse)
async def update_project_status(
    project_id: UUID,
    body: UpdateProjectStatusBody,
    current_user: ManageUserDep,
    service: ExperimentProjectServiceDep,
) -> ExperimentProjectResponse:
    """归档/恢复项目。

    Args:
        project_id: 项目 UUID。
        body: 状态切换请求体。
        current_user: 当前认证用户（需 experiment_project:manage 权限）。
        service: 项目服务。

    Returns:
        ExperimentProjectResponse: 更新后的项目。

    Raises:
        AppError: code="not_found"，当项目不存在时。
        AppError: code="conflict"，当 lock_version 不匹配时。
    """
    # 归属检查：所有者+上级模型
    project = await service.get(project_id)
    await _check_ownership(current_user, project.department_id, project.owner_user_id, service)

    updated = await service.set_status(
        project_id=project_id,
        status=body.status,
        lock_version=body.lock_version,
    )
    return _to_response(updated)


@experiment_projects_router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    current_user: ManageUserDep,
    service: ExperimentProjectServiceDep,
) -> None:
    """删除项目（仅允许删除已归档且无任务的项目）。

    Args:
        project_id: 项目 UUID。
        current_user: 当前认证用户（需 experiment_project:manage 权限）。
        service: 项目服务。

    Raises:
        AppError: code="conflict"，当项目未归档或仍有任务时。
    """
    project = await service.get(project_id)
    await _check_ownership(current_user, project.department_id, project.owner_user_id, service)
    await service.delete(project_id)
