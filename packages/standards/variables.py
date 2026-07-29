"""标准变量 ORM 模型与枚举。

定义三张表（IRIP Task 10）：
- variable: 标准变量主表，code 组织内唯一，含状态机字段；
- variable_version: 不可变版本表，每次提交审核创建一行，发布后锁定不可修改；
- variable_alias: 别名表，同一变量可有多语言别名。

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


class VariableStatus(StrEnum):
    """标准变量状态枚举（与 state_machine.StandardStatus 值对齐）。

    Attributes:
        DRAFT: 草稿状态，可编辑。
        IN_REVIEW: 审核中，已提交审核。
        PUBLISHED: 已发布，不可修改（仅可弃用）。
        REJECTED: 已拒绝，可重新提交。
        DEPRECATED: 已弃用，历史数据保留，新引用被阻止。
    """

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class DataType(StrEnum):
    """标准变量的数据类型枚举。

    Attributes:
        NUMBER: 数值型（含单位与量纲）。
        TEXT: 文本型。
        BOOLEAN: 布尔型。
        DATETIME: 日期时间型。
    """

    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


class QuantityKind(StrEnum):
    """物理量种类枚举（与 UnitConverter 维度对齐）。

    Attributes:
        LENGTH: 长度。
        TEMPERATURE: 温度。
        MASS: 质量。
        ANGLE: 角度。
        DIMENSIONLESS: 无量纲。
        TIME: 时间。
        AREA: 面积。
        VOLUME: 体积。
    """

    LENGTH = "length"
    TEMPERATURE = "temperature"
    MASS = "mass"
    ANGLE = "angle"
    DIMENSIONLESS = "dimensionless"
    TIME = "time"
    AREA = "area"
    VOLUME = "volume"


class Variable(Base):
    """标准变量实体（对应 variable 表）。

    code 在组织内唯一（UNIQUE 约束 (organization_id, code)）。
    valid_range 存为 JSONB 数组 [min_str, max_str]，使用 Decimal 字符串保留精度。
    version_count 记录已创建的版本数（= 最大版本号）。

    Attributes:
        id: 变量 UUID。
        organization_id: 所属组织 ID。
        code: 变量编码（组织内唯一，创建后锁定）。
        display_name: 中文显示名。
        data_type: 数据类型（number / text / boolean / datetime）。
        canonical_unit: 标准单位（可选，如 "mm"）。
        quantity_kind: 量纲种类（可选，如 "length"）。
        valid_range: 有效范围 [min, max]（JSONB 字符串数组，可选）。
        status: 状态（draft / in_review / published / rejected / deprecated）。
        version_count: 已创建版本数（默认 0）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "variable"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    data_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    canonical_unit: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    quantity_kind: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    valid_range: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'draft'"))
    version_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
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

    __table_args__ = (sa.UniqueConstraint("organization_id", "code", name="uq_variable_org_code"),)

    def __repr__(self) -> str:
        return (
            f"Variable(id={self.id!r}, code={self.code!r}, "
            f"status={self.status!r}, version_count={self.version_count!r})"
        )


class VariableVersion(Base):
    """标准变量不可变版本实体（对应 variable_version 表）。

    每次提交审核时从当前 variable 快照创建一行。发布后（status=published），
    核心属性（code, display_name, data_type, canonical_unit, quantity_kind,
    valid_range）不可修改；仅 status 可从 published 转为 deprecated。

    Attributes:
        id: 版本 UUID。
        variable_id: 所属变量 ID（FK→variable.id）。
        version: 版本号（从 1 开始递增）。
        code: 变量编码快照。
        display_name: 显示名快照。
        data_type: 数据类型快照。
        canonical_unit: 标准单位快照（可选）。
        quantity_kind: 量纲快照（可选）。
        valid_range: 有效范围快照（JSONB 字符串数组，可选）。
        status: 版本状态（in_review / published / rejected / deprecated）。
        published_at: 发布时间（发布后设置）。
        published_by: 发布人 UUID（发布后设置）。
        deprecated_at: 弃用时间（弃用后设置）。
        deprecated_by: 弃用人 UUID（弃用后设置）。
        rejection_reason: 拒绝原因（拒绝后设置）。
        created_at: 版本创建时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "variable_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    variable_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("variable.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    data_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    canonical_unit: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    quantity_kind: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    valid_range: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deprecated_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    def __repr__(self) -> str:
        return (
            f"VariableVersion(id={self.id!r}, variable_id={self.variable_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


class VariableAlias(Base):
    """标准变量别名实体（对应 variable_alias 表）。

    同一变量可拥有多个不同语言的别名，用于多语言检索。
    (variable_id, alias) 唯一约束防止重复别名。

    Attributes:
        id: 别名 UUID。
        variable_id: 所属变量 ID（FK→variable.id）。
        alias: 别名文本。
        language: 语言代码（默认 "zh"）。
        created_at: 创建时间。
    """

    __tablename__ = "variable_alias"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    variable_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("variable.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(sa.Text, nullable=False)
    language: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'zh'"))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (sa.UniqueConstraint("variable_id", "alias", name="uq_variable_alias"),)

    def __repr__(self) -> str:
        return (
            f"VariableAlias(id={self.id!r}, variable_id={self.variable_id!r}, "
            f"alias={self.alias!r}, language={self.language!r})"
        )
