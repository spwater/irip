"""IRIP 模型 ORM 实体（V2-T04）。

提供两张表：
- model: 模型主表，组织内按 (organization_id, code) 唯一，
  含 status / current_version_id（发布指针）/ lock_version；
- model_version: 模型版本表，按 (model_id, version) 唯一，
  含 contract_json / metrics_json / applicability_domain_json /
  三个哈希（code_hash / dependency_hash / model_hash）。

设计要点：
- current_version_id 为发布指针，指向已发布的 model_version.id，
  rollback 通过移动指针实现（不删除旧版本）；
- 三个哈希支持内容寻址与可复现性校验：
  - code_hash: 训练代码的 SHA-256；
  - dependency_hash: 依赖清单的 SHA-256；
  - model_hash: 模型工件内容的 SHA-256；
- status 取值：draft / pending_validation / validated / published /
  deprecated，遵循生命周期状态机。
- 复用 V1 的 Base / GUID / UTCDateTime / new_id。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


#: 合法模型状态集合（生命周期状态机）。
MODEL_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_validation",
    "validated",
    "published",
    "deprecated",
)


class Model(Base):
    """模型主表 ORM 模型（对应 model 表）。

    组织内按 (organization_id, code) 唯一。一个模型可包含多个版本，
    current_version_id 指向当前已发布版本（可空，表示未发布）。

    Attributes:
        id: 模型 UUID。
        organization_id: 所属组织 ID。
        code: 模型代码（组织内唯一）。
        display_name: 模型显示名称。
        status: 生命周期状态
            （draft/pending_validation/validated/published/deprecated）。
        current_version_id: 当前发布版本 ID（发布指针，可空）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    __tablename__ = "model"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="draft",
        server_default=sa.text("'draft'"),
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        GUID, nullable=True
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_model_org_code"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"Model(code={self.code!r}, status={self.status!r}, "
            f"current_version_id={self.current_version_id!r})"
        )


class ModelVersion(Base):
    """模型版本表 ORM 模型（对应 model_version 表）。

    每次提交验证创建一行，记录契约、指标、适用域与三个哈希。
    版本按 (model_id, version) 唯一，version 从 1 递增。

    Attributes:
        id: 版本 UUID。
        model_id: 所属模型 ID（FK→model.id）。
        version: 版本号（从 1 递增）。
        contract_json: 模型契约（JSONB，含 input/output Schema）。
        model_artifact_id: 模型工件 UUID（引用 artifact 表）。
        metrics_json: 验证指标（JSONB，如 R²、RMSE）。
        applicability_domain_json: 适用域（JSONB，各维度 min/max）。
        code_hash: 训练代码 SHA-256。
        dependency_hash: 依赖清单 SHA-256。
        model_hash: 模型工件内容 SHA-256。
        status: 版本状态
            （draft/pending_validation/validated/published/deprecated）。
        created_at: 创建时间（UTC）。
        published_at: 发布时间（UTC，可空）。
    """

    __tablename__ = "model_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    model_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("model.id", name="fk_model_version_model_id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    contract_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    model_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID, nullable=True
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    applicability_domain_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    code_hash: Mapped[str | None] = mapped_column(
        sa.Text, nullable=True
    )
    dependency_hash: Mapped[str | None] = mapped_column(
        sa.Text, nullable=True
    )
    model_hash: Mapped[str | None] = mapped_column(
        sa.Text, nullable=True
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="draft",
        server_default=sa.text("'draft'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "model_id", "version", name="uq_model_version_model_ver"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"ModelVersion(model_id={self.model_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )
