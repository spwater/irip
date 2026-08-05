"""0072: provenance_edge 表 RLS 策略切换 — 从 organization_id 换锚到 department_id

provenance_edge 是唯一没在 0065 迁移中切换为 department_id 策略的表，
仍在用旧策略 `organization_id = current_setting('app.current_org_id')`，
而 `app.current_org_id` 在整个代码库从未被设置，导致通电后该表查询返回空集。

操作：
1. 给 provenance_edge 添加 department_id 列（nullable，无 default）
2. 确定性回填：存量行挂 root 部门（code='root' AND parent_id IS NULL）
3. 显式检查：回填后不应有 NULL，否则抛异常中止
4. 设为 NOT NULL
5. 添加 FK 约束 → department.id
6. 添加索引（RLS 策略过滤会用到）
7. DROP 旧 RLS 策略（锚 org）
8. CREATE 新 RLS 策略（锚 dept，B 类层级：department_id IN (SELECT current_visible_dept_ids())）
9. 删除 organization_id 列（阶段3 已删除 org 概念，此列已无用）

支持空表和存量表两种场景：
- 空表：回填 UPDATE 影响 0 行，步骤 3 检查通过，直接设 NOT NULL
- 存量表：回填 UPDATE 将所有行挂 root 部门，步骤 3 验证无 NULL

前置条件：
- 0071 已执行（RLS 通电完成）
- department 表中存在 code='root' AND parent_id IS NULL 的 root 部门

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-02
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """provenance_edge RLS 策略从 org_id 切换到 dept_id。"""

    # === 1. 添加 department_id 列（nullable，无 default） ===
    op.execute("ALTER TABLE provenance_edge ADD COLUMN department_id uuid")

    # === 2. 确定性回填：存量行挂 root 部门 ===
    op.execute("""
        UPDATE provenance_edge
        SET department_id = (SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL)
        WHERE department_id IS NULL
    """)

    # === 3. 显式检查：回填后不应有 NULL ===
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM provenance_edge WHERE department_id IS NULL) THEN
                RAISE EXCEPTION '0072 回填失败：provenance_edge 仍有 department_id 为 NULL 的行';
            END IF;
        END $$;
    """)

    # === 4. 设为 NOT NULL ===
    op.execute("ALTER TABLE provenance_edge ALTER COLUMN department_id SET NOT NULL")

    # === 5. 添加 FK 约束 ===
    op.execute("""
        ALTER TABLE provenance_edge ADD CONSTRAINT
        fk_provenance_edge_department_id FOREIGN KEY (department_id)
        REFERENCES department(id)
    """)

    # === 6. 添加索引（RLS 策略过滤会用到） ===
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_provenance_edge_department_id
        ON provenance_edge (department_id)
    """)

    # === 7. DROP 旧 RLS 策略（锚 org） ===
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON provenance_edge")

    # === 8. CREATE 新 RLS 策略（锚 dept，B 类层级） ===
    op.execute(
        """
        CREATE POLICY tenant_isolation ON provenance_edge USING (
            department_id IN (SELECT current_visible_dept_ids())
        )
        """
    )

    # === 9. 删除 organization_id 列 ===
    # 阶段3 已删除 org 概念，此列已无用
    op.execute("ALTER TABLE provenance_edge DROP COLUMN IF EXISTS organization_id")


def downgrade() -> None:
    """回滚：恢复 organization_id 列 + 旧 RLS 策略。"""

    # 恢复 organization_id 列
    op.execute("ALTER TABLE provenance_edge ADD COLUMN organization_id uuid")

    # 删除新策略
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON provenance_edge")

    # 恢复旧策略（锚 org）
    op.execute(
        """
        CREATE POLICY tenant_isolation ON provenance_edge USING (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
        """
    )

    # 删除 FK + 索引 + department_id 列
    op.execute(
        "ALTER TABLE provenance_edge DROP CONSTRAINT IF EXISTS fk_provenance_edge_department_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_provenance_edge_department_id")
    op.execute("ALTER TABLE provenance_edge DROP COLUMN IF EXISTS department_id")
