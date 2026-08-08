"""0069: 实验项目提级 — 新建 experiment_project 表 + flow_definition 增 project_id 列

操作：
1. 建 experiment_project 表（A 类多租户 4 列 + 业务字段 + 唯一约束 + GIN 索引）；
2. 启用 RLS + FORCE，创建 4 分支 tenant_isolation 策略（复制 0065 A 类表模板）；
3. 挂 forbid_reprivatize() BEFORE UPDATE 触发器；
4. flow_definition 加 project_id UUID nullable 列 + FK 约束；
5. flow_definition.project_name 加 DEPRECATED COMMENT；
6. 存量数据迁移：按 (department_id, project_name) 去重创建项目并回填 project_id（幂等）。

Revision ID: 0069
Revises: 0068
Create Date: 2026-09-01
"""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """升级：建表 + 加列 + RLS + 触发器 + 存量迁移。"""

    # === 1. 建 experiment_project 表 ===
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_project (
            id                UUID            PRIMARY KEY,
            department_id     UUID            NOT NULL REFERENCES department(id),
            code              TEXT            NOT NULL,
            display_name      TEXT            NOT NULL,
            description       TEXT,
            status            TEXT            NOT NULL DEFAULT 'active',
            visible_departments JSONB         NOT NULL DEFAULT '[]'::jsonb,
            visibility_scope  TEXT            NOT NULL DEFAULT 'tree',
            owner_user_id     UUID            NOT NULL REFERENCES app_user(id),
            created_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
            lock_version      INTEGER         NOT NULL DEFAULT 0,
            CONSTRAINT uq_experiment_project_dept_code UNIQUE (department_id, code)
        )
        """
    )

    # GIN 索引：visible_departments 用于可见性过滤
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_experiment_project_visible_depts_gin
            ON experiment_project USING GIN (visible_departments)
        """
    )

    # === 2. RLS 策略（复制 0065 A 类表 4 分支模板） ===
    op.execute("ALTER TABLE experiment_project ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE experiment_project FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON experiment_project")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON experiment_project USING (
            -- 私有分支：仅所有者可见
            (visibility_scope = 'private'
             AND owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
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

    # === 3. forbid_reprivatize() 触发器 ===
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_forbid_reprivatize ON experiment_project;
        CREATE TRIGGER trg_forbid_reprivatize
            BEFORE UPDATE ON experiment_project
            FOR EACH ROW EXECUTE FUNCTION forbid_reprivatize();
        """
    )

    # === 4. flow_definition 加 project_id 列 ===
    op.execute(
        """
        ALTER TABLE flow_definition
            ADD COLUMN IF NOT EXISTS project_id UUID
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_flow_definition_project_id'
            ) THEN
                ALTER TABLE flow_definition
                    ADD CONSTRAINT fk_flow_definition_project_id
                    FOREIGN KEY (project_id) REFERENCES experiment_project(id);
            END IF;
        END $$;
        """
    )

    # === 5. project_name 加 DEPRECATED COMMENT ===
    op.execute(
        "COMMENT ON COLUMN flow_definition.project_name IS 'DEPRECATED: replaced by project_id'"
    )

    # === 6. 存量数据迁移（幂等） ===
    # 按 (department_id, project_name) 去重创建项目，project_name 非空
    # code 用 'proj_' + 8位UUID hex前缀
    # owner_user_id 取该 project_name 下最早创建任务的 owner_user_id
    # status='active', visibility_scope='tree', visible_departments='[]'
    # 回填 flow_definition.project_id
    op.execute(
        """
        INSERT INTO experiment_project (
            id, department_id, code, display_name, description,
            status, visible_departments, visibility_scope, owner_user_id,
            created_at, updated_at, lock_version
        )
        SELECT
            gen_random_uuid(),
            dept_project.department_id,
            'proj_' || substring(gen_random_uuid()::text, 1, 8),
            dept_project.project_name,
            NULL,
            'active',
            '[]'::jsonb,
            'tree',
            dept_project.owner_user_id,
            now(),
            now(),
            0
        FROM (
            SELECT
                fd.department_id,
                fd.project_name,
                (
                    SELECT fd2.owner_user_id
                    FROM flow_definition fd2
                    WHERE fd2.department_id = fd.department_id
                      AND fd2.project_name = fd.project_name
                      AND fd2.project_name IS NOT NULL
                      AND fd2.project_name <> ''
                    ORDER BY fd2.created_at ASC
                    LIMIT 1
                ) AS owner_user_id
            FROM flow_definition fd
            WHERE fd.project_name IS NOT NULL
              AND fd.project_name <> ''
            GROUP BY fd.department_id, fd.project_name
        ) AS dept_project
        WHERE NOT EXISTS (
            SELECT 1
            FROM experiment_project ep
            WHERE ep.department_id = dept_project.department_id
              AND ep.display_name = dept_project.project_name
        )
        """
    )

    # 回填 flow_definition.project_id（按 department_id + project_name 关联）
    op.execute(
        """
        UPDATE flow_definition fd
        SET project_id = ep.id
        FROM experiment_project ep
        WHERE fd.project_name IS NOT NULL
          AND fd.project_name <> ''
          AND fd.project_id IS NULL
          AND ep.department_id = fd.department_id
          AND ep.display_name = fd.project_name
        """
    )


def downgrade() -> None:
    """回滚：删触发器 + 删表 + 删列 + 删 COMMENT。"""

    # 1. 删触发器
    op.execute("DROP TRIGGER IF EXISTS trg_forbid_reprivatize ON experiment_project")

    # 2. 删 RLS 策略
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON experiment_project")
    op.execute("ALTER TABLE experiment_project NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE experiment_project DISABLE ROW LEVEL SECURITY")

    # 3. 删 flow_definition.project_id FK + 列
    op.execute(
        "ALTER TABLE flow_definition DROP CONSTRAINT IF EXISTS fk_flow_definition_project_id"
    )
    op.execute("ALTER TABLE flow_definition DROP COLUMN IF EXISTS project_id")

    # 4. 删 project_name COMMENT
    op.execute("COMMENT ON COLUMN flow_definition.project_name IS NULL")

    # 5. 删 experiment_project 表
    op.execute("DROP TABLE IF EXISTS experiment_project")
