"""0075: 可信执行表（分析计划版本 + 分析 Run + 步骤 + 工件 + AI 对话 + 研究记忆文档）

创建 6 张研究域表 + 索引 + 唯一约束：
- research_analysis_plan_version: 分析计划版本（不可变 DAG 结构）
- research_analysis_run: 分析运行（状态机 + 部分唯一索引保证每 Workspace 最多 1 个活跃 Run）
- research_analysis_step: 分析步骤（高频状态更新表）
- research_run_artifact: 运行工件（白名单扫描后持久化）
- research_ai_conversation: AI 对话历史
- research_memory_document: 后台研究记忆文档（每 Workspace 一行）

模块隔离约定：
- 研究表以 research_ 前缀命名，与核心表完全分离；
- 研究表之间的 FK 使用 ON DELETE CASCADE；
- 跨模块引用不建 FK；
- down migration 可完整回滚到 0074。

Revision ID: 0075
Revises: 0074
Create Date: 2026-08-05
"""

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 6 张研究域表 + 索引 + 唯一约束。"""

    # ---- 1. research_analysis_plan_version ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_analysis_plan_version (
            id                 uuid PRIMARY KEY,
            workspace_id       uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            version_number     integer NOT NULL,
            dag_structure      jsonb NOT NULL,
            coverage_declaration jsonb,
            status             text NOT NULL DEFAULT 'draft',
            confirmed_at       timestamptz,
            confirmed_by       uuid REFERENCES app_user(id),
            created_at         timestamptz NOT NULL DEFAULT now(),
            created_by         uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rapv_workspace_id "
        "ON research_analysis_plan_version (workspace_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rapv_workspace_version "
        "ON research_analysis_plan_version (workspace_id, version_number)"
    )

    # ---- 2. research_analysis_run ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_analysis_run (
            id               uuid PRIMARY KEY,
            workspace_id     uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            plan_version_id  uuid NOT NULL REFERENCES research_analysis_plan_version(id),
            snapshot_id      uuid NOT NULL REFERENCES research_evidence_snapshot(id),
            run_number       integer NOT NULL,
            status           text NOT NULL DEFAULT 'queued',
            queue_position   integer,
            submitted_at     timestamptz NOT NULL DEFAULT now(),
            started_at       timestamptz,
            completed_at     timestamptz,
            cancelled_at     timestamptz,
            cancelled_by     uuid REFERENCES app_user(id),
            error_summary    text,
            coverage_summary jsonb,
            image_digest     text NOT NULL,
            created_by       uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rar_workspace_id "
        "ON research_analysis_run (workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rar_status "
        "ON research_analysis_run (status)"
    )
    # 部分唯一索引：每 Workspace 最多 1 个活跃 Run
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rar_workspace_active "
        "ON research_analysis_run (workspace_id) "
        "WHERE status IN ('queued', 'planning', 'running')"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rar_workspace_run "
        "ON research_analysis_run (workspace_id, run_number)"
    )

    # ---- 3. research_analysis_step ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_analysis_step (
            id                  uuid PRIMARY KEY,
            run_id              uuid NOT NULL REFERENCES research_analysis_run(id) ON DELETE CASCADE,
            step_key            text NOT NULL,
            step_index          integer NOT NULL,
            status              text NOT NULL DEFAULT 'pending',
            method              text NOT NULL,
            analysis_mode       text,
            data_budget_tokens  integer,
            coverage_rate       float,
            llm_read_rate       float,
            is_sampled          boolean NOT NULL DEFAULT false,
            mode_reason         text,
            attempt_count       integer NOT NULL DEFAULT 0,
            started_at          timestamptz,
            completed_at        timestamptz,
            error_message       text,
            error_classification text,
            depends_on          jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ras_run_id "
        "ON research_analysis_step (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ras_run_status "
        "ON research_analysis_step (run_id, status)"
    )

    # ---- 4. research_run_artifact ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_run_artifact (
            id              uuid PRIMARY KEY,
            run_id          uuid NOT NULL REFERENCES research_analysis_run(id) ON DELETE CASCADE,
            step_id         uuid REFERENCES research_analysis_step(id) ON DELETE CASCADE,
            artifact_type   text NOT NULL,
            artifact_key    text NOT NULL,
            storage_path    text NOT NULL,
            content_hash    text,
            size_bytes      bigint,
            is_publishable  boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rra_run_id "
        "ON research_run_artifact (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rra_step_id "
        "ON research_run_artifact (step_id)"
    )

    # ---- 5. research_ai_conversation ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_ai_conversation (
            id            uuid PRIMARY KEY,
            workspace_id  uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            role          text NOT NULL,
            content       jsonb NOT NULL,
            run_id        uuid REFERENCES research_analysis_run(id),
            created_at    timestamptz NOT NULL DEFAULT now(),
            created_by    uuid REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rac_workspace_id "
        "ON research_ai_conversation (workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rac_run_id "
        "ON research_ai_conversation (run_id)"
    )

    # ---- 6. research_memory_document ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_memory_document (
            id            uuid PRIMARY KEY,
            workspace_id  uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            document      jsonb NOT NULL DEFAULT '{}'::jsonb,
            version       integer NOT NULL DEFAULT 1,
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rmd_workspace "
        "ON research_memory_document (workspace_id)"
    )


def downgrade() -> None:
    """删除全部可信执行表（反序 DROP）。"""

    op.execute("DROP TABLE IF EXISTS research_memory_document CASCADE")
    op.execute("DROP TABLE IF EXISTS research_ai_conversation CASCADE")
    op.execute("DROP TABLE IF EXISTS research_run_artifact CASCADE")
    op.execute("DROP TABLE IF EXISTS research_analysis_step CASCADE")
    op.execute("DROP TABLE IF EXISTS research_analysis_run CASCADE")
    op.execute("DROP TABLE IF EXISTS research_analysis_plan_version CASCADE")
