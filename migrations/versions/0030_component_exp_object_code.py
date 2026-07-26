"""0030: component_version 新增 experimental_object_code 独立列

将实验对象编码从 manifest YAML 中抽离为独立数据库列，便于查询关联。
原始 SQL: ALTER TABLE component_version ADD COLUMN IF NOT EXISTS experimental_object_code TEXT NULL;

Revision ID: 0030_component_exp_object_code
Revises: 0029_fk_constraints
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("component_version", sa.Column("experimental_object_code", sa.TEXT, nullable=True))


def downgrade() -> None:
    op.drop_column("component_version", "experimental_object_code")
