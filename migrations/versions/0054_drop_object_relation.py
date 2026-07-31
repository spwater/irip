"""Drop object_relation table and industrial_object.parent_id (unused, 0 rows).

Revision ID: 0054
Revises: 0053
Create Date: 2026-07-31

删除原因:
  object_relation 表自创建以来从未使用（0 条数据）。
  该表设计用于存储实验对象间的关系（contains/connected_to/upstream_of/downstream_of/measures/simulates/equivalent_to），
  但从未有用户创建过任何关系数据，对应的前端对象关系图页面也无实际使用。
  同时删除 industrial_object.parent_id 字段（预留未用，所有对象 parent_id 均为 NULL）。
  ObjectGraphService 保留了 object CRUD 方法，仅删除了 relation/descendants 相关方法。
"""
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("object_relation")
    op.drop_column("industrial_object", "parent_id")


def downgrade() -> None:
    # 恢复 parent_id
    op.execute("ALTER TABLE industrial_object ADD COLUMN parent_id UUID")
    # 恢复 object_relation 表
    op.execute("""
        CREATE TABLE object_relation (
            id UUID NOT NULL PRIMARY KEY,
            organization_id UUID NOT NULL,
            source_id UUID NOT NULL REFERENCES industrial_object(id) ON DELETE CASCADE,
            target_id UUID NOT NULL REFERENCES industrial_object(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            lock_version INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT ck_object_relation_no_self CHECK (source_id != target_id)
        )
    """)
