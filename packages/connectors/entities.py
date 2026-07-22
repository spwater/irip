"""映射配置与密钥 ORM 模型。

定义三张表（IRIP Task 13）：
- mapping_profile: 映射配置主表，name 组织内唯一，含状态机字段；
- mapping_profile_version: 不可变版本表，存储规则快照，发布后锁定；
- secret: 密钥表，按 id 引用存储外部数据源凭据（MVP 明文，TODO 加密）。

风格参考 packages/standards/variables.py：继承 Base，
使用 GUID / UTCDateTime 自定义类型，Mapped[] + mapped_column()。
状态值与 StandardStatus 对齐（draft / in_review / published / rejected / deprecated），
复用 packages.standards.state_machine.assert_transition 做转换校验。
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


class ProfileStatus(StrEnum):
    """映射配置状态枚举（与 StandardStatus 值对齐）。

    Attributes:
        DRAFT: 草稿，可编辑规则。
        IN_REVIEW: 审核中。
        PUBLISHED: 已发布，规则不可变（仅可弃用）。
        REJECTED: 已拒绝，可重新编辑。
        DEPRECATED: 已弃用。
    """

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class SecretKind(StrEnum):
    """密钥种类枚举。

    Attributes:
        POSTGRES_DSN: PostgreSQL 连接串。
        REST_TOKEN: REST API base URL + 认证令牌。
    """

    POSTGRES_DSN = "postgres_dsn"
    REST_TOKEN = "rest_token"


class MappingProfile(Base):
    """映射配置实体（对应 mapping_profile 表）。

    name 在组织内唯一（UNIQUE 约束 (organization_id, name)）。
    source_kind / source_config 描述外部数据源（不含明文凭据）。
    规则存储在 mapping_profile_version 中（草稿版本可编辑）。

    Attributes:
        id: 配置 UUID。
        organization_id: 所属组织 ID。
        name: 配置名称（组织内唯一）。
        source_kind: 数据源类型（file / postgres / rest）。
        source_config: 数据源配置 JSONB（含 secret_id 引用，不含明文）。
        status: 状态（draft / in_review / published / rejected / deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
        updated_at: 更新时间。
        created_by: 创建人 UUID。
    """

    __tablename__ = "mapping_profile"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'")
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_mapping_profile_org_name"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"MappingProfile(id={self.id!r}, name={self.name!r}, "
            f"status={self.status!r})"
        )


class MappingProfileVersion(Base):
    """映射配置不可变版本实体（对应 mapping_profile_version 表）。

    草稿阶段存储可编辑规则；发布后（status=published）规则不可修改。
    每次提交审核创建新版本（version 递增）。

    Attributes:
        id: 版本 UUID。
        profile_id: 所属配置 ID（FK→mapping_profile.id）。
        version: 版本号（从 1 开始递增）。
        rules: 规则 JSONB 数组（MappingRule 序列化）。
        status: 版本状态（draft / in_review / published / rejected / deprecated）。
        published_at: 发布时间（发布后设置）。
        lock_version: 乐观锁版本号。
        created_at: 版本创建时间。
    """

    __tablename__ = "mapping_profile_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    profile_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("mapping_profile.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    rules: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'")
    )
    published_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"MappingProfileVersion(id={self.id!r}, profile_id={self.profile_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


class Secret(Base):
    """密钥实体（对应 secret 表）。

    存储外部数据源凭据（PostgreSQL DSN / REST token），按 id 引用。
    MVP 阶段 value 为明文（TODO 加密），组织内可见。

    Attributes:
        id: 密钥 UUID。
        organization_id: 所属组织 ID。
        kind: 密钥种类（postgres_dsn / rest_token）。
        value: 凭据明文（MVP，TODO 加密）。
        created_at: 创建时间。
    """

    __tablename__ = "secret"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"Secret(id={self.id!r}, kind={self.kind!r})"
