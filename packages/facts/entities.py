"""L2 事实层 ORM 模型（IRIP Task 15）。

定义六张表：
- fact: 事实主表（稳定身份，一个逻辑事实一行），含状态与幂等键；
- fact_revision: 不可变修订表（每次修订一行，revision 号递增）；
- raw_observation: 原始观察值（来源数据的原始字段值，不可变）；
- normalized_observation: 标准化观察值（归一化到 L1 变量，不可变，
  必须引用一个 raw_observation）；
- fact_artifact: 事实-工件链接（角色化关联，如 raw_data / report）；
- fact_revision_link: 修订链链接（supersedes / corrects，用于历史遍历）。

设计要点：
- 修订不可变：一旦 fact_revision 创建，其内容不可修改，新变更创建新修订；
- 标准化必溯原始：normalized_observation 必须引用 raw_observation；
- 全文搜索：fact_revision 上生成 tsvector 列 search_vector，GIN 索引。

风格参考 packages/standards/templates.py：继承 Base，
使用 GUID / UTCDateTime 自定义类型，Mapped[] + mapped_column()。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# 导入被引用的 ORM 模型所在模块，确保 FK 目标表注册到 Base.metadata。
# 这些导入不在此模块中使用，但 SQLAlchemy 需要它们来解析 FK 依赖。
import packages.auth.entities  # noqa: F401 — app_user table
import packages.common.artifacts  # noqa: F401 — artifact table
import packages.standards.methods  # noqa: F401 — method_version table
import packages.standards.objects  # noqa: F401 — industrial_object table
import packages.standards.templates  # noqa: F401 — fact_template_version table
import packages.standards.variables  # noqa: F401 — variable_version table
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class Fact(Base):
    """事实实体（对应 fact 表）。

    稳定身份表：一个逻辑事实一行，current_revision 指向最新修订号。
    idempotency_key 在组织内唯一（仅成功创建时设置），用于幂等去重。

    Attributes:
        id: 事实 UUID（PK）。
        organization_id: 所属组织 ID。
        template_version_id: 事实模板版本 ID（FK→fact_template_version）。
        fact_type: 事实类型（experiment_run / simulation_run /
            document_record / model_execution）。
        object_id: 工业对象 ID（FK→industrial_object）。
        current_revision: 当前修订号（默认 1）。
        status: 状态（active / superseded / withdrawn）。
        lock_version: 乐观锁版本号。
        idempotency_key: 幂等键（组织内唯一，仅成功创建时设置）。
        created_at: 创建时间。
        updated_at: 更新时间。
        created_by: 创建人 UUID（FK→app_user）。
    """

    __tablename__ = "fact"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    template_version_id: Mapped[UUID | None] = mapped_column(
        GUID,
        nullable=True,
    )
    fact_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    object_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("industrial_object.id"),
        nullable=False,
    )
    current_revision: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'active'"))
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    idempotency_key: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("app_user.id"),
        nullable=True,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_fact_org_idempotency",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"Fact(id={self.id!r}, fact_type={self.fact_type!r}, "
            f"current_revision={self.current_revision!r}, "
            f"status={self.status!r})"
        )


class FactRevision(Base):
    """事实修订实体（对应 fact_revision 表）。

    不可变：一旦创建，内容不可修改。新变更创建新修订。
    revision 号在事实范围内递增（1, 2, 3, ...）。

    Attributes:
        id: 修订 UUID（PK）。
        fact_id: 事实 ID（FK→fact）。
        revision: 修订号（1, 2, 3, ...，事实范围内唯一）。
        template_version_id: 模板版本 ID（创建时快照，FK→fact_template_version）。
        fact_type: 事实类型快照。
        object_id: 工业对象 ID 快照。
        subject_id: 主体标识（如样品编号、批次号）。
        method_version_id: 方法版本 ID（可选，FK→method_version）。
        started_at: 事实开始时间。
        ended_at: 事实结束时间。
        revision_reason: 修订原因（修订 2+ 必填）。
        revision_summary: 质量评估摘要（JSONB，{level, code, status}）。
        created_at: 创建时间。
        created_by: 创建人 UUID（FK→app_user）。
        search_vector: 全文搜索向量（tsvector 生成列）。
    """

    __tablename__ = "fact_revision"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    fact_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    template_version_id: Mapped[UUID | None] = mapped_column(
        GUID,
        nullable=True,
    )
    fact_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    object_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("industrial_object.id"),
        nullable=False,
    )
    subject_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    method_version_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("method_version.id"),
        nullable=True,
    )
    # 入库时的任务信息快照（避免反查时的多表 JOIN）
    task_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    task_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    department_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    operator: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    run_operator: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    equipment_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 外键关联到 flow_run（nullable，兼容非 flow_run 来源的数据）
    flow_run_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("flow_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revision_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    revision_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("app_user.id"),
        nullable=True,
    )
    search_vector: Mapped[object] = mapped_column(
        sa.Text,
        server_default=sa.text(
            "to_tsvector('simple', coalesce(subject_id, '') || ' ' || coalesce(fact_type, ''))"
        ),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("fact_id", "revision", name="uq_fact_revision_fact_revision"),
    )

    def __repr__(self) -> str:
        return (
            f"FactRevision(id={self.id!r}, fact_id={self.fact_id!r}, "
            f"revision={self.revision!r}, subject_id={self.subject_id!r})"
        )


class RawObservation(Base):
    """原始观察值实体（对应 raw_observation 表）。

    不可变：记录来源数据的原始字段值。每个观察值关联到一个事实修订。

    Attributes:
        id: 观察 UUID（PK）。
        fact_revision_id: 事实修订 ID（FK→fact_revision）。
        source_path: 来源字段名/路径。
        source_value: 原始值（字符串形式）。
        source_unit: 原始单位（可选）。
        source_name: 原始列名/文件名（可选）。
        artifact_id: 工件 ID（可选，来自文件时关联，FK→artifact）。
        created_at: 创建时间。
    """

    __tablename__ = "raw_observation"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    fact_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_unit: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    artifact_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("artifact.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"RawObservation(id={self.id!r}, "
            f"source_path={self.source_path!r}, "
            f"source_value={self.source_value!r})"
        )


class NormalizedObservation(Base):
    """标准化观察值实体（对应 normalized_observation 表）。

    不可变：归一化到 L1 标准变量的观察值。必须引用一个原始观察值
    （raw_observation_id 不能为 None）。

    Attributes:
        id: 观察 UUID（PK）。
        fact_revision_id: 事实修订 ID（FK→fact_revision）。
        variable_version_id: 标准变量版本 ID（FK→variable_version，L1 标准）。
        raw_observation_id: 原始观察值 ID（FK→raw_observation，必须非空）。
        value: 标准化值（字符串形式）。
        unit: 标准化单位。
        created_at: 创建时间。
    """

    __tablename__ = "normalized_observation"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    fact_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    variable_version_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("variable_version.id"),
        nullable=False,
    )
    raw_observation_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("raw_observation.id"),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(sa.Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"NormalizedObservation(id={self.id!r}, "
            f"variable_version_id={self.variable_version_id!r}, "
            f"value={self.value!r})"
        )


class FactArtifact(Base):
    """事实-工件链接实体（对应 fact_artifact 表）。

    角色化关联：将事实修订链接到工件，并标记角色
    （如 raw_data / report / calibration）。

    Attributes:
        id: 链接 UUID（PK）。
        fact_revision_id: 事实修订 ID（FK→fact_revision）。
        artifact_id: 工件 ID（FK→artifact）。
        role: 角色（raw_data / report / calibration 等）。
        created_at: 创建时间。
    """

    __tablename__ = "fact_artifact"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    fact_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("artifact.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"FactArtifact(id={self.id!r}, "
            f"fact_revision_id={self.fact_revision_id!r}, "
            f"artifact_id={self.artifact_id!r}, role={self.role!r})"
        )


class FactRevisionLink(Base):
    """事实修订链链接实体（对应 fact_revision_link 表）。

    用于历史遍历：记录修订之间的替代/纠正关系。

    Attributes:
        id: 链接 UUID（PK）。
        from_revision_id: 源修订 ID（FK→fact_revision，新修订）。
        to_revision_id: 目标修订 ID（FK→fact_revision，旧修订）。
        link_type: 链接类型（supersedes / corrects）。
        created_at: 创建时间。
    """

    __tablename__ = "fact_revision_link"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    from_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    link_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"FactRevisionLink(id={self.id!r}, "
            f"from_revision_id={self.from_revision_id!r}, "
            f"to_revision_id={self.to_revision_id!r}, "
            f"link_type={self.link_type!r})"
        )


class FactDataIndex(Base):
    """事实数据索引实体（对应 fact_data_index 表）。

    通用 KV 展平索引：将 data 数组每行每列拆成 key-value 对，
    支持跨任务、跨实验类型的内容搜索。

    不管 XRF 的 {组分, 单位, 结果} 还是粒度分析的 {D10, D50, D90}，
    所有字段都会被拆成 (key, value_text, value_number) 三列存储。

    Attributes:
        id: 索引 UUID（PK）。
        fact_revision_id: 事实修订 ID（FK→fact_revision，CASCADE）。
        row_index: data 数组中的行号（0-based）。
        key: 字段名（如 "组分"、"结果"、"D50"）。
        value_text: 字符串值（非数值时存这里）。
        value_number: 数值值（数值时存这里，非数值为 None）。
    """

    __tablename__ = "fact_data_index"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    fact_revision_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact_revision.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    def __repr__(self) -> str:
        return (
            f"FactDataIndex(id={self.id!r}, "
            f"fact_revision_id={self.fact_revision_id!r}, "
            f"row_index={self.row_index!r}, key={self.key!r})"
        )
