"""设备仪器管理 ORM 模型。

定义设备仪器主表：
- equipment: code 组织内唯一，关联部门（department_id FK）。

原 equipment_variable 关联表已随标准层空表清理 DROP（migration 0057），
代码库中无对应 ORM 类。

风格参考 packages/departments/entities.py：继承 Base，
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


class EquipmentStatus(StrEnum):
    """设备仪器状态枚举。

    Attributes:
        ACTIVE: 启用状态，可正常使用。
        DISABLED: 禁用状态（软禁用），历史数据保留，新数据录入时过滤。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


class Equipment(Base):
    """设备仪器实体（对应 equipment 表）。

    code 在组织内唯一（UNIQUE 约束 (organization_id, code)）。
    department_id 关联 department 表（CASCADE 删除）。
    code 创建后锁定不可修改（服务层 UPDATE 语句不写 code 列）。

    Attributes:
        id: 设备 UUID。
        organization_id: 所属顶层组织 ID。
        code: 设备编码（组织内唯一，创建后锁定）。
        display_name: 中文显示名。
        description: 描述（可选）。
        department_id: 所属部门 ID（FK→department.id）。
        visible_departments: 可见单位 ID 列表（JSONB 数组，跨实验室可见性）。
        status: 状态（active / disabled）。
        sort_order: 排序权重（默认 0）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "equipment"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id", ondelete="CASCADE"),
        nullable=False,
    )
    visible_departments: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
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

    __table_args__ = (sa.UniqueConstraint("organization_id", "code", name="uq_equipment_org_code"),)

    def __repr__(self) -> str:
        return (
            f"Equipment(id={self.id!r}, code={self.code!r}, "
            f"display_name={self.display_name!r}, status={self.status!r})"
        )
