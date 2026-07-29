"""L3 参数层 ORM 模型（IRIP Task 18）。

定义四张表：
- parameter: 参数稳定身份（draft/pending_review/published/rejected/
  expired/deprecated），锁定版本号；
- parameter_version: 参数不可变发布版本（value/unit/confidence/
  conditions AST/derivation_run 引用），版本号递增；
- parameter_candidate: 推导产出的候选（pending_review/approved/rejected），
  等待审批，含提交人/审核人与审核决定；
- parameter_staleness: 事实修订 → 参数版本依赖跟踪（current/review_required）。

设计要点：
- 已发布版本不可变：parameter_version 创建后不可修改；
- 职责分离：提交人不能审批自己的候选；
- 过期跟踪：事实有新修订时，关联的参数版本变为 review_required；
- 唯一约束：每个参数每次推导只能有一个候选。

风格参考 packages/provenance/entities.py：继承 Base，使用 GUID / UTCDateTime
自定义类型，Mapped[] + mapped_column()。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# 导入被引用的 ORM 模型所在模块，确保 FK 目标表注册到 Base.metadata。
import packages.facts.entities  # noqa: F401 — fact_revision table
import packages.provenance.entities  # noqa: F401 — derivation_run table
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class Parameter(Base):
    """参数实体（对应 parameter 表）。

    稳定身份表：一个参数（variable_code + object_id）一行。
    status 从 draft 到 published 到 deprecated。

    Attributes:
        id: 参数 UUID（PK）。
        organization_id: 所属组织 ID。
        variable_code: 变量代码（关联标准变量）。
        object_id: 工业对象 ID。
        status: 状态（draft / pending_review / published / rejected /
            expired / deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
        updated_at: 更新时间。
        created_by: 创建人 UUID。
    """

    __tablename__ = "parameter"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    variable_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    object_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'draft'"),
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
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
            "organization_id",
            "variable_code",
            "object_id",
            name="uq_parameter_org_var_obj",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"Parameter(id={self.id!r}, variable_code={self.variable_code!r}, "
            f"status={self.status!r})"
        )


class ParameterVersion(Base):
    """参数版本实体（对应 parameter_version 表）。

    不可变：发布后内容不可修改。version 号在参数范围内递增。
    记录推导运行、证据集版本、配方版本的引用，保证可复现。

    Attributes:
        id: 版本 UUID（PK）。
        parameter_id: 参数 ID（FK→parameter）。
        version: 版本号。
        value: 参数值（Decimal 字符串形式）。
        unit: 单位（可选）。
        confidence: 置信度（Decimal 字符串形式，可选）。
        confidence_interval: 置信区间（JSONB，{lower, upper}，可选）。
        conditions: 条件 AST（JSONB，可选）。
        derivation_run_id: 推导运行 ID（FK→derivation_run）。
        evidence_set_version_id: 证据集版本 ID。
        recipe_version_id: 配方版本 ID。
        status: 状态（published / deprecated）。
        published_at: 发布时间。
        published_by: 发布人 UUID。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
    """

    __tablename__ = "parameter_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    parameter_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("parameter.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    confidence_interval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conditions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    derivation_run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("derivation_run.id"),
        nullable=False,
    )
    evidence_set_version_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    recipe_version_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'published'"),
    )
    published_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    published_by: Mapped[UUID] = mapped_column(GUID, nullable=False)
    lock_version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "parameter_id",
            "version",
            name="uq_parameter_version_param_version",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"ParameterVersion(id={self.id!r}, "
            f"parameter_id={self.parameter_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


class ParameterCandidate(Base):
    """参数候选实体（对应 parameter_candidate 表）。

    推导产出的候选，等待审批。每个参数每次推导只能有一个候选。

    Attributes:
        id: 候选 UUID（PK）。
        parameter_id: 参数 ID（FK→parameter）。
        derivation_run_id: 推导运行 ID（FK→derivation_run）。
        value: 候选值（Decimal 字符串形式）。
        unit: 单位（可选）。
        confidence: 置信度（Decimal 字符串形式，可选）。
        confidence_interval: 置信区间（JSONB，可选）。
        conditions: 条件 AST（JSONB，可选）。
        status: 状态（pending_review / approved / rejected）。
        submitted_by: 提交人 UUID。
        submitted_at: 提交时间。
        reviewed_by: 审核人 UUID（可选）。
        reviewed_at: 审核时间（可选）。
        review_decision: 审核决定（approved / rejected，可选）。
        review_comment: 审核备注（可选）。
        created_at: 创建时间。
    """

    __tablename__ = "parameter_candidate"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    parameter_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("parameter.id", ondelete="CASCADE"),
        nullable=False,
    )
    derivation_run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("derivation_run.id"),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    confidence_interval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conditions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'pending_review'"),
    )
    submitted_by: Mapped[UUID] = mapped_column(GUID, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    review_decision: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "parameter_id",
            "derivation_run_id",
            name="uq_parameter_candidate_param_deriv",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"ParameterCandidate(id={self.id!r}, "
            f"parameter_id={self.parameter_id!r}, "
            f"derivation_run_id={self.derivation_run_id!r}, "
            f"status={self.status!r})"
        )


class ParameterStaleness(Base):
    """参数过期跟踪实体（对应 parameter_staleness 表）。

    记录参数版本对特定事实修订的依赖。当事实有新修订时，
    依赖该事实的参数版本变为 review_required。

    Attributes:
        id: 跟踪 UUID（PK）。
        parameter_version_id: 参数版本 ID（FK→parameter_version）。
        fact_revision_id: 事实修订 ID（FK→fact_revision）。
        review_state: 过期状态（current / review_required）。
        last_checked_at: 最后检查时间。
    """

    __tablename__ = "parameter_staleness"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    parameter_version_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("parameter_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id"),
        nullable=False,
    )
    review_state: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'current'"),
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ParameterStaleness(id={self.id!r}, "
            f"parameter_version_id={self.parameter_version_id!r}, "
            f"fact_revision_id={self.fact_revision_id!r}, "
            f"review_state={self.review_state!r})"
        )
