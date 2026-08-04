"""实验项目管理 ORM 模型。

定义实验项目主表 experiment_project：
- code 在部门内唯一（UNIQUE 约束 (department_id, code)）；
- 部门归属 + A 类多租户 4 列（department_id / visible_departments /
  visibility_scope / owner_user_id），与 equipment 完全一致；
- status 为 active / archived 两态，归档后项目内任务只读。

风格参考 packages/equipment/entities.py：继承 Base，
使用 GUID / UTCDateTime 自定义类型，Mapped[] + mapped_column()。
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class ExperimentProjectStatus(StrEnum):
    """实验项目状态枚举。

    Attributes:
        ACTIVE: 活跃状态，可正常创建任务。
        ARCHIVED: 归档状态，项目内任务只读、不可新建任务。
    """

    ACTIVE = "active"
    ARCHIVED = "archived"


class ExperimentProject(Base):
    """实验项目实体（对应 experiment_project 表）。

    code 在部门内唯一（UNIQUE 约束 (department_id, code)）。
    department_id 关联 department 表，owner_user_id 关联 app_user 表。
    code 创建后锁定不可修改（服务层 UPDATE 语句不写 code 列）。

    Attributes:
        id: 项目 UUID。
        department_id: 所属部门 ID（FK→department.id）。
        code: 项目编码（部门内唯一，创建后锁定）。
        display_name: 中文显示名。
        description: 描述（可选）。
        status: 状态（active / archived）。
        visible_departments: 可见单位 ID 列表（JSONB 数组，跨实验室可见性）。
        visibility_scope: 可见范围（tree / explicit / all / private）。
        owner_user_id: 所有者用户 ID（FK→app_user.id）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "experiment_project"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'active'")
    )
    # ---- A 类多租户 4 列（与 equipment 一致） ----
    visible_departments: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    visibility_scope: Mapped[str] = mapped_column(
        sa.String(10),
        nullable=False,
        server_default=sa.text("'tree'"),
        comment="可见范围：tree / explicit / all",
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("app_user.id"),
        nullable=False,
        comment="所有者用户 ID",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    __table_args__ = (
        sa.UniqueConstraint("department_id", "code", name="uq_experiment_project_dept_code"),
    )

    def __repr__(self) -> str:
        return (
            f"ExperimentProject(id={self.id!r}, code={self.code!r}, "
            f"display_name={self.display_name!r}, status={self.status!r})"
        )
