"""0027: flow_definition 新增 department_id 和 project_name 字段

将之前手动执行的 ALTER TABLE 补为正式迁移：
  ALTER TABLE flow_definition ADD COLUMN IF NOT EXISTS department_id UUID NULL;
  ALTER TABLE flow_definition ADD COLUMN IF NOT EXISTS project_name TEXT NULL;

Revision ID: 0027_flow_definition_dept_project
Revises: 0026_object_equipment_link
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 原始 SQL: ALTER TABLE flow_definition ADD COLUMN IF NOT EXISTS department_id UUID NULL;
    op.add_column(
        "flow_definition",
        sa.Column("department_id", sa.UUID, nullable=True),
    )
    # 原始 SQL: ALTER TABLE flow_definition ADD COLUMN IF NOT EXISTS project_name TEXT NULL;
    op.add_column(
        "flow_definition",
        sa.Column("project_name", sa.TEXT, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flow_definition", "project_name")
    op.drop_column("flow_definition", "department_id")
