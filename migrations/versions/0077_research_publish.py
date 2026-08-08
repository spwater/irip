"""0077: 研究发布与复用表（成果包 + 版本 + ACL修订 + 溯源边 + 收藏）

创建 5 张研究域表 + 索引 + 唯一约束 + 全文搜索索引：
- research_result: 研究成果包稳定身份（可编辑 name，状态 draft/published/archived）
- research_result_version: 成果包不可变发布版本（标题/摘要/标签/产物引用/权限包络/内容哈希）
- research_result_acl_revision: ACL 修订记录（仅追加，记录每次 ACL 变更）
- research_lineage_edge: 溯源边（仅追加，为阶段 5 联邦溯源提供数据源）
- research_result_favorite: 成果包收藏

模块隔离约定：
- 研究表以 research_ 前缀命名，与核心表完全分离；
- 研究表之间的 FK 使用 ON DELETE CASCADE；
- 跨模块逻辑引用不建 FK（evidence_snapshot_ids / analysis_run_ids 等）；
- 版本实体表有 UNIQUE (result_id, version_number) 约束；
- 全文搜索 tsvector 基于 title / summary 生成（tags 在应用层用 ILIKE 搜索）；
- down migration 可完整回滚到 0076。

Revision ID: 0077
Revises: 0076
Create Date: 2026-08-06
"""

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 5 张研究发布表 + 索引 + 唯一约束 + 全文搜索索引。"""

    # ---- 1. research_result ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_result (
            id                       uuid PRIMARY KEY,
            workspace_id             uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            owner_user_id            uuid NOT NULL REFERENCES app_user(id),
            name                     text NOT NULL,
            status                   text NOT NULL DEFAULT 'published',
            current_version          integer NOT NULL DEFAULT 0,
            current_acl_type         text NOT NULL DEFAULT 'private',
            current_explicit_user_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at               timestamptz NOT NULL DEFAULT now(),
            updated_at               timestamptz NOT NULL DEFAULT now(),
            lock_version             integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rr_workspace_id ON research_result (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rr_owner_user_id ON research_result (owner_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rr_status ON research_result (status)")

    # ---- 2. research_result_version ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_result_version (
            id                          uuid PRIMARY KEY,
            result_id                   uuid NOT NULL REFERENCES research_result(id) ON DELETE CASCADE,
            version_number              integer NOT NULL,
            title                       text NOT NULL,
            summary                     text,
            tags                        jsonb NOT NULL DEFAULT '[]'::jsonb,
            release_notes               text,
            dataset_version_refs        jsonb NOT NULL DEFAULT '[]'::jsonb,
            view_version_refs           jsonb NOT NULL DEFAULT '[]'::jsonb,
            insight_version_refs        jsonb NOT NULL DEFAULT '[]'::jsonb,
            evidence_snapshot_ids       jsonb NOT NULL DEFAULT '[]'::jsonb,
            analysis_run_ids            jsonb NOT NULL DEFAULT '[]'::jsonb,
            source_run_statuses         jsonb NOT NULL DEFAULT '{}'::jsonb,
            publisher                   uuid NOT NULL REFERENCES app_user(id),
            published_at                timestamptz NOT NULL DEFAULT now(),
            content_hash                text NOT NULL,
            published_permission_envelope jsonb NOT NULL DEFAULT '{}'::jsonb,
            status                      text NOT NULL DEFAULT 'active',
            created_at                  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rrv_result_id ON research_result_version (result_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rrv_result_version "
        "ON research_result_version (result_id, version_number)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rrv_status ON research_result_version (status)")
    # 全文搜索 tsvector + GIN 索引（仅 title + summary；tags 在应用层用 ILIKE 搜索）
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_rrv_fts
        ON research_result_version
        USING GIN (
            to_tsvector('simple',
                coalesce(title, '') || ' ' || coalesce(summary, '')
            )
        )
        """
    )

    # ---- 3. research_result_acl_revision ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_result_acl_revision (
            id                          uuid PRIMARY KEY,
            result_id                   uuid NOT NULL REFERENCES research_result(id) ON DELETE CASCADE,
            revision_number             integer NOT NULL,
            acl_type                    text NOT NULL,
            explicit_user_ids           jsonb NOT NULL DEFAULT '[]'::jsonb,
            previous_acl_type           text,
            previous_explicit_user_ids  jsonb,
            changed_by                  uuid NOT NULL REFERENCES app_user(id),
            changed_at                  timestamptz NOT NULL DEFAULT now(),
            change_reason               text,
            is_declassify               boolean NOT NULL DEFAULT false,
            declassify_reason           text
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rrar_result_id ON research_result_acl_revision (result_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rrar_result_revision "
        "ON research_result_acl_revision (result_id, revision_number)"
    )

    # ---- 4. research_lineage_edge ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_lineage_edge (
            id                uuid PRIMARY KEY,
            source_namespace  text NOT NULL,
            source_id         uuid NOT NULL,
            source_version    integer,
            target_namespace  text NOT NULL,
            target_id         uuid NOT NULL,
            target_version    integer,
            edge_type         text NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rle_source "
        "ON research_lineage_edge (source_namespace, source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rle_target "
        "ON research_lineage_edge (target_namespace, target_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rle_edge_type ON research_lineage_edge (edge_type)")

    # ---- 5. research_result_favorite ----
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_result_favorite (
            id          uuid PRIMARY KEY,
            result_id   uuid NOT NULL REFERENCES research_result(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES app_user(id),
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rrf_result_user "
        "ON research_result_favorite (result_id, user_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rrf_user_id ON research_result_favorite (user_id)")


def downgrade() -> None:
    """删除 5 张研究发布表（反序）。"""
    op.execute("DROP TABLE IF EXISTS research_result_favorite")
    op.execute("DROP TABLE IF EXISTS research_lineage_edge")
    op.execute("DROP TABLE IF EXISTS research_result_acl_revision")
    op.execute("DROP TABLE IF EXISTS research_result_version")
    op.execute("DROP TABLE IF EXISTS research_result")
