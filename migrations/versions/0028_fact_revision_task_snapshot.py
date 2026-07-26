"""0028: fact_revision 新增任务信息快照字段

入库时保存任务编码/名称/部门名称快照，避免查询时的多表 JOIN 反查。

原始 SQL:
  ALTER TABLE fact_revision ADD COLUMN IF NOT EXISTS task_code TEXT NULL;
  ALTER TABLE fact_revision ADD COLUMN IF NOT EXISTS task_name TEXT NULL;
  ALTER TABLE fact_revision ADD COLUMN IF NOT EXISTS department_name TEXT NULL;

Revision ID: 0028_fact_revision_task_snapshot
Revises: 0027_flow_definition_dept_project
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact_revision", sa.Column("task_code", sa.TEXT, nullable=True))
    op.add_column("fact_revision", sa.Column("task_name", sa.TEXT, nullable=True))
    op.add_column("fact_revision", sa.Column("department_name", sa.TEXT, nullable=True))


def downgrade() -> None:
    op.drop_column("fact_revision", "department_name")
    op.drop_column("fact_revision", "task_name")
    op.drop_column("fact_revision", "task_code")
