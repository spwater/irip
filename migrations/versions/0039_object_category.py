"""0039: industrial_object 新增 category 字段 — 实验对象上层类别。

为 industrial_object 表添加 category TEXT 列（nullable），用于按业务类别
（如"生料"、"水泥"）分组实验对象。不创建独立分类表——category 是自由文本，
由前端通过下拉选择已有值或输入新值。

Revision ID: 0039
Revises: 0037
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "industrial_object",
        sa.Column("category", sa.Text, nullable=True),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON industrial_object TO irip_app")


def downgrade() -> None:
    op.drop_column("industrial_object", "category")
