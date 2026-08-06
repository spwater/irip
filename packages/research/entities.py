"""研究域 ORM 模型。

定义四张表：
- research_workspace: 研究工作空间（用户级，含主研究问题版本号缓存）；
- research_question_version: 研究问题版本（不可变，每次更新生成新版本）；
- research_workspace_evidence_ref: 工作空间证据引用（逻辑引用核心 Fact，不建 FK）；
- research_evidence_snapshot: 证据快照（不可变，冻结时的权限包络 + 字段清单 + 哈希）。

设计要点：
- 研究表以 ``research_`` 前缀命名，与核心表（fact / evidence_set 等）完全分离；
- 研究表之间的 FK（workspace_id → research_workspace.id ON DELETE CASCADE）使用 sa.ForeignKey；
- 跨模块引用（source_id）不建 FK，纯 GUID 列（逻辑引用核心 Fact）；
- 研究表到 app_user / department 的 FK 允许保留（稳定基础表）；
- 不可变表（question_version / snapshot）由应用层保证不 UPDATE / DELETE。

风格参考 packages/facts/entities.py：继承 Base，
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
import packages.departments.entities  # noqa: F401 — department table
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class ResearchWorkspace(Base):
    """研究工作空间实体（对应 research_workspace 表）。

    一个工作空间属于一个用户（owner_user_id），包含主研究问题（版本化）、
    证据引用列表和证据快照。工作空间状态为 draft（活跃）或 archived（归档）。

    Attributes:
        id: 工作空间 UUID（PK）。
        owner_user_id: 所有者用户 ID（FK→app_user）。
        department_id: 所属部门 ID（FK→department，用于 RLS 隔离）。
        name: 工作空间名称。
        status: 状态（draft / archived）。
        current_question_version: 当前最新问题版本号（冗余缓存）。
        forked_from_id: 分叉来源工作空间 ID（逻辑引用，不建 FK）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "research_workspace"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    department_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("department.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'")
    )
    current_question_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    forked_from_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    def __repr__(self) -> str:
        return (
            f"ResearchWorkspace(id={self.id!r}, name={self.name!r}, "
            f"status={self.status!r})"
        )


class ResearchQuestionVersion(Base):
    """研究问题版本实体（对应 research_question_version 表）。

    不可变：创建后不允许 UPDATE（应用层保证）。每次更新研究问题生成新版本，
    version_number 递增。sub_questions 为 JSONB 数组。

    Attributes:
        id: 版本 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        version_number: 版本号（从 1 开始递增）。
        question_text: 主研究问题文本。
        sub_questions: 子问题列表（JSONB 数组，如 ["温度梯度的影响"]）。
        created_at: 创建时间。
        created_by: 创建人 ID（FK→app_user）。
    """

    __tablename__ = "research_question_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sub_questions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchQuestionVersion(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, "
            f"version_number={self.version_number!r})"
        )


class WorkspaceEvidenceRef(Base):
    """工作空间证据引用实体（对应 research_workspace_evidence_ref 表）。

    逻辑引用核心域对象（如 Fact），通过 source_namespace + source_id 标识。
    source_id 不建数据库级 FK（跨模块逻辑引用）。软删除使用 status → 'removed'。

    Attributes:
        id: 引用 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        source_namespace: 源命名空间（如 "core:fact"）。
        source_id: 源对象 ID（逻辑引用，不建 FK）。
        source_version: 源对象版本快照（可选）。
        source_name: 源对象名称快照（可选）。
        added_at: 加入时间。
        added_by: 加入人 ID（FK→app_user）。
        status: 状态（active / removed）。
    """

    __tablename__ = "research_workspace_evidence_ref"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_namespace: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    source_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    added_by: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'active'")
    )

    def __repr__(self) -> str:
        return (
            f"WorkspaceEvidenceRef(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, "
            f"source_namespace={self.source_namespace!r}, "
            f"status={self.status!r})"
        )


class ResearchEvidenceSnapshot(Base):
    """证据快照实体（对应 research_evidence_snapshot 表）。

    不可变：创建后不允许 UPDATE / DELETE（应用层保证）。冻结时记录
    权限包络、字段清单、内容哈希和源引用列表。

    Attributes:
        id: 快照 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace ON DELETE CASCADE）。
        snapshot_number: 快照编号（从 1 开始递增）。
        content_hash: 内容哈希（SHA-256，64 字符十六进制）。
        captured_at: 冻结时间。
        permission_envelope: 权限快照（JSONB，如 {fact_id: {scope, dept_id}}）。
        field_manifest: 字段清单（JSONB，如 {fact_id: ["组分", "结果"]}）。
        source_refs: 源引用列表（JSONB，如 [{namespace, id, version}]）。
        created_by: 创建人 ID（FK→app_user）。
    """

    __tablename__ = "research_evidence_snapshot"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    permission_envelope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    field_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchEvidenceSnapshot(id={self.id!r}, "
            f"workspace_id={self.workspace_id!r}, "
            f"snapshot_number={self.snapshot_number!r})"
        )


