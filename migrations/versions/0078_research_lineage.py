"""0078: 统一溯源与知识接口表（research_knowledge_reference）

创建 1 张研究域表 + 索引：
- research_knowledge_reference: 知识引用快照（仅追加，保存 AI 引用知识库时的段落快照、文档版本和哈希）

模块隔离约定：
- 研究表以 research_ 前缀命名，与核心表完全分离；
- 研究表之间的 FK 使用 ON DELETE CASCADE（workspace_id → research_workspace）；
- insight_id 为逻辑引用（不建 FK），因为 Insight 可能在知识引用保存时为空；
- snippet_text ≤4KB 直接存储，>4KB 存 MinIO（snippet_storage_path 记录路径）；
- research_lineage_edge 表结构不变（阶段 4 已创建），阶段 5 新增 edge_type 通过应用层使用。

Revision ID: 0078
Revises: 0077
Create Date: 2026-08-06
"""

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 research_knowledge_reference 表 + 索引。"""

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS research_knowledge_reference (
            id                        uuid PRIMARY KEY,
            workspace_id              uuid NOT NULL REFERENCES research_workspace(id) ON DELETE CASCADE,
            run_id                    uuid NOT NULL REFERENCES research_analysis_run(id),
            step_id                   uuid REFERENCES research_analysis_step(id),
            insight_id               uuid,
            document_id               text NOT NULL,
            document_version          text NOT NULL,
            title                     text NOT NULL,
            section                   text,
            page                      integer,
            chunk_id                  text,
            snippet_text              text,
            snippet_storage_path      text,
            content_hash              text NOT NULL,
            source_uri                text NOT NULL,
            retrieval_time            timestamptz NOT NULL DEFAULT now(),
            provider_name             text NOT NULL,
            research_question_context text,
            created_at                timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rkr_workspace_id "
        "ON research_knowledge_reference (workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rkr_run_id "
        "ON research_knowledge_reference (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rkr_insight_id "
        "ON research_knowledge_reference (insight_id) "
        "WHERE insight_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rkr_document "
        "ON research_knowledge_reference (document_id, document_version)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rkr_run_step "
        "ON research_knowledge_reference (run_id, step_id) "
        "WHERE step_id IS NOT NULL"
    )


def downgrade() -> None:
    """删除 research_knowledge_reference 表。"""
    op.execute("DROP TABLE IF EXISTS research_knowledge_reference CASCADE")
