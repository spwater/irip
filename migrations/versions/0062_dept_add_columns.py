"""0062: 多租户隔离键升级 — 阶段1加列

为 A/B 类表添加 department_id 列（先 NULL），A 类表额外添加
visible_departments / visibility_scope / owner_user_id。
同时创建 root / system 哨兵部门行，并将 department 表唯一约束从
(organization_id, code) 改为 (parent_id, code)。

表分类：
- A 类（补 4 列）：fact, parameter, evidence_set, artifact, model,
  transformation_recipe, component, flow_definition, industrial_object, equipment
- B 类（补 1 列）：job, flow_run, derivation_run, audit_event, scope_grant,
  secret, backup_record, app_user
- C 类（无租户列无 RLS）：provenance_edge, object_relation, object_type_dict,
  department 自身

Revision ID: 0062
Revises: 0001
Create Date: 2026-08-20
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0001"
branch_labels = None
depends_on = None

# ---- A 类表新增列（除已有列） ----
# fact: 全部 4 列新增
# parameter: 全部 4 列新增
# evidence_set: 全部 4 列新增
# artifact: 全部 4 列新增
# model: 全部 4 列新增
# transformation_recipe: 全部 4 列新增
# component: 全部 4 列新增
# flow_definition: department_id 已有（nullable），新增其余 3 列
# industrial_object: department_id + visible_departments 已有，新增其余 2 列
# equipment: department_id + visible_departments 已有，新增其余 2 列

# ---- B 类表新增列 ----
# job: 新增 department_id
# flow_run: 新增 department_id
# derivation_run: 新增 department_id
# audit_event: 新增 department_id
# secret: 新增 department_id
# backup_record: 新增 department_id
# scope_grant: department_id 已有（nullable），沿用
# app_user: department_id 已有（nullable），沿用

#: 需要新增全部 4 列的 A 类表
_A_FULL_TABLES: list[str] = [
    "fact",
    "parameter",
    "evidence_set",
    "artifact",
    "model",
    "transformation_recipe",
    "component",
]

#: 仅需新增 visible_departments + visibility_scope + owner_user_id 的 A 类表
_A_PARTIAL_FLOW: list[str] = ["flow_definition"]

#: 仅需新增 visibility_scope + owner_user_id 的 A 类表（department_id + visible_departments 已有）
_A_PARTIAL_EQUIP: list[str] = ["industrial_object", "equipment"]

#: 需要新增 department_id 的 B 类表
_B_TABLES: list[str] = [
    "job",
    "flow_run",
    "derivation_run",
    "audit_event",
    "secret",
    "backup_record",
]


def upgrade() -> None:
    """加列（先 NULL）+ 哨兵部门 + 唯一约束改 (parent_id, code)。"""

    # === 1. A 类表新增 4 列（全部新增） ===
    for table in _A_FULL_TABLES:
        op.add_column(
            table,
            sa.Column("department_id", sa.UUID(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("visible_departments", sa.dialects.postgresql.JSONB(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("visibility_scope", sa.String(10), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("owner_user_id", sa.UUID(), nullable=True),
        )

    # === 2. A 类表部分新增（flow_definition 已有 department_id） ===
    for table in _A_PARTIAL_FLOW:
        op.add_column(
            table,
            sa.Column("visible_departments", sa.dialects.postgresql.JSONB(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("visibility_scope", sa.String(10), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("owner_user_id", sa.UUID(), nullable=True),
        )

    # === 3. A 类表部分新增（equipment / industrial_object 已有 department_id + visible_departments） ===
    for table in _A_PARTIAL_EQUIP:
        op.add_column(
            table,
            sa.Column("visibility_scope", sa.String(10), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("owner_user_id", sa.UUID(), nullable=True),
        )

    # === 4. B 类表新增 department_id ===
    for table in _B_TABLES:
        op.add_column(
            table,
            sa.Column("department_id", sa.UUID(), nullable=True),
        )

    # === 5. department 表唯一约束从 (organization_id, code) 改为 (parent_id, code) ===
    op.drop_constraint("uq_department_org_code", "department", type_="unique")
    # department.organization_id 改为 nullable（阶段3会 DROP）
    op.alter_column("department", "organization_id", nullable=True)
    op.create_unique_constraint(
        "uq_department_parent_code",
        "department",
        ["parent_id", "code"],
    )

    # === 6. 创建 root / system 哨兵部门行（幂等） ===
    root_display_name = os.environ.get("IRIP_ROOT_DEPT_NAME", "IRIP 研究院")
    op.execute(
        f"""
        DO $$
        DECLARE
            v_root_id UUID;
        BEGIN
            -- 创建 root 哨兵部门（parent_id = NULL）
            INSERT INTO department (id, code, display_name, description, status, sort_order, parent_id)
            VALUES (
                gen_random_uuid(), 'root', '{root_display_name}',
                '系统根部门（哨兵），全组织公共数据归属', 'active', -1, NULL
            )
            ON CONFLICT DO NOTHING;

            -- 获取 root 部门 ID
            SELECT id INTO v_root_id FROM department WHERE code = 'root' AND parent_id IS NULL LIMIT 1;

            -- 创建 system 哨兵部门（parent_id = root.id）
            INSERT INTO department (id, code, display_name, description, status, sort_order, parent_id)
            VALUES (
                gen_random_uuid(), 'system', '系统室',
                '系统级数据归属（密钥/连接器/备份等）', 'active', -2, v_root_id
            )
            ON CONFLICT DO NOTHING;
        END
        $$;
        """
    )


def downgrade() -> None:
    """回滚：删除新增列 + 恢复唯一约束 + 删除哨兵部门。"""
    # 删除哨兵部门
    op.execute("DELETE FROM department WHERE code IN ('root', 'system')")

    # 恢复唯一约束
    op.drop_constraint("uq_department_parent_code", "department", type_="unique")
    op.create_unique_constraint(
        "uq_department_org_code",
        "department",
        ["parent_id", "code"],
    )

    # 删除 B 类表 department_id
    for table in _B_TABLES:
        op.drop_column(table, "department_id")

    # 删除 A 类部分表新增列
    for table in _A_PARTIAL_EQUIP:
        op.drop_column(table, "owner_user_id")
        op.drop_column(table, "visibility_scope")

    for table in _A_PARTIAL_FLOW:
        op.drop_column(table, "owner_user_id")
        op.drop_column(table, "visibility_scope")
        op.drop_column(table, "visible_departments")

    # 删除 A 类全部新增表的新增列
    for table in _A_FULL_TABLES:
        op.drop_column(table, "owner_user_id")
        op.drop_column(table, "visibility_scope")
        op.drop_column(table, "visible_departments")
        op.drop_column(table, "department_id")
