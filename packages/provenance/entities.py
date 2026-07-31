"""L2.5 溯源与推导层 ORM 模型。

定义六张表：
- evidence_set: 证据集稳定身份（draft/frozen），锁定版本号；
- evidence_set_version: 证据集不可变冻结快照（members JSONB）；
- transformation_recipe: 推导配方稳定身份（draft/published/deprecated）；
- transformation_recipe_version: 推导配方不可变发布版本；
- derivation_run: 一次配方在证据集上的执行记录；
- provenance_edge: 溯源图边（连接事实、推导运行、参数版本）。

设计要点：
- 证据集版本不可变：冻结后 members 固定，保证可复现推导；
- 配方版本不可变：发布后参数固定，保证确定性回放；
- 推导输出确定性：相同证据 + 相同配方 + 相同随机种子 → 相同 output_digest；
- 溯源边双向索引：支持从推导结果向上追溯到原始事实，也支持向下遍历。

风格参考 packages/facts/entities.py：继承 Base，使用 GUID / UTCDateTime
自定义类型，Mapped[] + mapped_column()。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import packages.facts.entities  # noqa: F401 — fact table registration

# 导入被引用的 ORM 模型所在模块，确保 FK 目标表注册到 Base.metadata。
import packages.jobs.entities  # noqa: F401 — job table
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class EvidenceSet(Base):
    """证据集实体（对应 evidence_set 表）。

    稳定身份表：一个证据集一行，status 从 draft 转为 frozen 后不可修改。
    lock_version 用于乐观锁。

    Attributes:
        id: 证据集 UUID（PK）。
        organization_id: 所属组织 ID。
        name: 证据集名称。
        status: 状态（draft / frozen）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
        updated_at: 更新时间。
        created_by: 创建人 UUID。
    """

    __tablename__ = "evidence_set"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
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

    def __repr__(self) -> str:
        return f"EvidenceSet(id={self.id!r}, name={self.name!r}, status={self.status!r})"


class EvidenceSetVersion(Base):
    """证据集版本实体（对应 evidence_set_version 表）。

    不可变：冻结后 members 固定，保证可复现推导。
    version 号在证据集范围内递增（1, 2, 3, ...）。

    Attributes:
        id: 版本 UUID（PK）。
        evidence_set_id: 证据集 ID（FK→evidence_set）。
        version: 版本号。
        status: 状态（frozen）。
        members: 成员列表（JSONB，数组 of EvidenceMember dict）。
        member_count: 成员数量。
        created_at: 创建时间。
        frozen_at: 冻结时间。
    """

    __tablename__ = "evidence_set_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    evidence_set_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("evidence_set.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'frozen'"),
    )
    members: Mapped[list] = mapped_column(JSONB, nullable=False)
    member_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    frozen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "evidence_set_id",
            "version",
            name="uq_evidence_set_version_set_version",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"EvidenceSetVersion(id={self.id!r}, "
            f"evidence_set_id={self.evidence_set_id!r}, "
            f"version={self.version!r}, member_count={self.member_count!r})"
        )


class TransformationRecipe(Base):
    """推导配方实体（对应 transformation_recipe 表）。

    稳定身份表：一个配方一行，status 从 draft 到 published 到 deprecated。
    code 在组织内唯一。

    Attributes:
        id: 配方 UUID（PK）。
        organization_id: 所属组织 ID。
        code: 配方代码（组织内唯一）。
        display_name: 显示名称。
        status: 状态（draft / published / deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    __tablename__ = "transformation_recipe"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
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

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id",
            "code",
            name="uq_transformation_recipe_org_code",
        ),
    )

    def __repr__(self) -> str:
        return f"TransformationRecipe(id={self.id!r}, code={self.code!r}, status={self.status!r})"


class TransformationRecipeVersion(Base):
    """推导配方版本实体（对应 transformation_recipe_version 表）。

    不可变：发布后参数固定，保证确定性回放。
    version 号在配方范围内递增。

    Attributes:
        id: 版本 UUID（PK）。
        recipe_id: 配方 ID（FK→transformation_recipe）。
        version: 版本号。
        component_name: 执行器组件名称。
        component_version: 执行器组件版本。
        parameters: 算法参数（JSONB）。
        random_seed: 随机种子（保证确定性回放）。
        output_definitions: 输出定义列表（JSONB，数组 of strings）。
        status: 状态（published）。
        created_at: 创建时间。
        published_at: 发布时间。
    """

    __tablename__ = "transformation_recipe_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    recipe_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("transformation_recipe.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    component_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    component_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    random_seed: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    output_definitions: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'published'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "recipe_id",
            "version",
            name="uq_recipe_version_recipe_version",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"TransformationRecipeVersion(id={self.id!r}, "
            f"recipe_id={self.recipe_id!r}, version={self.version!r}, "
            f"component_name={self.component_name!r}, "
            f"component_version={self.component_version!r})"
        )


class DerivationRun(Base):
    """推导运行实体（对应 derivation_run 表）。

    一次配方在证据集版本上的执行记录。output_digest 是输出的 SHA-256
    摘要，相同证据 + 相同配方 → 相同 output_digest（确定性）。

    Attributes:
        id: 运行 UUID（PK）。
        organization_id: 所属组织 ID。
        evidence_set_version_id: 证据集版本 ID（FK→evidence_set_version）。
        recipe_version_id: 配方版本 ID（FK→transformation_recipe_version）。
        job_id: 关联作业 ID（可选，FK→job）。
        status: 状态（pending / running / succeeded / failed）。
        output_digest: 输出 SHA-256 摘要。
        outputs: 输出列表（JSONB，数组 of ParameterCandidateOutput dict）。
        started_at: 开始时间。
        completed_at: 完成时间。
        error: 错误信息（失败时填充）。
        created_at: 创建时间。
    """

    __tablename__ = "derivation_run"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    evidence_set_version_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("evidence_set_version.id"),
        nullable=False,
    )
    recipe_version_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("transformation_recipe_version.id"),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("job.id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'pending'"),
    )
    output_digest: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    outputs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"DerivationRun(id={self.id!r}, status={self.status!r}, "
            f"output_digest={self.output_digest!r})"
        )


class ProvenanceEdge(Base):
    """溯源图边实体（对应 provenance_edge 表）。

    连接溯源图节点：事实、中间工件、推导运行、参数版本。
    支持从推导结果向上追溯到原始事实，也支持向下遍历到参数版本。

    Attributes:
        id: 边 UUID（PK）。
        organization_id: 所属组织 ID。
        derivation_run_id: 推导运行 ID（FK→derivation_run）。
        source_type: 源节点类型（fact /
            intermediate_artifact / derivation_run）。
        source_id: 源节点 UUID。
        target_type: 目标节点类型（fact /
            intermediate_artifact / derivation_run / parameter_version）。
        target_id: 目标节点 UUID。
        edge_type: 边类型（selected_from / transformed_by / produced /
            published_as）。
        metadata: 元数据（JSONB）。
        created_at: 创建时间。
    """

    __tablename__ = "provenance_edge"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    derivation_run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("derivation_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    target_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    edge_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.Index(
            "ix_provenance_edge_source",
            "source_type",
            "source_id",
        ),
        sa.Index(
            "ix_provenance_edge_target",
            "target_type",
            "target_id",
        ),
        sa.Index(
            "ix_provenance_edge_derivation_run_id",
            "derivation_run_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"ProvenanceEdge(source_type={self.source_type!r}, "
            f"source_id={self.source_id!r}, "
            f"target_type={self.target_type!r}, "
            f"target_id={self.target_id!r}, "
            f"edge_type={self.edge_type!r})"
        )
