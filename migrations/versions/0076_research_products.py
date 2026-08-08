"""0076: 研究产物表（衍生数据集 + 研究视图 + Insight + Insight候选）

创建 7 张研究域表 + 索引 + 唯一约束：
- research_derived_dataset: 衍生数据集稳定身份（可编辑元数据）
- research_derived_dataset_version: 衍生数据集版本（不可变，三段式数据 + field_manifest）
- research_view: 研究视图稳定身份（可编辑元数据）
- research_view_version: 研究视图版本（不可变，静态图 + 绘图代码引用 + 溯源）
- research_insight: Insight 稳定身份（可编辑 name）
- research_insight_version: Insight 版本（不可变，6 个必填字段 + AI 原稿 + 修改记录）
- research_insight_candidate: Insight 候选（由 Orchestrator 提取，用户接受/修改/拒绝）

模块隔离约定：
- 研究表以 research_ 前缀命名，与核心表完全分离；
- 研究表之间的 FK 使用 ON DELETE CASCADE；
- 跨模块逻辑引用不建 FK（bound_dataset_version_id / source_candidate_id / source_snapshot_id）；
- 版本实体表有 UNIQUE (parent_id, version_number) 约束；
- down migration 可完整回滚到 0075。

Revision ID: 0076
Revises: 0075
Create Date: 2026-08-06
"""

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 7 张研究产物表 + 索引 + 唯一约束。"""

    # ---- 1. research_derived_dataset ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_derived_dataset (
            id                 uuid PRIMARY KEY,
            workspace_id        uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            owner_user_id       uuid NOT NULL REFERENCES app_user(id),
            name               text NOT NULL,
            summary            text,
            tags               jsonb NOT NULL DEFAULT '[]'::jsonb,
            status             text NOT NULL DEFAULT 'confirmed',
            current_version    integer NOT NULL DEFAULT 0,
            source_run_id      uuid NOT NULL REFERENCES research_analysis_run(id),
            source_snapshot_id uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            lock_version       integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rdd_workspace_id ON research_derived_dataset (workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rdd_owner_user_id "
        "ON research_derived_dataset (owner_user_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rdd_status ON research_derived_dataset (status)")

    # ---- 2. research_derived_dataset_version ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_derived_dataset_version (
            id                  uuid PRIMARY KEY,
            dataset_id          uuid NOT NULL REFERENCES research_derived_dataset(id) ON DELETE CASCADE,
            version_number      integer NOT NULL,
            metadata_content    jsonb NOT NULL,
            points_content      jsonb NOT NULL DEFAULT '[]'::jsonb,
            series_content      jsonb NOT NULL DEFAULT '[]'::jsonb,
            field_manifest      jsonb NOT NULL DEFAULT '[]'::jsonb,
            source_run_id       uuid NOT NULL REFERENCES research_analysis_run(id),
            source_step_id      uuid REFERENCES research_analysis_step(id),
            source_artifact_id  uuid REFERENCES research_run_artifact(id),
            content_hash        text NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            created_by          uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rddv_dataset_id "
        "ON research_derived_dataset_version (dataset_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rddv_dataset_version "
        "ON research_derived_dataset_version (dataset_id, version_number)"
    )

    # ---- 3. research_view ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_view (
            id               uuid PRIMARY KEY,
            workspace_id     uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            owner_user_id    uuid NOT NULL REFERENCES app_user(id),
            name             text NOT NULL,
            caption          text,
            display_order    integer NOT NULL DEFAULT 0,
            status           text NOT NULL DEFAULT 'confirmed',
            current_version  integer NOT NULL DEFAULT 0,
            source_run_id    uuid NOT NULL REFERENCES research_analysis_run(id),
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            lock_version     integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rv_workspace_id ON research_view (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rv_owner_user_id ON research_view (owner_user_id)")

    # ---- 4. research_view_version ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_view_version (
            id                      uuid PRIMARY KEY,
            view_id                 uuid NOT NULL REFERENCES research_view(id) ON DELETE CASCADE,
            version_number          integer NOT NULL,
            image_storage_path      text NOT NULL,
            image_format            text NOT NULL DEFAULT 'png',
            image_width             integer,
            image_height            integer,
            image_content_hash      text NOT NULL,
            chart_code_artifact_id  uuid REFERENCES research_run_artifact(id),
            image_digest            text,
            source_run_id           uuid NOT NULL REFERENCES research_analysis_run(id),
            source_step_id          uuid REFERENCES research_analysis_step(id),
            source_artifact_id      uuid REFERENCES research_run_artifact(id),
            bound_dataset_version_id uuid,
            chart_description       text,
            created_at              timestamptz NOT NULL DEFAULT now(),
            created_by              uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rvv_view_id ON research_view_version (view_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rvv_view_version "
        "ON research_view_version (view_id, version_number)"
    )

    # ---- 5. research_insight ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_insight (
            id               uuid PRIMARY KEY,
            workspace_id     uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            owner_user_id    uuid NOT NULL REFERENCES app_user(id),
            name             text NOT NULL,
            status           text NOT NULL DEFAULT 'confirmed',
            current_version  integer NOT NULL DEFAULT 0,
            source_run_id    uuid REFERENCES research_analysis_run(id),
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            lock_version     integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ri_workspace_id ON research_insight (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ri_owner_user_id ON research_insight (owner_user_id)")

    # ---- 6. research_insight_version ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_insight_version (
            id                     uuid PRIMARY KEY,
            insight_id             uuid NOT NULL REFERENCES research_insight(id) ON DELETE CASCADE,
            version_number         integer NOT NULL,
            conclusion             text NOT NULL,
            scope                  text NOT NULL,
            evidence_refs          jsonb NOT NULL,
            method_refs            jsonb NOT NULL,
            confidence_level       text NOT NULL,
            limitations            text NOT NULL,
            evidence_source_label  text NOT NULL,
            ai_original_text       text,
            is_modified            boolean NOT NULL DEFAULT false,
            modification_note      text,
            source_candidate_id    uuid,
            source_run_id          uuid REFERENCES research_analysis_run(id),
            created_at             timestamptz NOT NULL DEFAULT now(),
            created_by             uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_riv_insight_id ON research_insight_version (insight_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_riv_insight_version "
        "ON research_insight_version (insight_id, version_number)"
    )

    # ---- 7. research_insight_candidate ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_insight_candidate (
            id                     uuid PRIMARY KEY,
            workspace_id           uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            run_id                 uuid NOT NULL REFERENCES research_analysis_run(id) ON DELETE CASCADE,
            step_id                uuid REFERENCES research_analysis_step(id),
            conclusion             text NOT NULL,
            scope                  text NOT NULL,
            evidence_refs          jsonb NOT NULL,
            method_refs            jsonb NOT NULL,
            confidence_level       text NOT NULL,
            limitations            text NOT NULL,
            evidence_source_label  text NOT NULL,
            ai_raw_text            text NOT NULL,
            status                 text NOT NULL DEFAULT 'pending',
            accepted_insight_id    uuid,
            rejection_reason       text,
            created_at             timestamptz NOT NULL DEFAULT now(),
            reviewed_at            timestamptz,
            reviewed_by            uuid REFERENCES app_user(id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ric_run_id ON research_insight_candidate (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ric_status ON research_insight_candidate (status)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ric_workspace_id "
        "ON research_insight_candidate (workspace_id)"
    )


def downgrade() -> None:
    """删除 7 张研究产物表（反序）。"""
    op.execute("DROP TABLE IF EXISTS research_insight_candidate")
    op.execute("DROP TABLE IF EXISTS research_insight_version")
    op.execute("DROP TABLE IF EXISTS research_insight")
    op.execute("DROP TABLE IF EXISTS research_view_version")
    op.execute("DROP TABLE IF EXISTS research_view")
    op.execute("DROP TABLE IF EXISTS research_derived_dataset_version")
    op.execute("DROP TABLE IF EXISTS research_derived_dataset")
