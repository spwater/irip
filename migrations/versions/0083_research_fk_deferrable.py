"""0083: 研究域 NO ACTION FK 改为 DEFERRABLE INITIALLY DEFERRED

修复删除 research_workspace 时因 CASCADE 处理顺序与 NO ACTION 约束
冲突导致的 ForeignKeyViolation 500 错误。

根因：
  PostgreSQL 默认 NO ACTION 约束在 NOT DEFERRABLE 时等同于 RESTRICT
  （立即检查）。当 CASCADE 从 research_workspace 同时删除多个子表时，
  如果被引用表（如 research_analysis_run）的行先于引用表（如
  research_insight_version）的行被删除，NO ACTION 约束立即触发报错。

  典型场景：删除 workspace → CASCADE 删 research_analysis_run →
  CASCADE 删 research_insight（→ CASCADE 删 research_insight_version）
  如果 research_analysis_run 先被处理，research_insight_version 行
  仍引用其 source_run_id，NO ACTION 立即报错。

修复：
  将研究域内 17 个 NO ACTION FK 全部改为 DEFERRABLE INITIALLY DEFERRED，
  使约束检查延迟到事务结束时执行。此时所有 CASCADE 删除已完成，
  引用行已不存在，约束自然通过。

  不使用 ON DELETE SET NULL / CASCADE，因为这些是溯源引用，
  语义上不应在 run 被删时级联删产物或置空溯源链。

Revision ID: 0083
Revises: 0082
Create Date: 2026-08-10
"""

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


#: 需要改为 DEFERRABLE INITIALLY DEFERRED 的约束列表
#: (table_name, constraint_name)
_CONSTRAINTS: list[tuple[str, str]] = [
    # research_analysis_run -> plan_version / snapshot
    ("research_analysis_run", "research_analysis_run_plan_version_id_fkey"),
    ("research_analysis_run", "research_analysis_run_snapshot_id_fkey"),
    # research_ai_conversation -> run
    ("research_ai_conversation", "research_ai_conversation_run_id_fkey"),
    # research_derived_dataset -> run
    ("research_derived_dataset", "research_derived_dataset_source_run_id_fkey"),
    # research_derived_dataset_version -> run / step / artifact
    ("research_derived_dataset_version", "research_derived_dataset_version_source_run_id_fkey"),
    ("research_derived_dataset_version", "research_derived_dataset_version_source_step_id_fkey"),
    ("research_derived_dataset_version", "research_derived_dataset_version_source_artifact_id_fkey"),
    # research_insight -> run
    ("research_insight", "research_insight_source_run_id_fkey"),
    # research_insight_version -> run
    ("research_insight_version", "research_insight_version_source_run_id_fkey"),
    # research_insight_candidate -> step
    ("research_insight_candidate", "research_insight_candidate_step_id_fkey"),
    # research_knowledge_reference -> run / step
    ("research_knowledge_reference", "research_knowledge_reference_run_id_fkey"),
    ("research_knowledge_reference", "research_knowledge_reference_step_id_fkey"),
    # research_view -> run
    ("research_view", "research_view_source_run_id_fkey"),
    # research_view_version -> run / step / artifact / chart_artifact
    ("research_view_version", "research_view_version_source_run_id_fkey"),
    ("research_view_version", "research_view_version_source_step_id_fkey"),
    ("research_view_version", "research_view_version_source_artifact_id_fkey"),
    ("research_view_version", "research_view_version_chart_code_artifact_id_fkey"),
]


def upgrade() -> None:
    """将研究域 NO ACTION FK 改为 DEFERRABLE INITIALLY DEFERRED。"""
    for table, constraint in _CONSTRAINTS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER CONSTRAINT {constraint} DEFERRABLE INITIALLY DEFERRED"
        )


def downgrade() -> None:
    """恢复为 NOT DEFERRABLE（原状态）。"""
    for table, constraint in _CONSTRAINTS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER CONSTRAINT {constraint} NOT DEFERRABLE"
        )
