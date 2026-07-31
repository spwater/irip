"""L2 事实层 ORM 模型。

定义两张表：
- fact: 事实主表（含合并字段，一个逻辑事实一行）；
- fact_data_index: 通用 KV 展平索引（支持跨任务内容搜索）。

设计要点：
- 事实写入后实验数据不可编辑（业务层保证），status 可更新（如 archive）；
- 全文搜索：fact 表上生成 tsvector 列 search_vector，GIN 索引；
- FactDataIndex FK → fact(id) ON DELETE CASCADE。

风格参考 packages/standards/templates.py：继承 Base，
使用 GUID / UTCDateTime 自定义类型，Mapped[] + mapped_column()。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

# 导入被引用的 ORM 模型所在模块，确保 FK 目标表注册到 Base.metadata。
# 这些导入不在此模块中使用，但 SQLAlchemy 需要它们来解析 FK 依赖。
import packages.auth.entities  # noqa: F401 — app_user table
import packages.common.artifacts  # noqa: F401 — artifact table
import packages.standards.objects  # noqa: F401 — industrial_object table
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class Fact(Base):
    """事实实体（对应 fact 表）。

    稳定身份表：一个逻辑事实一行，含合并自 fact_revision 的字段。
    idempotency_key 在组织内唯一（仅成功创建时设置），用于幂等去重。

    Attributes:
        id: 事实 UUID（PK）。
        organization_id: 所属组织 ID。
        fact_type: 事实类型（experiment_run / simulation_run /
            document_record / model_execution）。
        object_id: 工业对象 ID（FK→industrial_object）。
        status: 状态（active / superseded / withdrawn / archived）。
        lock_version: 乐观锁版本号。
        idempotency_key: 幂等键（组织内唯一，仅成功创建时设置）。
        created_at: 创建时间。
        updated_at: 更新时间。
        created_by: 创建人 UUID（FK→app_user）。
        subject_id: 主体标识（如样品编号、批次号）。
        flow_run_id: 流程运行 ID（可选，FK→flow_run ON DELETE SET NULL）。
        started_at: 事实开始时间。
        ended_at: 事实结束时间。
        task_code: 任务编码快照。
        task_name: 任务名称快照。
        department_name: 部门名称快照。
        operator: 操作人快照。
        run_operator: 运行操作人快照。
        equipment_name: 设备名快照。
        source_artifact_id: 源工件 ID（可选，FK→artifact ON DELETE SET NULL）。
        search_vector: 全文搜索向量（tsvector 生成列）。
    """

    __tablename__ = "fact"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    fact_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    object_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("industrial_object.id"),
        nullable=False,
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
    # 合并自 fact_revision 的字段
    subject_id: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("''")
    )
    flow_run_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("flow_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    task_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    task_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    department_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    operator: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    run_operator: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    equipment_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("artifact.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_fact_org_idempotency",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"Fact(id={self.id!r}, fact_type={self.fact_type!r}, "
            f"status={self.status!r})"
        )


class FactDataIndex(Base):
    """事实数据索引实体（对应 fact_data_index 表）。

    通用 KV 展平索引：将 data 数组每行每列拆成 key-value 对，
    支持跨任务、跨实验类型的内容搜索。

    不管 XRF 的 {组分, 单位, 结果} 还是粒度分析的 {D10, D50, D90}，
    所有字段都会被拆成 (key, value_text, value_number) 三列存储。

    Attributes:
        id: 索引 UUID（PK）。
        fact_id: 事实 ID（FK→fact，CASCADE）。
        row_index: data 数组中的行号（0-based）。
        key: 字段名（如 "组分"、"结果"、"D50"）。
        value_text: 字符串值（非数值时存这里）。
        value_number: 数值值（数值时存这里，非数值为 None）。
    """

    __tablename__ = "fact_data_index"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    fact_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("fact.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    def __repr__(self) -> str:
        return (
            f"FactDataIndex(id={self.id!r}, "
            f"fact_id={self.fact_id!r}, "
            f"row_index={self.row_index!r}, key={self.key!r})"
        )
