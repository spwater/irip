"""0036: 删除 scope_grant 表 + app_user 新增 department_id 列

变更内容：
1. 删除 scope_grant 表及其索引（范围授权功能移除）；
2. app_user 新增 department_id 列（UUID，nullable，无 FK 约束，保持松耦合）。

全新部署说明：
- 0034_db_roles.py 的 _BUSINESS_TABLES 已移除 scope_grant，全新部署不会再授权；
- 对已有部署（0034 已授权 scope_grant 给 irip_runtime），upgrade 先 REVOKE 再 DROP。

Revision ID: 0036_remove_scope_grant_add_department
Revises: 0035_simplify_roles
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 scope_grant 表，app_user 新增 department_id 列。"""

    # ---- 1. REVOKE irip_runtime 对 scope_grant 的权限 ----
    # 0034 可能已授权 scope_grant 给 irip_runtime，删除前先收回。
    # 使用 DO 块避免表或角色不存在时报错。
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE 'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE scope_grant FROM irip_runtime';
        EXCEPTION
            WHEN undefined_table THEN NULL;
            WHEN undefined_object THEN NULL;
        END
        $$;
        """
    )

    # ---- 2. 删除 scope_grant 表及其索引 ----
    # 索引：ix_scope_grant_department_id (0006)、
    #       ix_scope_grant_user_resource_action (0003)、
    #       ix_scope_grant_role_resource_action (0003)
    op.execute("DROP INDEX IF EXISTS ix_scope_grant_department_id;")
    op.execute("DROP INDEX IF EXISTS ix_scope_grant_user_resource_action;")
    op.execute("DROP INDEX IF EXISTS ix_scope_grant_role_resource_action;")
    op.execute("DROP TABLE IF EXISTS scope_grant;")

    # ---- 3. app_user 新增 department_id 列 ----
    op.add_column(
        "app_user",
        sa.Column("department_id", sa.UUID, nullable=True),
    )


def downgrade() -> None:
    """恢复 scope_grant 表，删除 app_user.department_id 列。"""

    # ---- 1. 删除 app_user.department_id 列 ----
    op.drop_column("app_user", "department_id")

    # ---- 2. 重建 scope_grant 表 ----
    # 参考 0003_authorization_audit + 0006_department 的表结构
    op.create_table(
        "scope_grant",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.UUID, nullable=True),
        sa.Column("role_id", sa.UUID, nullable=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("object_root_id", sa.UUID, nullable=True),
        sa.Column("resource_type", sa.TEXT, nullable=False),
        sa.Column("action", sa.TEXT, nullable=False),
        sa.Column(
            "effective_from", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "effective_to", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("department_id", sa.UUID, nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_scope_grant_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name="fk_scope_grant_role_id"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["department.id"],
            name="fk_scope_grant_department_id",
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND role_id IS NULL) "
            "OR (user_id IS NULL AND role_id IS NOT NULL)",
            name="ck_scope_grant_user_or_role",
        ),
    )

    # 重建索引
    op.create_index(
        "ix_scope_grant_user_resource_action",
        "scope_grant",
        ["user_id", "resource_type", "action"],
    )
    op.create_index(
        "ix_scope_grant_role_resource_action",
        "scope_grant",
        ["role_id", "resource_type", "action"],
    )
    op.create_index(
        "ix_scope_grant_department_id",
        "scope_grant",
        ["department_id"],
    )
