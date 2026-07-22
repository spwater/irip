"""industrial_objects: industrial_object + object_relation.

增量迁移（IRIP Task 11）：
- 创建 industrial_object 表：工业对象主表，code 组织内+类型内唯一；
- 创建 object_relation 表：对象间关系，活跃时 (source, target, type) 唯一，禁止自关联；
- 索引：ix_industrial_object_org_type_code (organization_id, object_type, code) UNIQUE、
  ix_industrial_object_organization_id、ix_object_relation_source、
  ix_object_relation_target、ix_object_relation_org_type；
- 部分唯一索引：(source_id, target_id, relation_type) WHERE is_active = true；
- CHECK 约束：source_id != target_id（禁止自关联）；
- FK：object_relation.source_id → industrial_object.id、
  object_relation.target_id → industrial_object.id；
- irip_app GRANT 两表权限。

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 industrial_object + object_relation 表。"""

    # ---- industrial_object 表 ----
    op.create_table(
        "industrial_object",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("object_type", sa.TEXT, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=True),
        sa.Column("parent_id", sa.UUID, nullable=True),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'active'"),
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
        sa.Column(
            "lock_version",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "object_type",
            "code",
            name="uq_industrial_object_org_type_code",
        ),
    )
    op.create_index(
        "ix_industrial_object_org_type_code",
        "industrial_object",
        ["organization_id", "object_type", "code"],
        unique=True,
    )
    op.create_index(
        "ix_industrial_object_organization_id",
        "industrial_object",
        ["organization_id"],
    )

    # ---- object_relation 表 ----
    op.create_table(
        "object_relation",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("source_id", sa.UUID, nullable=False),
        sa.Column("target_id", sa.UUID, nullable=False),
        sa.Column("relation_type", sa.TEXT, nullable=False),
        sa.Column(
            "is_active",
            sa.BOOLEAN,
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["industrial_object.id"],
            name="fk_object_relation_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["industrial_object.id"],
            name="fk_object_relation_target_id",
        ),
        sa.CheckConstraint(
            "source_id != target_id",
            name="ck_object_relation_no_self",
        ),
    )
    op.create_index(
        "ix_object_relation_source",
        "object_relation",
        ["source_id"],
    )
    op.create_index(
        "ix_object_relation_target",
        "object_relation",
        ["target_id"],
    )
    op.create_index(
        "ix_object_relation_org_type",
        "object_relation",
        ["organization_id", "relation_type"],
    )
    # 部分唯一索引：活跃关系的 (source, target, type) 唯一
    op.execute(
        "CREATE UNIQUE INDEX ix_object_relation_active_unique "
        "ON object_relation (source_id, target_id, relation_type) "
        "WHERE is_active = true"
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON industrial_object, object_relation TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除两张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON industrial_object, object_relation FROM irip_app"
    )

    op.execute("DROP INDEX IF EXISTS ix_object_relation_active_unique")
    op.drop_index("ix_object_relation_org_type", table_name="object_relation")
    op.drop_index("ix_object_relation_target", table_name="object_relation")
    op.drop_index("ix_object_relation_source", table_name="object_relation")
    op.drop_table("object_relation")

    op.drop_index("ix_industrial_object_organization_id", table_name="industrial_object")
    op.drop_index("ix_industrial_object_org_type_code", table_name="industrial_object")
    op.drop_table("industrial_object")
