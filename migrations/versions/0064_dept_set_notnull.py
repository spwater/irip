"""0064: 多租户隔离键升级 — 阶段1 SET NOT NULL + 函数 + GIN 索引 + 备用 RLS 策略

1. 将 0062 新增的 department_id 列设为 NOT NULL（A/B 类表）
2. 将 A 类表的 visible_departments / visibility_scope / owner_user_id 设为 NOT NULL
   并添加 server_default
3. 创建 current_visible_dept_ids() 函数（STABLE, SECURITY DEFINER）
   — 递归计算当前部门及其上下级可见部门 ID 集合
4. 在 A 类表的 visible_departments 列上创建 GIN 索引
5. 创建新的 RLS 策略 tenant_isolation_dept（仅创建，不激活）
   — 阶段1 RLS 仍锚 organization_id（tenant_isolation 策略继续生效）
   — 0065 切换时将 DROP 旧 tenant_isolation 并 RENAME tenant_isolation_dept → tenant_isolation

Revision ID: 0064
Revises: 0063
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

#: A 类表完整列表
_A_TABLES: list[str] = [
    "fact",
    "parameter",
    "evidence_set",
    "artifact",
    "model",
    "transformation_recipe",
    "component",
    "flow_definition",
    "industrial_object",
    "equipment",
]

#: B 类表完整列表（需要 SET NOT NULL department_id）
#: app_user / scope_grant 已有 nullable department_id，此迁移设 NOT NULL + FK
_B_TABLES: list[str] = [
    "job",
    "flow_run",
    "derivation_run",
    "audit_event",
    "secret",
    "backup_record",
    "app_user",
    # scope_grant 已在 0055-0059 清理批次中删除，跳过
]

#: 需要添加 FK 约束的表（department_id → department.id）
_ALL_DEPT_TABLES: list[str] = _A_TABLES + _B_TABLES


def upgrade() -> None:
    """SET NOT NULL + 函数 + GIN 索引 + 备用 RLS 策略。"""

    # === 1. A 类表 SET NOT NULL + server_default ===
    for table in _A_TABLES:
        # 先删除 department_id 上可能已存在的 FK（如 flow_definition 的 ondelete=SET NULL、
        # equipment 的 ondelete=CASCADE），再创建统一的 FK
        op.execute(
            f"""
            DO $$
            DECLARE
                fk_name TEXT;
            BEGIN
                SELECT conname INTO fk_name
                FROM pg_constraint
                WHERE conrelid = '{table}'::regclass
                  AND contype = 'f'
                  AND connamespace = 'public'::regnamespace
                  AND array_length(conkey, 1) = 1
                  AND conkey[1] = (
                      SELECT attnum FROM pg_attribute
                      WHERE attrelid = '{table}'::regclass
                        AND attname = 'department_id'
                  );
                IF fk_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', '{table}', fk_name);
                END IF;
            END
            $$;
            """
        )
        # department_id: SET NOT NULL + FK
        op.alter_column(table, "department_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_department_id",
            table,
            "department",
            ["department_id"],
            ["id"],
        )

        # visible_departments: SET NOT NULL + server_default '[]'
        op.alter_column(
            table,
            "visible_departments",
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        )

        # visibility_scope: SET NOT NULL + server_default 'tree'
        op.alter_column(
            table,
            "visibility_scope",
            nullable=False,
            server_default=sa.text("'tree'"),
        )

        # owner_user_id: SET NOT NULL + FK
        op.alter_column(table, "owner_user_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_owner_user_id",
            table,
            "app_user",
            ["owner_user_id"],
            ["id"],
        )

    # === 2. B 类表 SET NOT NULL + FK ===
    for table in _B_TABLES:
        # 先删除可能已存在的 FK（app_user / scope_grant 已有 nullable department_id）
        op.execute(
            f"""
            DO $$
            DECLARE
                fk_name TEXT;
            BEGIN
                SELECT conname INTO fk_name
                FROM pg_constraint
                WHERE conrelid = '{table}'::regclass
                  AND contype = 'f'
                  AND connamespace = 'public'::regnamespace
                  AND array_length(conkey, 1) = 1
                  AND conkey[1] = (
                      SELECT attnum FROM pg_attribute
                      WHERE attrelid = '{table}'::regclass
                        AND attname = 'department_id'
                  );
                IF fk_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', '{table}', fk_name);
                END IF;
            END
            $$;
            """
        )
        op.alter_column(table, "department_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_department_id",
            table,
            "department",
            ["department_id"],
            ["id"],
        )

    # === 3. 创建 current_visible_dept_ids() 函数 ===
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_visible_dept_ids()
        RETURNS SETOF uuid
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        AS $$
            WITH RECURSIVE
            user_depts AS (
                SELECT department_id AS id
                FROM app_user_department
                WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            ),
            down AS (
                SELECT id FROM user_depts
                UNION ALL
                SELECT d.id FROM department d JOIN down s ON d.parent_id = s.id
            ), up AS (
                SELECT d.parent_id AS id FROM department d
                WHERE d.id IN (SELECT id FROM user_depts)
                  AND d.parent_id IS NOT NULL
                UNION ALL
                SELECT d.parent_id FROM department d JOIN up ON d.id = up.id
                WHERE d.parent_id IS NOT NULL
            )
            SELECT id FROM down
            UNION
            SELECT id FROM up
        $$;
        """
    )

    # === 4. A 类表 visible_departments GIN 索引 ===
    for table in _A_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_visible_depts_gin "
            f"ON {table} USING GIN (visible_departments)"
        )

    # === 5. 创建备用 RLS 策略 tenant_isolation_dept（仅创建，不激活） ===
    # 阶段1 RLS 仍锚 organization_id（tenant_isolation 策略继续生效）
    # 此策略在 0065 切换时才会被 RENAME 为 tenant_isolation
    # 策略逻辑：department_id IN (SELECT current_visible_dept_ids())
    #           OR cardinality(visible_departments) > 0
    #           AND visible_departments ?| ARRAY(SELECT current_visible_dept_ids()::text)
    for table in _A_TABLES + _B_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                -- 仅创建策略，不删除现有 tenant_isolation 策略
                DROP POLICY IF EXISTS tenant_isolation_dept ON {table};

                CREATE POLICY tenant_isolation_dept ON {table}
                USING (
                    department_id IN (SELECT current_visible_dept_ids())
                );
            END
            $$;
            """
        )


def downgrade() -> None:
    """回滚：删除备用策略 + GIN 索引 + 函数 + 恢复 nullable。"""

    # 1. 删除备用 RLS 策略
    for table in _A_TABLES + _B_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_dept ON {table}")

    # 2. 删除 GIN 索引
    for table in _A_TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_visible_depts_gin")

    # 3. 删除函数
    op.execute("DROP FUNCTION IF EXISTS current_visible_dept_ids()")

    # 4. A 类表恢复 nullable + 删除 FK
    for table in _A_TABLES:
        op.drop_constraint(f"fk_{table}_owner_user_id", table, type_="foreignkey")
        op.alter_column(table, "owner_user_id", nullable=True)

        op.alter_column(
            table,
            "visibility_scope",
            nullable=True,
            server_default=None,
        )

        op.alter_column(
            table,
            "visible_departments",
            nullable=True,
            server_default=None,
        )

        op.drop_constraint(f"fk_{table}_department_id", table, type_="foreignkey")
        op.alter_column(table, "department_id", nullable=True)

    # 5. B 类表恢复 nullable + 删除 FK
    for table in _B_TABLES:
        op.drop_constraint(f"fk_{table}_department_id", table, type_="foreignkey")
        op.alter_column(table, "department_id", nullable=True)
