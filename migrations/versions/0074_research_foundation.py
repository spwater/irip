"""0074: 研究域基础表（研究工作空间 + 问题版本 + 证据引用 + 证据快照）

创建 4 张研究域表 + 索引 + 唯一约束：
- research_workspace: 研究工作空间主表
- research_question_version: 研究问题版本（不可变）
- research_workspace_evidence_ref: 证据引用（逻辑引用核心 Fact）
- research_evidence_snapshot: 证据快照（不可变）

模块隔离约定：
- 研究表以 research_ 前缀命名，与核心表完全分离；
- 研究表之间的 FK 使用 ON DELETE CASCADE；
- 跨模块引用（source_id）不建 FK，纯 GUID 列；
- down migration 可完整回滚到 0073。

Revision ID: 0074
Revises: 0073
Create Date: 2026-08-05
"""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 4 张研究域表 + 索引 + 唯一约束。"""

    # ---- 1. research_workspace ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_workspace (
            id                   uuid PRIMARY KEY,
            owner_user_id        uuid NOT NULL REFERENCES app_user(id),
            department_id        uuid NOT NULL REFERENCES department(id),
            name                 text NOT NULL,
            status               text NOT NULL DEFAULT 'draft',
            current_question_version integer NOT NULL DEFAULT 0,
            forked_from_id       uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            lock_version         integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_research_workspace_owner_user_id "
        "ON research_workspace (owner_user_id)"
    )

    # ---- 2. research_question_version ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_question_version (
            id             uuid PRIMARY KEY,
            workspace_id   uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            version_number integer NOT NULL,
            question_text  text NOT NULL,
            sub_questions  jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_research_question_version_workspace_id "
        "ON research_question_version (workspace_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rqv_workspace_version "
        "ON research_question_version (workspace_id, version_number)"
    )

    # ---- 3. research_workspace_evidence_ref ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_workspace_evidence_ref (
            id               uuid PRIMARY KEY,
            workspace_id     uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            source_namespace text NOT NULL,
            source_id        uuid NOT NULL,
            source_version   text,
            source_name      text,
            added_at         timestamptz NOT NULL DEFAULT now(),
            added_by         uuid NOT NULL REFERENCES app_user(id),
            status           text NOT NULL DEFAULT 'active'
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_research_evidence_ref_workspace_id "
        "ON research_workspace_evidence_ref (workspace_id)"
    )
    # Partial unique index: 同一 Workspace 中同一 source_namespace + source_id 仅允许一个 active 引用
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_ref_workspace_source "
        "ON research_workspace_evidence_ref (workspace_id, source_namespace, source_id) "
        "WHERE status = 'active'"
    )

    # ---- 4. research_evidence_snapshot ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_evidence_snapshot (
            id                 uuid PRIMARY KEY,
            workspace_id       uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            snapshot_number    integer NOT NULL,
            content_hash       text NOT NULL,
            captured_at        timestamptz NOT NULL DEFAULT now(),
            permission_envelope jsonb NOT NULL,
            field_manifest      jsonb NOT NULL,
            source_refs        jsonb NOT NULL,
            created_by          uuid NOT NULL REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_research_snapshot_workspace_id "
        "ON research_evidence_snapshot (workspace_id)"
    )


def downgrade() -> None:
    """删除全部研究域表（反序 DROP）。"""

    op.execute("DROP TABLE IF EXISTS research_evidence_snapshot CASCADE")
    op.execute("DROP TABLE IF EXISTS research_workspace_evidence_ref CASCADE")
    op.execute("DROP TABLE IF EXISTS research_question_version CASCADE")
    op.execute("DROP TABLE IF EXISTS research_workspace CASCADE")
