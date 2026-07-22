"""user_roles: add roles JSONB column to app_user.

增量迁移：为 app_user 表添加 roles JSONB 列，存储用户角色代码列表
（如 ``["platform_administrator"]``）。

此前 app_user 表无 roles 列，LocalAuthBackend 与 AuthService 均硬编码
``roles=[]``，导致登录后 JWT 中 roles 始终为空、权限检查全部 403。
本迁移补齐 schema 缺口，并将现有管理员 admin@irip.local 的 roles
回写为 ``["platform_administrator"]``。

幂等性：
  - 列添加使用 ``ADD COLUMN IF NOT EXISTS``；
  - 管理员 roles 回写使用条件 UPDATE（仅当 roles 为空时）；
  - 迁移本身由 alembic 版本表保证只执行一次。

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 app_user 添加 roles JSONB 列并回写管理员角色。"""
    # ---- 添加 roles 列（幂等：IF NOT EXISTS）----
    op.execute(
        "ALTER TABLE app_user "
        "ADD COLUMN IF NOT EXISTS roles JSONB "
        "NOT NULL DEFAULT '[]'::jsonb"
    )

    # ---- 回写现有管理员角色（仅当 roles 为空数组或 NULL 时）----
    # admin@irip.local 由 bootstrap 创建，此前无 roles 列，此处补写。
    op.execute(
        sa.text(
            "UPDATE app_user "
            "SET roles = CAST(:roles AS jsonb) "
            "WHERE email = :email "
            "AND (roles IS NULL OR roles = '[]'::jsonb)"
        ).bindparams(
            roles='["platform_administrator"]',
            email="admin@irip.local",
        )
    )

    # ---- irip_app 权限：重新授权 app_user 表（含新列）----
    # 0003 已 GRANT app_user 表级权限，新列继承表级权限；
    # 此处显式重新 GRANT 以确保列级权限覆盖（幂等）。
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON app_user TO irip_app"
    )


def downgrade() -> None:
    """回滚：删除 app_user.roles 列。"""
    op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS roles")
