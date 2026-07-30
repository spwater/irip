"""0049: 修正不可变触发器保护的表（H-01）

0033 迁移错误地将 flow_node_execution 和 evidence_set 标记为不可变表，
创建了阻止 UPDATE/DELETE 的触发器。但业务逻辑需要：
- flow_node_execution: flow_runtime 需要将状态从 pending 更新为 running/succeeded/failed；
- evidence_set: evidence.py 需要更新 status 从 draft 变为 frozen。

真正不可变的应该是 evidence_set_version（冻结后的快照不可修改）。

此迁移：
1. DROP flow_node_execution 上的 prevent_modify 触发器；
2. DROP evidence_set 上的 prevent_modify 触发器；
3. 在 evidence_set_version 上创建 prevent_modify 触发器；
4. 恢复 irip_runtime 对 flow_node_execution 和 evidence_set 的 UPDATE/DELETE 权限；
5. 对 evidence_set_version REVOKE UPDATE/DELETE。

对于已修改 0033 的 fresh DB（0033 已不再对 flow_node_execution/evidence_set
创建触发器），此迁移的 DROP 操作是幂等的（IF EXISTS）。

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-31
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """修正不可变触发器保护的表。"""
    # 1. 确保触发器函数存在（幂等，fresh DB 上 0033 已创建）
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

    # 2. DROP flow_node_execution 上的触发器（幂等）
    op.execute(
        "DROP TRIGGER IF EXISTS prevent_modify_flow_node_execution ON flow_node_execution;"
    )

    # 3. DROP evidence_set 上的触发器（幂等）
    op.execute(
        "DROP TRIGGER IF EXISTS prevent_modify_evidence_set ON evidence_set;"
    )

    # 4. 在 evidence_set_version 上创建 prevent_modify 触发器
    op.execute(
        """
        DROP TRIGGER IF EXISTS prevent_modify_evidence_set_version ON evidence_set_version;
        CREATE TRIGGER prevent_modify_evidence_set_version
            BEFORE UPDATE OR DELETE ON evidence_set_version
            FOR EACH ROW
            EXECUTE FUNCTION raise_immutable_violation();
        """
    )

    # 5. 恢复 irip_runtime 对 flow_node_execution 的 UPDATE/DELETE 权限
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                GRANT UPDATE, DELETE ON TABLE flow_node_execution TO irip_runtime;
            END IF;
        END
        $$;
        """
    )

    # 6. 恢复 irip_runtime 对 evidence_set 的 UPDATE/DELETE 权限
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                GRANT UPDATE, DELETE ON TABLE evidence_set TO irip_runtime;
            END IF;
        END
        $$;
        """
    )

    # 7. 对 evidence_set_version REVOKE UPDATE/DELETE（不可变表权限保护）
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                REVOKE UPDATE, DELETE ON TABLE evidence_set_version FROM irip_runtime;
                GRANT SELECT, INSERT ON TABLE evidence_set_version TO irip_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """回滚：恢复 0033 的错误状态。"""
    # 1. DROP evidence_set_version 触发器
    op.execute(
        "DROP TRIGGER IF EXISTS prevent_modify_evidence_set_version ON evidence_set_version;"
    )

    # 2. 恢复 flow_node_execution 触发器
    op.execute(
        """
        DROP TRIGGER IF EXISTS prevent_modify_flow_node_execution ON flow_node_execution;
        CREATE TRIGGER prevent_modify_flow_node_execution
            BEFORE UPDATE OR DELETE ON flow_node_execution
            FOR EACH ROW
            EXECUTE FUNCTION raise_immutable_violation();
        """
    )

    # 3. 恢复 evidence_set 触发器
    op.execute(
        """
        DROP TRIGGER IF EXISTS prevent_modify_evidence_set ON evidence_set;
        CREATE TRIGGER prevent_modify_evidence_set
            BEFORE UPDATE OR DELETE ON evidence_set
            FOR EACH ROW
            EXECUTE FUNCTION raise_immutable_violation();
        """
    )

    # 4. 收回 flow_node_execution 和 evidence_set 的 UPDATE/DELETE
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                REVOKE UPDATE, DELETE ON TABLE flow_node_execution FROM irip_runtime;
                REVOKE UPDATE, DELETE ON TABLE evidence_set FROM irip_runtime;
            END IF;
        END
        $$;
        """
    )

    # 5. 恢复 evidence_set_version 的 UPDATE/DELETE
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irip_runtime') THEN
                GRANT UPDATE, DELETE ON TABLE evidence_set_version TO irip_runtime;
            END IF;
        END
        $$;
        """
    )
