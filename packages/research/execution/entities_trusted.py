"""可信执行 ORM 实体（阶段 2 新增 6 张表）。

定义六张表：
- research_analysis_plan_version: 分析计划版本（不可变 DAG 结构，JSONB 存储步骤列表）；
- research_analysis_run: 分析运行（状态机，部分唯一索引保证每 Workspace 最多 1 个活跃 Run）；
- research_analysis_step: 分析步骤（高频状态更新表，单独建表避免更新 JSONB）；
- research_run_artifact: 运行工件（白名单扫描后持久化到 MinIO）；
- research_ai_conversation: AI 对话历史（长对话截断保留最近 N 条）；
- research_memory_document: 后台研究记忆文档（每 Workspace 一行，JSONB 存储）。

设计要点：
- 继承 Base（来自 packages.common.database），与阶段 1 实体共享同一 metadata；
- 导入 packages.research.entities 确保阶段 1 的表注册到 Base.metadata；
- 研究表之间的 FK 使用 sa.ForeignKey + ON DELETE CASCADE；
- 跨模块引用（如 confirmed_by → app_user.id）保留 FK（稳定基础表）。

风格参考 packages/research/entities.py：Mapped[] + mapped_column()。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import packages.auth.entities  # noqa: F401 — app_user table

# 导入阶段 1 实体，确保 FK 目标表注册到 Base.metadata
import packages.research.entities  # noqa: F401 — research_workspace / snapshot tables
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class ResearchAnalysisPlanVersion(Base):
    """分析计划版本实体（对应 research_analysis_plan_version 表）。

    不可变：dag_structure 创建后不可修改。status 仅允许 draft → confirmed → superseded。
    DAG 结构以 JSONB 存储，步骤高频状态更新使用 research_analysis_step 表。

    Attributes:
        id: 计划版本 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        version_number: 版本号（从 1 开始递增）。
        dag_structure: DAG 步骤结构（JSONB，包含 steps 列表）。
        coverage_declaration: 覆盖声明（JSONB，可选）。
        status: 状态（draft / confirmed / superseded）。
        confirmed_at: 确认时间。
        confirmed_by: 确认人 ID（FK→app_user）。
        created_at: 创建时间。
        created_by: 创建人 ID（FK→app_user）。
    """

    __tablename__ = "research_analysis_plan_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    dag_structure: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    coverage_declaration: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'draft'"))
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    confirmed_by: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(GUID, sa.ForeignKey("app_user.id"), nullable=False)

    def __repr__(self) -> str:
        return (
            f"ResearchAnalysisPlanVersion(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, "
            f"version_number={self.version_number!r}, status={self.status!r})"
        )


class ResearchAnalysisRun(Base):
    """分析运行实体（对应 research_analysis_run 表）。

    Run 在后台持久运行，脱离前端会话。状态机：
    queued → planning → running → succeeded / partially_succeeded / failed
    queued / running → cancelled

    部分唯一索引确保每 Workspace 最多 1 个活跃 Run（status IN queued/planning/running）。
    重跑创建新 Run（run_number 递增），不覆盖旧 Run。

    Attributes:
        id: Run UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        plan_version_id: 计划版本 ID（FK→research_analysis_plan_version）。
        snapshot_id: 证据快照 ID（FK→research_evidence_snapshot）。
        run_number: Run 编号（从 1 开始递增）。
        status: 状态（queued / planning / running / partially_succeeded
        / succeeded / failed / cancelled）。
        queue_position: 排队位置（排队中时有值）。
        submitted_at: 提交时间。
        started_at: 开始执行时间。
        completed_at: 完成时间。
        cancelled_at: 取消时间。
        cancelled_by: 取消人 ID（FK→app_user）。
        error_summary: 错误摘要。
        coverage_summary: 覆盖率汇总（JSONB）。
        image_digest: 科学计算镜像 digest（旧 Run 永久记录）。
        created_by: 创建人 ID（FK→app_user）。
    """

    __tablename__ = "research_analysis_run"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_version_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_plan_version.id"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_evidence_snapshot.id"), nullable=False
    )
    run_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'queued'"))
    queue_position: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_by: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=True
    )
    error_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    coverage_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    image_digest: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[UUID] = mapped_column(GUID, sa.ForeignKey("app_user.id"), nullable=False)

    def __repr__(self) -> str:
        return (
            f"ResearchAnalysisRun(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, "
            f"run_number={self.run_number!r}, status={self.status!r})"
        )


class ResearchAnalysisStep(Base):
    """分析步骤实体（对应 research_analysis_step 表）。

    高频更新表：步骤状态变更频繁（pending → running → succeeded/failed），
    单独建表避免更新 JSONB。每个步骤记录执行方式、分析模式、覆盖率等。

    Attributes:
        id: 步骤 UUID（PK）。
        run_id: Run ID（FK→research_analysis_run ON DELETE CASCADE）。
        step_key: 步骤键（对应 DAG 中的 step_key）。
        step_index: 步骤序号（拓扑序）。
        status: 状态（pending / running / succeeded / failed / skipped / cancelled）。
        method: 执行方式（python / llm / knowledge / mixed）。
        analysis_mode: 分析模式（full_compute / chunked_full_scan
        / direct_full_context / retrieval / mixed）。
        data_budget_tokens: 数据预算 token 数。
        coverage_rate: 数据覆盖率。
        llm_read_rate: LLM 阅读率。
        is_sampled: 是否抽样。
        mode_reason: 模式选择原因。
        attempt_count: 尝试次数（含自动修错）。
        started_at: 开始时间。
        completed_at: 完成时间。
        error_message: 错误消息。
        error_classification: 错误分类。
        depends_on: 依赖步骤列表（JSONB 数组，存 step_key 列表）。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    __tablename__ = "research_analysis_step"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    step_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'")
    )
    method: Mapped[str] = mapped_column(sa.Text, nullable=False)
    analysis_mode: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    data_budget_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    coverage_rate: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    llm_read_rate: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    is_sampled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    mode_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_classification: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    depends_on: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchAnalysisStep(id={self.id!r}, run_id={self.run_id!r}, "
            f"step_key={self.step_key!r}, status={self.status!r})"
        )


