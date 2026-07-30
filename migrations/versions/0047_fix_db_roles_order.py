"""0047: 修复 db_roles 授权顺序 -- 安全地对所有业务表重新授权

0034 迁移在 fresh DB 上执行时，部分 GRANT 语句会因表不存在而失败。
此迁移用 DO 块包裹所有 GRANT，通过 information_schema.tables 检查表存在
后再授权，确保对 0034 之后新增的表也能正确授权。

同时此迁移也是 idempotent 的：对已授权的表重复 GRANT 不会报错。

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-31
"""

from alembic import op

revision = "0047"
down_revision = "0046"
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
    "scope_grant",
    "ai_tool",
    "fact_data_index",
]

#: 不可变表列表（运行时账号仅 SELECT/INSERT，不可 UPDATE/DELETE）。
#: H-01 修复：flow_node_execution 和 evidence_set 不再是不可变的，
#: 真正不可变的是 evidence_set_version。
_IMMUTABLE_TABLES = [
    "fact_revision",
    "component_version",
    "flow_definition_version",
    "audit_event",
    "evidence_set_version",
]

#: 审计事件表名。
_AUDIT_TABLE = "audit_event"


def _grant_safe(table: str, privileges: str, role: str) -> str:
    """生成安全的 GRANT 语句（DO 块包裹，检查表存在）。

    Args:
        table: 表名。
        privileges: 权限列表（如 "SELECT, INSERT, UPDATE, DELETE"）。
        role: 角色名。

    Returns:
        str: SQL 语句。
    """
    return (
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = '{table}'
            ) THEN
                GRANT {privileges} ON TABLE "{table}" TO {role};
            END IF;
        END
        $$;
        """
    )


def _revoke_safe(table: str, privileges: str, role: str) -> str:
    """生成安全的 REVOKE 语句（DO 块包裹，检查表存在）。

    Args:
        table: 表名。
        privileges: 权限列表（如 "UPDATE, DELETE"）。
        role: 角色名。

    Returns:
        str: SQL 语句。
    """
    return (
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = '{table}'
            ) THEN
                REVOKE {privileges} ON TABLE "{table}" FROM {role};
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    """对所有业务表安全地重新授权。"""
    # 1. 确保角色存在（与 0034 相同的 IF NOT EXISTS 逻辑）
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

    # 2. 对所有业务表安全地授权 SELECT/INSERT/UPDATE/DELETE
    for table in _BUSINESS_TABLES:
        op.execute(_grant_safe(table, "SELECT, INSERT, UPDATE, DELETE", "irip_runtime"))

    # 3. 对不可变表 REVOKE UPDATE/DELETE（仅保留 SELECT/INSERT）
    for table in _IMMUTABLE_TABLES:
        op.execute(_revoke_safe(table, "UPDATE, DELETE", "irip_runtime"))
        op.execute(_grant_safe(table, "SELECT, INSERT", "irip_runtime"))

    # 4. 运行时账号不授予 audit_event 的 UPDATE/DELETE
    op.execute(_revoke_safe(_AUDIT_TABLE, "UPDATE, DELETE", "irip_runtime"))

    # 5. 审计写入账号仅 INSERT
    op.execute(_grant_safe(_AUDIT_TABLE, "INSERT", "irip_audit_writer"))

    # 6. 序列使用权限
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO irip_runtime;")


def downgrade() -> None:
    """回滚：收回此迁移中授予的额外权限。

    注意：此降级不会完全恢复到 0034 之前的状态，
    因为 0034 本身也授予了权限。此降级仅收回此迁移中
    对新增表的授权。
    """
    for table in _BUSINESS_TABLES:
        op.execute(_revoke_safe(table, "SELECT, INSERT, UPDATE, DELETE", "irip_runtime"))

    for table in _IMMUTABLE_TABLES:
        op.execute(_revoke_safe(table, "SELECT, INSERT", "irip_runtime"))

    op.execute(_revoke_safe(_AUDIT_TABLE, "INSERT", "irip_audit_writer"))
