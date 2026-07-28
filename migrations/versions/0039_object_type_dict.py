"""0039: object_type_dict 字典表 — 实验对象类型管理。

创建独立的 object_type_dict 表，存储 object_type 的 code（不可变唯一键）+
display_name（可改中文名）。industrial_object.object_type 仍然存 code，
改名时只改字典的 display_name，不影响关联关系。

Revision ID: 0039
Revises: 0038
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
    op.create_table(
        "object_type_dict",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("code", sa.TEXT, nullable=False, unique=True),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON object_type_dict TO irip_app")

    # 种子数据
    op.execute(
        "INSERT INTO object_type_dict (code, display_name, sort_order) VALUES "
        "('material', '物料', 1), "
        "('signal', '信号', 2)"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON object_type_dict FROM irip_app")
    op.drop_table("object_type_dict")
