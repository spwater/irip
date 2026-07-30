"""0048: 对所有含 organization_id 列的表启用 FORCE ROW LEVEL SECURITY

0032 迁移已对部分表启用了 RLS 和 FORCE RLS，但后续迁移（0035-0046）
新增的含 organization_id 列的表可能未覆盖。此迁移动态查询
information_schema.columns 找到所有含 organization_id 列的表，
统一启用 RLS + FORCE RLS + tenant_isolation policy。

安全收益：
- 确保即使表 owner 也受 RLS 约束（FORCE）；
- 新增的租户表自动被 RLS 覆盖；
- fail-closed 语义：GUC 缺失时拒绝所有行访问。

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-31
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """对所有含 organization_id 列的表启用 FORCE RLS + tenant_isolation policy。"""
    # 动态查询所有含 organization_id 列的 public schema 表，
    # 对每个表启用 RLS、创建 tenant_isolation policy、强制 RLS。
    op.execute(
        """
        DO $$
        DECLARE
            tbl TEXT;
        BEGIN
            FOR tbl IN
                SELECT DISTINCT c.table_name
                FROM information_schema.columns c
                JOIN information_schema.tables t
                    ON c.table_schema = t.table_schema
                    AND c.table_name = t.table_name
                WHERE c.table_schema = 'public'
                  AND c.column_name = 'organization_id'
                  AND t.table_type = 'BASE TABLE'
            LOOP
                -- 启用 RLS
                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);

                -- 删除已存在的 tenant_isolation policy（幂等）
                EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);

                -- 创建 tenant_isolation policy
                EXECUTE format(
                    'CREATE POLICY tenant_isolation ON %I '
                    'USING (organization_id = current_setting(''app.current_org_id'', true)::uuid)',
                    tbl
                );

                -- 强制 RLS（即使 owner 也受约束）
                EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
            END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    """回滚：删除 tenant_isolation policy 并禁用 FORCE RLS。

    注意：此降级会移除所有含 organization_id 列的表上的 RLS 保护，
    仅在明确需要时执行。
    """
    op.execute(
        """
        DO $$
        DECLARE
            tbl TEXT;
        BEGIN
            FOR tbl IN
                SELECT DISTINCT c.table_name
                FROM information_schema.columns c
                JOIN information_schema.tables t
                    ON c.table_schema = t.table_schema
                    AND c.table_name = t.table_name
                WHERE c.table_schema = 'public'
                  AND c.column_name = 'organization_id'
                  AND t.table_type = 'BASE TABLE'
            LOOP
                EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
                EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tbl);
                EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', tbl);
            END LOOP;
        END
        $$;
        """
    )
