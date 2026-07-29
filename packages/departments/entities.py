"""IRIP 机构/实验室管理 ORM 模型。

定义两张表（docs/arch-department.md §3.1 / §3.2）：
- department: 实验室/机构主表，code 创建后锁定，软禁用（status='disabled'）；
- app_user_department: 用户-实验室多对多关联表（P1），复合主键 (user_id, department_id)。

风格参考 packages/auth/entities.py：继承 Base，使用 GUID / UTCDateTime 自定义类型，
Mapped[] + mapped_column()，default=new_id。
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class DepartmentStatus(StrEnum):
    """实验室状态枚举。

    Attributes:
        ACTIVE: 启用状态，可正常使用。
        DISABLED: 禁用状态（软禁用），历史数据保留，新数据录入时过滤。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


class Department(Base):
    """实验室/机构实体（对应 department 表）。

    organization_id 不设 FK（V0 约定：organization 表由 bootstrap 创建，不在 Alembic 中）。
    code 创建后锁定不可修改（服务层 UPDATE 语句不写 code 列）。

    Attributes:
        id: 实验室 UUID。
        organization_id: 所属顶层组织 ID。
        code: 实验室编码（组织内唯一，创建后锁定）。
        display_name: 中文显示名。
        description: 描述（可选）。
        status: 状态（active / disabled）。
        sort_order: 排序权重（默认 0）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
        parent_id: 上级部门 ID（nullable，顶级部门为 NULL）。
    """

    __tablename__ = "department"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'active'"))
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("department.id"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"Department(id={self.id!r}, code={self.code!r}, "
            f"display_name={self.display_name!r}, status={self.status!r})"
        )


class AppUserDepartment(Base):
    """用户-实验室关联实体（对应 app_user_department 表，P1）。

    复合主键 (user_id, department_id)。is_primary 由应用层保证唯一性
    （同一 user 最多一条 is_primary = true）。

    Attributes:
        user_id: 用户 ID（PK + FK→app_user.id）。
        department_id: 实验室 ID（PK + FK→department.id）。
        is_primary: 是否主要实验室（默认 false）。
        created_at: 关联创建时间。
    """

    __tablename__ = "app_user_department"

    user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    department_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("department.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"AppUserDepartment(user_id={self.user_id!r}, "
            f"department_id={self.department_id!r}, is_primary={self.is_primary!r})"
        )
