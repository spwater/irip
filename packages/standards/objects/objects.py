"""工业对象 ORM 模型与枚举（IRIP Task 11）。

定义两张表：
- industrial_object: 工业对象实体（实验室 / 产线 / 设备组 / 仪器 / 测量点 / 样品 / 物料 / 产品），
  code 在组织内 + 类型内唯一，status 默认 active；
- object_relation: 对象间关系（包含 / 连接 / 上游 / 下游 / 测量 / 模拟 / 等价），
  (source_id, target_id, relation_type) 活跃时唯一，禁止自关联。

层次型关系（contains / upstream_of / downstream_of）必须无环，
ObjectGraphService 在 add_relation 时进行环检测。

风格参考 packages/standards/variables.py：继承 Base，
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


class RelationType(StrEnum):
    """对象间关系类型枚举。

    Attributes:
        CONTAINS: 包含关系（层次型，父子归属）。
        CONNECTED_TO: 连接关系（非层次型，允许双向）。
        UPSTREAM_OF: 上游关系（层次型，流向）。
        DOWNSTREAM_OF: 下游关系（层次型，流向）。
        MEASURES: 测量关系（非层次型，仪器与变量）。
        SIMULATES: 模拟关系（非层次型，模型与实体）。
        EQUIVALENT_TO: 等价关系（非层次型，对象等价）。
    """

    CONTAINS = "contains"
    CONNECTED_TO = "connected_to"
    UPSTREAM_OF = "upstream_of"
    DOWNSTREAM_OF = "downstream_of"
    MEASURES = "measures"
    SIMULATES = "simulates"
    EQUIVALENT_TO = "equivalent_to"


#: 形成层次结构且必须无环的关系类型集合。
HIERARCHICAL_RELATIONS: frozenset[str] = frozenset(
    {
        RelationType.CONTAINS.value,
        RelationType.UPSTREAM_OF.value,
        RelationType.DOWNSTREAM_OF.value,
    }
)


class ObjectType(StrEnum):
    """工业对象类型枚举。

    Attributes:
        LAB: 实验室。
        PRODUCTION_LINE: 产线。
        EQUIPMENT_GROUP: 设备组。
        INSTRUMENT: 仪器。
        MEASUREMENT_POINT: 测量点。
        MATERIAL: 物料。
        SIGNAL: 信号。
    """

    LAB = "lab"
    PRODUCTION_LINE = "production_line"
    EQUIPMENT_GROUP = "equipment_group"
    INSTRUMENT = "instrument"
    MEASUREMENT_POINT = "measurement_point"
    MATERIAL = "material"
    SIGNAL = "signal"


class IndustrialObject(Base):
    """工业对象实体（对应 industrial_object 表）。

    code 在组织内 + 类型内唯一。

    Attributes:
        id: 对象 UUID。
        organization_id: 所属组织 ID。
        object_type: 对象类型（lab / production_line / ...）。
        code: 对象编码（组织内 + 类型内唯一）。
        display_name: 中文显示名。
        description: 描述（可选）。
        department_id: 所属部门 ID（nullable，跨实验室可见性基准）。
        visible_departments: 可见单位 ID 列表（JSONB 数组，跨实验室可见性）。
        status: 状态（默认 active）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "industrial_object"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    object_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    equipment_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    department_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    visible_departments: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa.text("'[]'::jsonb"),
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'active'"))
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
        sa.UniqueConstraint(
            "organization_id",
            "object_type",
            "code",
            name="uq_industrial_object_org_type_code",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"IndustrialObject(id={self.id!r}, code={self.code!r}, "
            f"object_type={self.object_type!r}, status={self.status!r})"
        )


