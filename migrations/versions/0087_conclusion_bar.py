"""0087: Research conclusion bar — push-to-bar workspace aggregation.

Creates the ``research_conclusion_bar_item`` table used to aggregate pushed
report blocks across turns within a workspace for the "结论栏" feature.

New table:
  research_conclusion_bar_item
    - workspace_id (FK research_workspace, CASCADE) — aggregation dimension
    - turn_id (FK research_turn, CASCADE) — provenance only
    - block_type (echarts | chart_ref | structured | table | text)
    - title, content_snapshot (JSONB), source_info (JSONB)
    - created_by (FK app_user)
    - created_at / updated_at
    - INDEX idx_bar_item_workspace ON (workspace_id, created_at DESC)

Rev ID: 0087
Revises: 0086
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create research_conclusion_bar_item table."""
    op.create_table(
        "research_conclusion_bar_item",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_turn.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_type", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content_snapshot", JSONB, nullable=False),
        sa.Column("source_info", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_bar_item_workspace",
        "research_conclusion_bar_item",
        ["workspace_id", sa.text("created_at DESC")],
    )

    # Grant permissions to application roles
    for role in ("irip_app", "irip_runtime"):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE research_conclusion_bar_item TO {role}"
        )


def downgrade() -> None:
    """Drop research_conclusion_bar_item table."""
    op.drop_index("idx_bar_item_workspace", table_name="research_conclusion_bar_item")
    op.drop_table("research_conclusion_bar_item")
