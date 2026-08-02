"""0065: 多租户隔离键升级 — 阶段2切换

将 RLS 策略从 organization_id 换锚到 department_id。

操作：
1. 对所有 A/B 类表：DROP 旧 tenant_isolation 策略（锚 org）+ DROP 备用 tenant_isolation_dept
   → CREATE 新 tenant_isolation 策略（锚 dept）
   - A 类表：含私有分支 + 层级分支 + 白名单分支
   - B 类表：仅层级分支
2. AI 会话（ai_conversation / ai_message）：DROP 旧 org 策略 → CREATE 新策略（锚 user_id）
   + 创建 current_user_conversations() 辅助函数
3. department 表策略：锚 current_visible_dept_ids()
4. 创建 forbid_reprivatize() 触发器函数 + 对所有 A 类表挂 BEFORE UPDATE 触发器
5. 创建 protect_sentinel_dept() 触发器函数 + 挂在 department 表 BEFORE UPDATE/DELETE

阶段1 0064 创建的 tenant_isolation_dept 策略只有简单层级分支（无私有分支），
本迁移 DROP 它并 CREATE 完整的 tenant_isolation 策略。

Revision ID: 0065
Revises: 0064
Create Date: 2026-08-23
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

#: A 类表完整列表（含私有分支）
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

#: B 类表完整列表（仅层级分支）
#: 注意：app_user 是认证基础设施，不启 RLS（安全性由应用层保证）
_B_TABLES: list[str] = [
    "job",
    "flow_run",
    "derivation_run",
    "audit_event",
    "secret",
    "backup_record",
    # app_user 不启 RLS（认证基础设施）
    # scope_grant 已在 0036 迁移中删除，跳过
]


def upgrade() -> None:
    """切换 RLS 策略 + 创建触发器 + AI 会话策略。"""

    # === 1. A 类表 RLS 策略切换 ===
    for table in _A_TABLES:
        # DROP 旧策略（锚 org）+ 备用策略
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_dept ON {table}")

        # CREATE 新策略（锚 dept，含私有分支）
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table} USING (
                -- 私有分支：仅所有者可见
                (visibility_scope = 'private'
                 AND owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
                OR
                -- 层级分支：本部门子树可见
                (visibility_scope = 'tree'
                 AND (department_id IN (SELECT current_visible_dept_ids())
                      OR visible_departments @> jsonb_build_array(
                          NULLIF(current_setting('app.current_dept_id', true), '')::uuid
                      )
                     )
                )
                OR
                -- 显式白名单分支
                (visibility_scope = 'explicit'
                 AND visible_departments @> jsonb_build_array(
                     NULLIF(current_setting('app.current_dept_id', true), '')::uuid
                 )
                )
                OR
                -- 全可见分支
                visibility_scope = 'all'
            )
            """
        )

    # === 2. B 类表 RLS 策略切换 ===
    for table in _B_TABLES:
        # DROP 旧策略（锚 org）+ 备用策略
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_dept ON {table}")

        # CREATE 新策略（锚 dept，仅层级）
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table} USING (
                department_id IN (SELECT current_visible_dept_ids())
            )
            """
        )

    # === 3. department 表是结构数据，全员可读，不启用 RLS ===
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON department")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_dept ON department")
    op.execute("ALTER TABLE department NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE department DISABLE ROW LEVEL SECURITY")

    # === 3b. app_user 是认证基础设施，不启用 RLS ===
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON app_user")
    op.execute("ALTER TABLE app_user NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app_user DISABLE ROW LEVEL SECURITY")

    # === 4. AI 会话 RLS 策略 ===
    # 创建 current_user_conversations() 辅助函数
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_user_conversations()
        RETURNS SETOF uuid
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        AS $$
            SELECT conversation_id FROM conversation_participant
            WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        $$;
        """
    )

    # ai_conversation 策略
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON ai_conversation")
    op.execute("DROP POLICY IF EXISTS ai_conversation_isolation ON ai_conversation")
    op.execute(
        """
        CREATE POLICY ai_conversation_isolation ON ai_conversation USING (
            user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            OR id IN (SELECT current_user_conversations())
        )
        """
    )

    # ai_message 策略（随父——通过 conversation_id 关联）
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON ai_message")
    op.execute("DROP POLICY IF EXISTS ai_conversation_isolation ON ai_message")
    op.execute(
        """
        CREATE POLICY ai_conversation_isolation ON ai_message USING (
            conversation_id IN (
                SELECT id FROM ai_conversation WHERE
                    user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                    OR id IN (SELECT current_user_conversations())
            )
        )
        """
    )

    # === 5. forbid_reprivatize() 触发器函数 + A 类表触发器 ===
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_reprivatize()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- 1. 禁止将 visibility_scope 从非 private 改为 private（防止数据私有化锁定）
            IF OLD.visibility_scope != 'private' AND NEW.visibility_scope = 'private' THEN
                RAISE EXCEPTION 'forbid_reprivatize: 不允许将已有数据改为 private 可见范围 (table=%, id=%)',
                    TG_TABLE_NAME, OLD.id;
            END IF;
            -- 2. 禁止修改 owner_user_id（隐私锁不可换钥匙）
            IF NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id THEN
                RAISE EXCEPTION 'forbid_reprivatize: owner_user_id 不可修改 (table=%, id=%)',
                    TG_TABLE_NAME, OLD.id;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    for table in _A_TABLES:
        op.execute(
            f"""
            DROP TRIGGER IF EXISTS trg_forbid_reprivatize ON {table};
            CREATE TRIGGER trg_forbid_reprivatize
                BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION forbid_reprivatize();
            """
        )

    # === 6. protect_sentinel_dept() 触发器函数 + department 表触发器 ===
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_sentinel_dept()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- DELETE: 只拦哨兵部门
            IF TG_OP = 'DELETE' THEN
                IF OLD.code IN ('root', 'system') THEN
                    RAISE EXCEPTION 'protect_sentinel_dept: 禁止删除哨兵部门 (code=%)', OLD.code;
                END IF;
                RETURN OLD;
            END IF;
            -- UPDATE: 哨兵部门仅允许修改 display_name / description / sort_order
            -- 禁止修改 parent_id（re-parent）和 status（禁用/删除）
            IF OLD.code IN ('root', 'system') THEN
                IF NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
                    RAISE EXCEPTION 'protect_sentinel_dept: 禁止调整哨兵部门的父子关系 (code=%)',
                        OLD.code;
                END IF;
                IF NEW.status IS DISTINCT FROM OLD.status THEN
                    RAISE EXCEPTION 'protect_sentinel_dept: 禁止修改哨兵部门状态 (code=%)',
                        OLD.code;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_protect_sentinel ON department;
        CREATE TRIGGER trg_protect_sentinel
            BEFORE UPDATE OR DELETE ON department
            FOR EACH ROW EXECUTE FUNCTION protect_sentinel_dept();
        """
    )


def downgrade() -> None:
    """回滚：删除触发器 + 删除函数 + 删除新策略。

    阶段3后不再重建旧 organization_id 锚定策略（该列已删除）。
    """

    # 1. 删除触发器
    for table in _A_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_forbid_reprivatize ON {table}")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_sentinel ON department")

    # 2. 删除触发器函数
    op.execute("DROP FUNCTION IF EXISTS forbid_reprivatize()")
    op.execute("DROP FUNCTION IF EXISTS protect_sentinel_dept()")

    # 3. 删除 AI 会话策略 + 函数
    op.execute("DROP POLICY IF EXISTS ai_conversation_isolation ON ai_conversation")
    op.execute("DROP POLICY IF EXISTS ai_conversation_isolation ON ai_message")
    op.execute("DROP FUNCTION IF EXISTS current_user_conversations()")

    # 4. 删除 department 策略
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON department")

    # 5. 删除 A/B 类表策略（不再重建旧 org 策略）
    for table in _A_TABLES + _B_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # 6. 删除 AI 会话策略
    for table in ["ai_conversation", "ai_message"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
