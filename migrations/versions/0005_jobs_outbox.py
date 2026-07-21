"""jobs + outbox: FK, lease index, irip_app grants.

T01（迁移 0001）已创建 job 和 outbox_event 根表骨架（含全部字段 + 索引）。
本迁移在已有表上补充：
- job.created_by FK → app_user.id（0001 时 app_user 尚不存在）；
- job.lease_expires_at 索引（高效回收过期租约）；
- irip_app 角色对 job / outbox_event 的 CRUD 权限。

注意：使用 ALTER TABLE，不重建表（0001 已建）。

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """在已有 job/outbox_event 表上补充 FK、索引、权限。"""
    # ---- job.created_by FK → app_user.id ----
    # 0001 建表时 app_user 尚不存在，此处补加 FK
    op.create_foreign_key(
        "fk_job_created_by",
        "job",
        "app_user",
        ["created_by"],
        ["id"],
    )

    # ---- job.lease_expires_at 索引（高效回收过期租约）----
    op.create_index(
        "ix_job_lease_expires_at",
        "job",
        ["lease_expires_at"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON job, outbox_event TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除索引、删除 FK。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON job, outbox_event FROM irip_app"
    )

    op.drop_index("ix_job_lease_expires_at", table_name="job")

    op.drop_constraint("fk_job_created_by", "job", type_="foreignkey")
