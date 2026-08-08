"""治理 API 的 Pydantic 请求/响应模型。

从 apps/api/routers/governance.py 提取（P2-C5）。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---- 用户管理 ----


class UserResponse(BaseModel):
    """用户响应体。"""

    id: str
    email: str
    display_name: str
    roles: list[str]
    status: str
    department_id: str | None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """用户分页列表响应。"""

    items: list[UserResponse]
    next_cursor: str | None
    has_more: bool


class AssignRolesRequest(BaseModel):
    """分配角色请求体。"""

    roles: list[str] = Field(..., min_length=1, description="要分配的角色代码列表")


class CreateUserRequest(BaseModel):
    """新建用户请求体。"""

    email: str = Field(..., description="用户邮箱（登录账号）")
    display_name: str = Field(..., description="显示名")
    password: str = Field(..., min_length=6, description="初始密码（至少 6 位）")
    roles: list[str] = Field(..., min_length=1, description="角色代码列表（可多选）")
    department_id: str | None = Field(None, description="所属实验室 ID")


class UpdateUserStatusRequest(BaseModel):
    """启用/禁用用户请求体。"""

    status: str = Field(..., description="目标状态：active 或 disabled")


class UpdateUserRequest(BaseModel):
    """编辑用户请求体（邮箱不可修改）。"""

    display_name: str | None = Field(None, description="显示名")
    password: str | None = Field(None, min_length=6, description="新密码（留空则不修改）")
    roles: list[str] | None = Field(None, min_length=1, description="角色代码列表")
    department_id: str | None = Field(None, description="所属实验室 ID")


# ---- 数据移交 ----


class DataTransferRequest(BaseModel):
    """数据移交请求体。

    Attributes:
        table: 目标表名（必须在白名单中）。
        from_dept_id: 源部门 UUID。
        to_dept_id: 目标部门 UUID。
        dry_run: True 时只返回影响行数，不执行 UPDATE。
    """

    table: str = Field(
        ..., description="目标表名（fact/parameter/model/flow_definition/flow_run/equipment）"
    )
    from_dept_id: str = Field(..., description="源部门 UUID")
    to_dept_id: str = Field(..., description="目标部门 UUID")
    dry_run: bool = Field(False, description="True 时只返回影响行数，不执行")


class DataTransferResponse(BaseModel):
    """数据移交响应体。"""

    table: str
    from_dept_id: str
    to_dept_id: str
    dry_run: bool
    affected_rows: int


# ---- root 部门数据量监控 ----


class RootDataStatsResponse(BaseModel):
    """root 部门数据量统计响应体。"""

    root_department_id: str
    root_department_name: str
    stats: list[dict[str, Any]]