class ResearchRunArtifact(Base):
    """运行工件实体（对应 research_run_artifact 表）。

    工件经白名单扫描后持久化到 MinIO。is_publishable 仅在依赖闭包全部成功时为 true。

    Attributes:
        id: 工件 UUID（PK）。
        run_id: Run ID（FK→research_analysis_run ON DELETE CASCADE）。
        step_id: 步骤 ID（FK→research_analysis_step ON DELETE CASCADE，可选）。
        artifact_type: 工件类型（code / log / chart / data / intermediate）。
        artifact_key: 工件键名。
        storage_path: MinIO 存储路径。
        content_hash: 内容哈希（SHA-256）。
        size_bytes: 文件大小（字节）。
        is_publishable: 是否可发布。
        created_at: 创建时间。
    """

    __tablename__ = "research_run_artifact"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("research_analysis_step.id", ondelete="CASCADE"),
        nullable=True,
    )
    artifact_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    artifact_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    is_publishable: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchRunArtifact(id={self.id!r}, run_id={self.run_id!r}, "
            f"artifact_type={self.artifact_type!r}, artifact_key={self.artifact_key!r})"
        )


class ResearchAiConversation(Base):
    """AI 对话历史实体（对应 research_ai_conversation 表）。

    持久化 AI 助手对话消息，支持重新进入恢复对话。
    长对话截断策略：查询时仅返回最近 50 条，旧消息保留在表中不删除。

    Attributes:
        id: 消息 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        role: 角色（user / assistant / system）。
        content: 消息内容（JSONB，如 {text, code_blocks, plan_ref, artifact_refs}）。
        run_id: 关联的 Run ID（FK→research_analysis_run，可空）。
        created_at: 创建时间。
        created_by: 创建人 ID（FK→app_user，AI 消息可为空）。
    """

    __tablename__ = "research_ai_conversation"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"ResearchAiConversation(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, role={self.role!r})"
        )


class ResearchMemoryDocument(Base):
    """后台研究记忆文档实体（对应 research_memory_document 表）。

    每 Workspace 一行（唯一约束），JSONB 存储记忆文档。
    由事件自动更新（Run 提交/完成/取消、计划确认、Insight 接受/否决）。
    文档与原始事件冲突时以原始事件为准。文档可重建（非权威源）。

    Attributes:
        id: 文档 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        document: 记忆文档（JSONB，包含主问题、范围、证据、计划、方法、Run、Insight 等）。
        version: 文档版本号（每次更新递增）。
        updated_at: 更新时间。
    """

    __tablename__ = "research_memory_document"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    document: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchMemoryDocument(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, version={self.version!r})"
        )