# ============================================================
# 阶段 3：研究产物 ORM 实体（7 张表）
# ============================================================


class ResearchDerivedDataset(Base):
    """衍生数据集稳定身份实体（对应 research_derived_dataset 表）。

    一个 Dataset 可有多个版本（DerivedDatasetVersion），版本号递增。
    stable identity 可编辑 name/summary/tags，version 内容不可变。

    Attributes:
        id: 数据集 UUID（PK）。
        workspace_id: 工作空间 ID（FK→research_workspace CASCADE）。
        owner_user_id: 所有者用户 ID（FK→app_user）。
        name: 名称（可编辑）。
        summary: 摘要（可编辑，可空）。
        tags: 标签列表（JSONB，可编辑）。
        status: 状态（draft/confirmed，默认 confirmed）。
        current_version: 当前版本号（冗余缓存）。
        source_run_id: 来源 Run ID（FK→research_analysis_run）。
        source_snapshot_id: 来源快照 ID（逻辑引用，不建 FK）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "research_derived_dataset"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'confirmed'")
    )
    current_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    source_run_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=False
    )
    source_snapshot_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    def __repr__(self) -> str:
        return (
            f"ResearchDerivedDataset(id={self.id!r}, name={self.name!r}, "
            f"current_version={self.current_version!r})"
        )


class ResearchDerivedDatasetVersion(Base):
    """衍生数据集版本实体（对应 research_derived_dataset_version 表）。

    不可变：创建后不允许 UPDATE / DELETE（应用层保证）。
    存储三段式数据（metadata/points/series）+ field_manifest + content_hash。

    Attributes:
        id: 版本 UUID（PK）。
        dataset_id: 数据集 ID（FK→research_derived_dataset CASCADE）。
        version_number: 版本号（从 1 开始递增）。
        metadata_content: 报告级描述（JSONB dict）。
        points_content: 独立单值指标（JSONB list of {name, value, unit}）。
        series_content: 普通表格/时间序列（JSONB list of {name, columns, rows}）。
        field_manifest: 字段清单（JSONB list of FieldManifestEntry）。
        source_run_id: 来源 Run ID。
        source_step_id: 来源步骤 ID（可空）。
        source_artifact_id: 来源工件 ID（可空）。
        content_hash: 三段式数据 SHA-256。
        created_at: 创建时间。
        created_by: 创建人 ID。
    """

    __tablename__ = "research_derived_dataset_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    dataset_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_derived_dataset.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    metadata_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    points_content: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    series_content: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    field_manifest: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    source_run_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=False
    )
    source_step_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_step.id"), nullable=True
    )
    source_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_run_artifact.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchDerivedDatasetVersion(id={self.id!r}, "
            f"dataset_id={self.dataset_id!r}, "
            f"version_number={self.version_number!r})"
        )


class ResearchView(Base):
    """研究视图稳定身份实体（对应 research_view 表）。

    Attributes:
        id: 视图 UUID（PK）。
        workspace_id: 工作空间 ID。
        owner_user_id: 所有者用户 ID。
        name: 名称（可编辑）。
        caption: 图注（可编辑，可空）。
        display_order: 展示顺序（可编辑，默认 0）。
        status: 状态（draft/confirmed）。
        current_version: 当前版本号。
        source_run_id: 来源 Run ID。
        created_at / updated_at / lock_version。
    """

    __tablename__ = "research_view"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    display_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'confirmed'")
    )
    current_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    source_run_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=False
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

    def __repr__(self) -> str:
        return (
            f"ResearchView(id={self.id!r}, name={self.name!r}, "
            f"current_version={self.current_version!r})"
        )


class ResearchViewVersion(Base):
    """研究视图版本实体（对应 research_view_version 表）。

    不可变：创建后不允许 UPDATE / DELETE。
    记录静态图存储路径、格式、绘图代码引用、沙箱环境、来源 Run/Step/Artifact。

    Attributes:
        id: 版本 UUID（PK）。
        view_id: 视图 ID（FK→research_view CASCADE）。
        version_number: 版本号。
        image_storage_path: MinIO 存储路径。
        image_format: 图片格式（png/pdf）。
        image_width / image_height: 图片尺寸（可空）。
        image_content_hash: 图片内容哈希。
        chart_code_artifact_id: 绘图代码工件 ID（FK→research_run_artifact，可空）。
        image_digest: 沙箱镜像 digest（从 Run 继承）。
        source_run_id / source_step_id / source_artifact_id: 来源引用。
        bound_dataset_version_id: 绑定数据集版本 ID（逻辑引用，不建 FK）。
        chart_description: 图表说明（可空）。
        created_at / created_by。
    """

    __tablename__ = "research_view_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    view_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_view.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    image_storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    image_format: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'png'")
    )
    image_width: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    image_content_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    chart_code_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_run_artifact.id"), nullable=True
    )
    image_digest: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_run_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=False
    )
    source_step_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_step.id"), nullable=True
    )
    source_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_run_artifact.id"), nullable=True
    )
    bound_dataset_version_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    chart_description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchViewVersion(id={self.id!r}, view_id={self.view_id!r}, "
            f"version_number={self.version_number!r})"
        )


class ResearchInsight(Base):
    """Insight 稳定身份实体（对应 research_insight 表）。

    Attributes:
        id: Insight UUID（PK）。
        workspace_id / owner_user_id: 工作空间和所有者。
        name: 名称（可编辑）。
        status: 状态（draft/confirmed）。
        current_version: 当前版本号。
        source_run_id: 来源 Run ID（可空）。
        created_at / updated_at / lock_version。
    """

    __tablename__ = "research_insight"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'confirmed'")
    )
    current_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=True
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

    def __repr__(self) -> str:
        return (
            f"ResearchInsight(id={self.id!r}, name={self.name!r}, "
            f"current_version={self.current_version!r})"
        )


class ResearchInsightVersion(Base):
    """Insight 版本实体（对应 research_insight_version 表）。

    不可变：创建后不允许 UPDATE / DELETE。
    6 个必填字段 + evidence_source_label + AI 原稿 + 修改记录。

    Attributes:
        id: 版本 UUID（PK）。
        insight_id: Insight ID（FK→research_insight CASCADE）。
        version_number: 版本号。
        conclusion / scope / evidence_refs / method_refs / confidence_level / limitations: 6 个必填字段。
        evidence_source_label: 证据来源标签（experimental_data / knowledge_base / model_inference）。
        ai_original_text: AI 原稿（可空）。
        is_modified: 是否被用户修改。
        modification_note: 修改原因（可空）。
        source_candidate_id: 来源候选 ID（逻辑引用，不建 FK）。
        source_run_id: 来源 Run ID（可空）。
        created_at / created_by。
    """

    __tablename__ = "research_insight_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    insight_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_insight.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    conclusion: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    method_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    confidence_level: Mapped[str] = mapped_column(sa.Text, nullable=False)
    limitations: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_source_label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ai_original_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    is_modified: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    modification_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_candidate_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    source_run_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_run.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ResearchInsightVersion(id={self.id!r}, "
            f"insight_id={self.insight_id!r}, "
            f"version_number={self.version_number!r})"
        )


class ResearchInsightCandidate(Base):
    """Insight 候选实体（对应 research_insight_candidate 表）。

    由 Orchestrator 在 LLM/混合步骤完成后通过 InsightExtractor 提取。
    用户可接受/修改/拒绝。

    Attributes:
        id: 候选 UUID（PK）。
        workspace_id / run_id / step_id: 来源引用。
        conclusion / scope / evidence_refs / method_refs / confidence_level / limitations: 6 个必填字段。
        evidence_source_label: 证据来源标签。
        ai_raw_text: AI 原始回答文本。
        status: 状态（pending / accepted / modified / rejected）。
        accepted_insight_id: 接受后创建的 Insight ID（逻辑引用，不建 FK）。
        rejection_reason: 拒绝原因（可空）。
        created_at / reviewed_at / reviewed_by。
    """

    __tablename__ = "research_insight_candidate"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    workspace_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("research_analysis_step.id"), nullable=True
    )
    conclusion: Mapped[str] = mapped_column(sa.Text, nullable=False)
    scope: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    method_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    confidence_level: Mapped[str] = mapped_column(sa.Text, nullable=False)
    limitations: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_source_label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ai_raw_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'")
    )
    accepted_insight_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"ResearchInsightCandidate(id={self.id!r}, run_id={self.run_id!r}, "
            f"status={self.status!r})"
        )
