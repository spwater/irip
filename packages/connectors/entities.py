"""密钥 ORM 模型。

原 mapping_profile / mapping_profile_version 表已随标准层空表清理
DROP（migration 0057），对应 ORM 类（MappingProfile /
MappingProfileVersion / ProfileStatus）已删除。本模块仅保留：

- secret: 密钥表，按 id 引用存储外部数据源凭据。
  凭据使用 AES-256-GCM 信封加密（EnvelopeCrypto），写入时加密、读取时解密。

风格参考 packages/standards/objects：继承 Base，
使用 GUID / UTCDateTime 自定义类型，Mapped[] + mapped_column()。
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class SecretKind(StrEnum):
    """密钥种类枚举。

    Attributes:
        POSTGRES_DSN: PostgreSQL 连接串。
        REST_TOKEN: REST API base URL + 认证令牌。
    """

    POSTGRES_DSN = "postgres_dsn"
    REST_TOKEN = "rest_token"


class Secret(Base):
    """密钥实体（对应 secret 表）。

    存储外部数据源凭据（PostgreSQL DSN / REST token），按 id 引用。
    value 字段使用 AES-256-GCM 信封加密（EnvelopeCrypto），
    写入时加密、读取时解密，绝不返回凭据给 API 层。

    Attributes:
        id: 密钥 UUID。
        department_id: 所属部门 ID。
        kind: 密钥种类（postgres_dsn / rest_token）。
        value: 加密后的凭据密文（格式 v{version}:{nonce}:{ciphertext}）。
        created_at: 创建时间。
    """

    __tablename__ = "secret"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    # ---- 多租户隔离键升级：B 类一列 ----
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id"),
        nullable=False,
        comment="所属部门 ID（密钥归 system 哨兵部门）",
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"Secret(id={self.id!r}, kind={self.kind!r})"
