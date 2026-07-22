"""department: 新增 parent_id 列支持多层级树形结构。

增量迁移（IRIP Task — 组织机构升级为多层级树形结构）：
- 新增 parent_id 列（nullable=True，顶级部门为 NULL）；
- 外键约束 fk_department_parent_id（自引用 department.id）；
- 索引 ix_department_parent_id 加速子部门查询。

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 parent_id 列、外键约束、索引。"""

    # ---- parent_id 列（nullable=True，顶级部门为 NULL）----
    op.add_column(
        "department",
        sa.Column("parent_id", sa.UUID, nullable=True),
    )

    # ---- 外键约束（自引用）----
    op.create_foreign_key(
        "fk_department_parent_id",
        "department",
        "department",
        ["parent_id"],
        ["id"],
    )

    # ---- 索引（加速按 parent_id 查子部门）----
    op.create_index("ix_department_parent_id", "department", ["parent_id"])


def downgrade() -> None:
    """回滚：删除索引、外键约束、parent_id 列。"""
    op.drop_index("ix_department_parent_id", table_name="department")
    op.drop_constraint("fk_department_parent_id", "department", type_="foreignkey")
    op.drop_column("department", "parent_id")
