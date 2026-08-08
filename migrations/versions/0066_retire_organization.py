"""0066: 多租户隔离键升级 — 阶段3退役 organization_id

彻底清除所有 organization_id 残留：
1. 对所有含 organization_id 列的表执行 DROP COLUMN IF EXISTS
2. DROP TABLE IF EXISTS organization（如存在）
3. 清理 department 表上可能残留的 organization_id 索引/约束
4. 旧 GUC app.current_org_id 为会话级设置，无需主动删除
   （仅在注释中说明已废弃，应用层不再设置此 GUC）

⚠️ 此迁移不可逆：downgrade 为空操作。
一旦执行，organization_id 列及 organization 表将被永久删除。

Revision ID: 0066
Revises: 0065
Create Date: 2026-08-24
"""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

#: 所有曾经拥有 organization_id 列的表
_ALL_TABLES: list[str] = [
    "app_user",
    "department",
    "fact",
    "parameter",
    "evidence_set",
    "artifact",
    "model",
    "transformation_recipe",
    "component",
    "flow_definition",
    "flow_run",
    "industrial_object",
    "equipment",
    "job",
    "derivation_run",
    "audit_event",
    "secret",
    "backup_record",
    "ai_conversation",
    "ai_message",
    "ai_showcase_item",
    "ai_config",
    "ai_tool",
]


def upgrade() -> None:
    """DROP organization_id 列 + DROP organization 表 + 清理残留索引/约束。"""

    # === 1. 清理 department 表上可能残留的 organization_id 索引/约束 ===
    # 旧唯一约束 uq_department_org_code（已在 0062 中被替换为 uq_department_parent_code，
    # 但安全起见再次尝试删除）
    op.execute("ALTER TABLE department DROP CONSTRAINT IF EXISTS uq_department_org_code")
    # department 表上可能的 organization_id 索引
    op.execute("DROP INDEX IF EXISTS ix_department_organization_id")

    # === 2. 对所有表执行 DROP COLUMN organization_id IF EXISTS ===
    # 先删除可能依赖 organization_id 的外键约束，再删除列
    for table in _ALL_TABLES:
        # 安全删除：IF EXISTS 确保不存在的列不会报错
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS organization_id CASCADE")

    # === 3. 删除 organization 表（如果存在） ===
    op.execute("DROP TABLE IF EXISTS organization")

    # === 4. 旧 GUC app.current_org_id 已废弃 ===
    # GUC 是会话/事务级设置，无需主动删除。
    # 应用层 (database.py) 已不再设置此 GUC，RLS 策略也不再引用它。
    # 注释说明：app.current_org_id 已在阶段3中废弃。


def downgrade() -> None:
    """不可逆迁移。

    阶段3 退役后不再回滚。organization_id 列及 organization 表已被永久删除，
    所有应用代码和 RLS 策略已锚定 department_id。
    如需回滚到阶段2，请从 0065 之前的备份恢复数据库。
    """
    pass
