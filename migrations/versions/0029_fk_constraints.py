"""0029: 数据库完整性约束 — department_id 外键 + flow_run_id 外键

1. flow_definition.department_id 加外键约束 → department.id (ON DELETE SET NULL)
   原始 SQL: ALTER TABLE flow_definition ADD CONSTRAINT fk_flow_definition_department
             FOREIGN KEY (department_id) REFERENCES department(id) ON DELETE SET NULL;

2. fact_revision 新增 flow_run_id 列 + 外键约束 → flow_run.id (ON DELETE SET NULL)
   原始 SQL: ALTER TABLE fact_revision ADD COLUMN flow_run_id UUID NULL;
             ALTER TABLE fact_revision ADD CONSTRAINT fk_fact_revision_flow_run
             FOREIGN KEY (flow_run_id) REFERENCES flow_run(id) ON DELETE SET NULL;

Revision ID: 0029_fk_constraints
Revises: 0028_fact_revision_task_snapshot
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. flow_definition.department_id → department.id
    op.create_foreign_key(
        "fk_flow_definition_department",
        "flow_definition",
        "department",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. fact_revision 新增 flow_run_id 列 + 外键
    op.add_column("fact_revision", sa.Column("flow_run_id", sa.UUID, nullable=True))
    op.create_foreign_key(
        "fk_fact_revision_flow_run",
        "fact_revision",
        "flow_run",
        ["flow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_fact_revision_flow_run", "fact_revision", type_="foreignkey")
    op.drop_column("fact_revision", "flow_run_id")
    op.drop_constraint("fk_flow_definition_department", "flow_definition", type_="foreignkey")
