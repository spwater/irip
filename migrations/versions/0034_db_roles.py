"""0034: 分离 migration / runtime / audit 三类数据库角色

分离数据库账号实现最小权限原则（技术设计文档 F-05/F-12）：

1. ``irip_migrate`` — 迁移 owner，拥有全部 DDL/DML 权限（用于 alembic 迁移）；
2. ``irip_runtime`` — API/Worker 运行时，最小 DML 权限
   （业务表 SELECT/INSERT/UPDATE/DELETE，但不可变表仅 SELECT/INSERT）；
3. ``irip_audit_writer`` — 审计写入，仅 audit_event 表 INSERT 权限。

安全收益：
- 运行时账号无法修改表结构（DDL），即使应用被注入也无法改表；
- 运行时账号无法 UPDATE/DELETE 不可变表（fact_revision、component_version、
  flow_definition_version、audit_event、evidence_set）；
- 审计写入账号仅能 INSERT 审计事件，无法读取或修改其他数据。

注意：
- 此迁移在生产数据库执行时创建角色并授权；
- 本地开发环境使用 owner 账号不受影响；
- 不可变表的 REVOKE UPDATE/DELETE 在 0033 迁移中已处理（触发器+权限双保险），
  此迁移仅创建角色和基本授权框架；
- compose.yaml 的连接账号切换在 T1-4 完成后一起改。

Revision ID: 0034_db_roles
Revises: 0033_immutable_tables
Create Date: 2026-07-27
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

#: 所有业务表列表（运行时账号需要 CRUD 权限）。
_BUSINESS_TABLES = [
    "organization",
    "app_user",
    "app_user_department",
    "role",
    "artifact",
    "artifact_blob",
    "job",
    "outbox_event",
    "department",
    "equipment",
    "equipment_variable",
    "variable",
    "variable_alias",
    "variable_version",
    "standard_package",
    "standard_package_version",
    "industrial_object",
    "object_relation",
    "fact_template",
    "fact_template_version",
    "fact",
    "fact_revision",
    "fact_revision_link",
    "fact_artifact",
    "raw_observation",
    "normalized_observation",
    "quality_assessment",
    "mapping_profile",
    "mapping_profile_version",
    "ingestion_job",
    "provenance_edge",
    "evidence_set",
    "evidence_set_version",
    "derivation_run",
    "transformation_recipe",
    "transformation_recipe_version",
    "parameter",
    "parameter_candidate",
    "parameter_staleness",
    "parameter_version",
    "component",
    "component_version",
    "flow_definition",
    "flow_definition_version",
    "flow_run",
    "flow_node_execution",
    "model",
    "model_version",
    "ai_conversation",
    "ai_message",
    "ai_config",
    "audit_event",
    "refresh_session",
    "secret",
]

#: 不可变表列表（运行时账号仅 SELECT/INSERT，不可 UPDATE/DELETE）。
_IMMUTABLE_TABLES = [
    "fact_revision",
    "component_version",
    "flow_definition_version",
    "flow_node_execution",
    "audit_event",
    "evidence_set",
]

#: 审计事件表名。
_AUDIT_TABLE = "audit_event"


def upgrade() -> None:
    """创建三类数据库角色并授权。"""
    # ---- 1. 创建角色（IF NOT EXISTS 语义） ----
    # 使用 DO 块实现 IF NOT EXISTS，避免重复创建报错
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_migrate') THEN
                CREATE ROLE irip_migrate LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                CREATE ROLE irip_runtime LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_audit_writer') THEN
                CREATE ROLE irip_audit_writer NOLOGIN;
            END IF;
        END
        $$;
        """
    )

    # ---- 2. irip_migrate: 迁移 owner，全部权限 ----
    # 授予 irip_migrate 对所有现有表的全部权限
    # 并授予默认权限（未来由 owner 创建的表自动授权）
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO irip_migrate;")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO irip_migrate;")
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO irip_migrate;")
    # 默认权限：owner 创建的新表自动授权给 irip_migrate
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT ALL PRIVILEGES ON TABLES TO irip_migrate;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT ALL PRIVILEGES ON SEQUENCES TO irip_migrate;"
    )

    # ---- 3. irip_runtime: 运行时账号，业务表最小权限 ----
    # 授予 schema 使用权限
    op.execute("GRANT USAGE ON SCHEMA public TO irip_runtime;")

    # 对所有业务表授予 SELECT/INSERT/UPDATE/DELETE
    for table in _BUSINESS_TABLES:
        op.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO irip_runtime;'
        )

    # 对不可变表 REVOKE UPDATE/DELETE（仅保留 SELECT/INSERT）
    # 注意：不可变表的触发器在 0033 迁移中创建，此处仅做权限层面的限制
    for table in _IMMUTABLE_TABLES:
        op.execute(f'REVOKE UPDATE, DELETE ON TABLE "{table}" FROM irip_runtime;')
        op.execute(f'GRANT SELECT, INSERT ON TABLE "{table}" TO irip_runtime;')

    # 运行时账号不授予 audit_event 的 UPDATE/DELETE
    op.execute(f'REVOKE UPDATE, DELETE ON TABLE "{_AUDIT_TABLE}" FROM irip_runtime;')

    # 授予序列使用权限（INSERT 需要序列值）
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO irip_runtime;")

    # ---- 4. irip_audit_writer: 审计写入账号，仅审计表 INSERT ----
    op.execute("GRANT USAGE ON SCHEMA public TO irip_audit_writer;")
    op.execute(f'GRANT INSERT ON TABLE "{_AUDIT_TABLE}" TO irip_audit_writer;')

    # ---- 5. 默认权限（未来新建表自动授权给 irip_runtime） ----
    # 由数据库 owner 创建的新表自动授权给 irip_runtime
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO irip_runtime;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO irip_runtime;"
    )


def downgrade() -> None:
    """回滚：收回权限并删除角色。"""
    # 收回默认权限
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM irip_runtime;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM irip_runtime;"
    )

    # 收回 irip_audit_writer 权限
    op.execute(f'REVOKE INSERT ON TABLE "{_AUDIT_TABLE}" FROM irip_audit_writer;')
    op.execute("REVOKE USAGE ON SCHEMA public FROM irip_audit_writer;")

    # 收回 irip_runtime 权限
    for table in _BUSINESS_TABLES:
        op.execute(
            f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
            f"FROM irip_runtime;"
        )
    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM irip_runtime;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM irip_runtime;")

    # 收回 irip_migrate 权限
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM irip_migrate;")
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM irip_migrate;"
    )
    op.execute("REVOKE USAGE, CREATE ON SCHEMA public FROM irip_migrate;")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM irip_migrate;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON SEQUENCES FROM irip_migrate;"
    )

    # 删除角色
    op.execute("DROP ROLE IF EXISTS irip_audit_writer;")
    op.execute("DROP ROLE IF EXISTS irip_runtime;")
    op.execute("DROP ROLE IF EXISTS irip_migrate;")
