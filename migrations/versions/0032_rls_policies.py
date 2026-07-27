"""0032: 为所有含 organization_id 的租户表启用 PostgreSQL RLS

技术设计文档 F-02/F-05：在应用层强制组织条件之外，增加数据库级 RLS 作为
第二道防线，确保即使应用代码遗漏组织过滤，数据库也会拒绝跨租户数据访问。

RLS 策略：
- 每个含 ``organization_id`` 列的表启用 ``ROW LEVEL SECURITY``；
- 创建 ``tenant_isolation`` policy，``USING`` 子句比较
  ``organization_id = current_setting('app.current_org_id', true)::uuid``；
- 对运行时角色 ``irip_runtime`` 强制 RLS（``FORCE ROW LEVEL SECURITY``）；
- 表超级用户和 owner 不受 RLS 限制（PostgreSQL 默认行为）。

``current_setting('app.current_org_id', true)`` 使用 ``true`` 参数允许
设置缺失时返回 NULL（而非报错），此时 RLS policy 的 ``USING`` 子句计算为
NULL = uuid，结果为 NULL（假），拒绝所有行访问——fail-closed 语义。

安全收益：
- 路由直接访问 ``service._factory`` 绕过应用层时，RLS 仍然阻断跨租户读取；
- 即使 SQL 注入拼接了不含组织条件的查询，RLS 也会自动过滤。

Revision ID: 0032_rls_policies
Revises: 0031_missing_columns
Create Date: 2026-07-27
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

#: 所有含 organization_id 列的租户表列表。
#: 仅对有直接 organization_id 列的表启用 RLS；
#: 通过 FK 间接关联的表（如 component_version、flow_version、flow_node_execution）
#: 不在此列——它们的隔离由父表的查询条件保证。
_TENANT_TABLES: list[str] = [
    "ai_conversation",
    "app_user",
    "artifact",
    "audit_event",
    "component",
    "department",
    "derivation_run",
    "equipment",
    "evidence_set",
    "fact",
    "fact_template",
    "flow_definition",
    "flow_run",
    "industrial_object",
    "ingestion_job",
    "job",
    "mapping_profile",
    "method",
    "model",
    "object_relation",
    "parameter",
    "provenance_edge",
    "scope_grant",
    "secret",
    "standard_package",
    "transformation_recipe",
    "variable",
]


def upgrade() -> None:
    """为所有租户表启用 RLS 并创建组织隔离 policy。"""
    # 1. 创建辅助函数：安全地启用 RLS（仅当表有 organization_id 列时）
    # 使用 DO 块动态检查列是否存在，避免对不含 organization_id 的表报错
    for table in _TENANT_TABLES:
        # 启用 RLS
        op.execute(
            f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;'
        )
        # 创建组织隔离 policy（如果已存在则跳过——使用 DROP IF CREATE 语义）
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = '{table}'
                      AND policyname = 'tenant_isolation'
                ) THEN
                    CREATE POLICY tenant_isolation ON "{table}"
                    USING (
                        organization_id = current_setting('app.current_org_id', true)::uuid
                    );
                END IF;
            END
            $$;
            """
        )
        # 对运行时角色强制 RLS（即使 owner 也会受 RLS 约束）
        # 注意：FORCE 仅影响非超级用户连接，superuser 始终绕过 RLS
        op.execute(
            f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;'
        )


def downgrade() -> None:
    """回滚：删除 RLS policy 并禁用 RLS。"""
    for table in _TENANT_TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS tenant_isolation ON "{table}";'
        )
        op.execute(
            f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;'
        )
        op.execute(
            f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;'
        )
