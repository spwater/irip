"""0033: 不可变表触发器 + 运行时角色权限修正

技术设计文档 F-03/F-05 §8.3：以下表为不可变表（INSERT-only），通过数据库
触发器强制拒绝 UPDATE/DELETE，确保证据链和版本历史不可篡改：

不可变表列表：
- ``fact_revision`` — 事实修订历史
- ``component_version`` — 组件版本历史
- ``flow_version`` — 流程版本历史（对应 flow_definition_version 表）
- ``flow_node_execution`` — 节点执行记录
- ``audit_event`` — 审计事件
- ``evidence_record`` — 证据记录

触发器：
- ``raise_immutable_violation()`` — BEFORE UPDATE OR DELETE 触发器函数，
  抛出异常阻止任何 UPDATE/DELETE 操作。

权限修正：
- 对运行时角色 ``irip_runtime`` REVOKE UPDATE/DELETE 权限，
  即使触发器被意外删除，权限层仍拒绝修改。

注意：``flow_version`` 表在数据库中的实际表名为 ``flow_definition_version``。

Revision ID: 0033_immutable_tables
Revises: 0032_rls_policies
Create Date: 2026-07-27
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

#: 不可变表列表（数据库实际表名）。
#: 注意 flow_version 的实际表名为 flow_definition_version。
_IMMUTABLE_TABLES: list[str] = [
    "fact_revision",
    "component_version",
    "flow_definition_version",
    "flow_node_execution",
    "audit_event",
    "evidence_set",
]

#: 触发器名称映射（表名 → 触发器名）。
_TRIGGER_NAMES: dict[str, str] = {
    "fact_revision": "prevent_modify_fact_revision",
    "component_version": "prevent_modify_component_version",
    "flow_definition_version": "prevent_modify_flow_version",
    "flow_node_execution": "prevent_modify_flow_node_execution",
    "audit_event": "prevent_modify_audit_event",
    "evidence_set": "prevent_modify_evidence_set",
}


def upgrade() -> None:
    """创建不可变表触发器 + REVOKE 运行时 UPDATE/DELETE 权限。"""
    # 1. 创建触发器函数（幂等：CREATE OR REPLACE）
    op.execute(
        """
        CREATE OR REPLACE FUNCTION raise_immutable_violation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Table % is immutable: UPDATE/DELETE not allowed (F-03)',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # 2. 为每个不可变表创建 BEFORE UPDATE OR DELETE 触发器
    for table in _IMMUTABLE_TABLES:
        trigger_name: str = _TRIGGER_NAMES[table]
        op.execute(
            f"""
            DROP TRIGGER IF EXISTS {trigger_name} ON "{table}";
            CREATE TRIGGER {trigger_name}
                BEFORE UPDATE OR DELETE ON "{table}"
                FOR EACH ROW
                EXECUTE FUNCTION raise_immutable_violation();
            """
        )

    # 3. 对运行时角色 REVOKE UPDATE/DELETE（权限层第二道防线）
    # 注意：irip_runtime 角色在 0034 迁移中创建；
    # 此处使用 DO 块安全地 REVOKE（角色不存在时跳过）
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                    REVOKE UPDATE, DELETE ON TABLE "{table}" FROM irip_runtime;
                    GRANT SELECT, INSERT ON TABLE "{table}" TO irip_runtime;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    """回滚：删除触发器并恢复运行时角色权限。"""
    # 1. 恢复运行时角色权限
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                    GRANT UPDATE, DELETE ON TABLE "{table}" TO irip_runtime;
                END IF;
            END
            $$;
            """
        )

    # 2. 删除触发器
    for table in _IMMUTABLE_TABLES:
        trigger_name: str = _TRIGGER_NAMES[table]
        op.execute(f'DROP TRIGGER IF EXISTS {trigger_name} ON "{table}";')

    # 3. 删除触发器函数
    op.execute("DROP FUNCTION IF EXISTS raise_immutable_violation();")
