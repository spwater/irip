"""流程运行时 ORM 实体定义。

包含 4 个 ORM 实体：
- FlowDefinition: 流程定义主表（组织内按 code 唯一）；
- FlowDefinitionVersionORM: 流程版本表（按 definition+version 唯一，已发布不可变）；
- FlowRun: 流程执行记录（关联 job + 输入快照 + 输出摘要）；
- FlowNodeExecution: 节点执行记录（逐节点状态 + 输入/输出摘要）。

向后兼容：``flow_runtime.py`` 和 ``flow/__init__.py`` 通过 re-export
保持外部 ``from packages.components.flow.flow_runtime import FlowDefinition`` 兼容。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class FlowDefinition(Base):
    """流程定义主表 ORM 模型（对应 flow_definition 表）。

    部门内按 code 唯一，一个定义可包含多个版本。

    Attributes:
        id: 流程定义 UUID。
        department_id: 所属部门 ID。
        code: 流程编码（部门内唯一）。
        display_name: 显示名称。
        status: 生命周期状态（draft/published/deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    __tablename__ = "flow_definition"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id"),
        nullable=False,
        comment="所属部门 ID",
    )
    # ---- 阶段1 多租户隔离键升级：A 类其余三列（department_id 已有，阶段2改为 NOT NULL） ----
    visible_departments: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
        comment="跨实验室可见部门 ID 列表",
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
    project_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("experiment_project.id"),
        nullable=True,
        comment="所属实验项目 ID",
    )
    operator: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    experimental_object_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="draft",
        server_default=sa.text("'draft'"),
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

    def __repr__(self) -> str:
        return f"FlowDefinition(code={self.code!r}, status={self.status!r})"


class FlowDefinitionVersionORM(Base):
    """流程版本表 ORM 模型（对应 flow_definition_version 表）。

    每次发布创建一行，已发布版本不可变。

    Attributes:
        id: 版本 UUID。
        flow_definition_id: 所属流程定义 ID（FK）。
        version: 版本号（从 1 递增）。
        nodes_json: 节点列表（JSONB）。
        edges_json: 边列表（JSONB）。
        random_seed: 随机种子。
        digest: 内容摘要（SHA-256）。
        status: 版本状态（默认 published）。
        published_at: 发布时间（UTC）。
        created_at: 创建时间（UTC）。
    """

    __tablename__ = "flow_definition_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    flow_definition_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "flow_definition.id",
            name="fk_flow_version_definition_id",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    edges_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    random_seed: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    digest: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="published",
        server_default=sa.text("'published'"),
    )
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"FlowDefinitionVersionORM(version={self.version!r}, status={self.status!r})"


class FlowRun(Base):
    """流程执行记录 ORM 模型（对应 flow_run 表）。

    Attributes:
        id: 执行记录 UUID。
        department_id: 所属部门 ID。
        flow_version_id: 流程版本 ID（FK）。
        status: 执行状态（pending/running/succeeded/failed/cancelled）。
        job_id: 关联作业 ID（FK→job.id）。
        input_snapshot: 输入快照（JSONB）。
        output_digest: 输出摘要（SHA-256）。
        started_at: 开始执行时间。
        completed_at: 完成时间。
        created_at: 创建时间。
    """

    __tablename__ = "flow_run"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    # ---- 多租户隔离键升级：B 类一列 ----
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id"),
        nullable=False,
        comment="所属部门 ID（流程定义部门快照）",
    )
    flow_version_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "flow_definition_version.id",
            name="fk_flow_run_version_id",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="pending",
        server_default=sa.text("'pending'"),
    )
    job_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_digest: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"FlowRun(status={self.status!r}, flow_version_id={self.flow_version_id!r})"


class FlowNodeExecution(Base):
    """节点执行记录 ORM 模型（对应 flow_node_execution 表）。

    Attributes:
        id: 执行记录 UUID。
        flow_run_id: 所属流程执行 ID（FK→flow_run.id CASCADE）。
        node_id: 节点 ID（流程内标识）。
        status: 执行状态（pending/running/succeeded/failed）。
        input_summary: 输入摘要（JSONB）。
        output_summary: 输出摘要（JSONB）。
        diagnostics: 诊断信息（JSONB）。
        started_at: 开始时间。
        completed_at: 完成时间。
        duration_ms: 执行耗时（毫秒）。
    """

    __tablename__ = "flow_node_execution"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    flow_run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "flow_run.id",
            name="fk_flow_node_execution_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="pending",
        server_default=sa.text("'pending'"),
    )
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    def __repr__(self) -> str:
        return f"FlowNodeExecution(node_id={self.node_id!r}, status={self.status!r})"
