"""platform base: extensions and root tables.

创建 PostgreSQL 扩展（pgcrypto / vector）以及 V0 平台骨架三张根表：
- audit_event: 仅追加审计日志（应用角色 REVOKE UPDATE, DELETE）；
- outbox_event: Outbox 模式事件表（dispatcher 轮询未投递事件）；
- job: 异步作业表（租约 + 幂等键 + 乐观锁）。

表结构参考 docs/arch-v0.md §3.1 字段级定义。
所有主键 UUID DEFAULT gen_random_uuid()（需 pgcrypto 扩展）。
所有时间戳 TIMESTAMP(timezone=True) DEFAULT now()。

Revision ID: 0001
Revises:
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """启用扩展 + 创建根表。

    幂等：所有 CREATE EXTENSION 使用 IF NOT EXISTS。
    """
    # ---- 扩展 ----
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- audit_event（仅追加审计日志）----
    op.create_table(
        "audit_event",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID, nullable=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("action", sa.TEXT, nullable=False),
        sa.Column("resource_type", sa.TEXT, nullable=True),
        sa.Column("resource_id", sa.UUID, nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("ip", sa.TEXT, nullable=True),
        sa.Column("user_agent", sa.TEXT, nullable=True),
    )
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"])
    op.create_index(
        "ix_audit_event_actor_user_id", "audit_event", ["actor_user_id"]
    )
    op.create_index(
        "ix_audit_event_organization_id", "audit_event", ["organization_id"]
    )

    # ---- outbox_event（Outbox 模式事件表）----
    op.create_table(
        "outbox_event",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("aggregate_type", sa.TEXT, nullable=False),
        sa.Column("aggregate_id", sa.UUID, nullable=False),
        sa.Column("event_type", sa.TEXT, nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # 拉取未投递事件：delivered_at NULLS FIRST + occurred_at 升序
    op.execute(
        "CREATE INDEX ix_outbox_event_pending "
        "ON outbox_event (delivered_at NULLS FIRST, occurred_at)"
    )

    # ---- job（异步作业表）----
    op.create_table(
        "job",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("kind", sa.TEXT, nullable=False),
        sa.Column("status", sa.TEXT, nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("idempotency_key", sa.TEXT, nullable=False),
        sa.Column(
            "attempt",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.INTEGER,
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("run_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.TEXT, nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("last_error", JSONB, nullable=True),
        sa.Column("created_by", sa.UUID, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_job_organization_idempotency_key",
        ),
    )
    op.create_index("ix_job_organization_id", "job", ["organization_id"])
    op.create_index("ix_job_status", "job", ["status"])
    op.create_index("ix_job_run_after", "job", ["run_after"])


def downgrade() -> None:
    """回滚：按创建逆序删除表，最后移除扩展。"""
    op.drop_index("ix_job_run_after", table_name="job")
    op.drop_index("ix_job_status", table_name="job")
    op.drop_index("ix_job_organization_id", table_name="job")
    op.drop_table("job")

    op.execute("DROP INDEX IF EXISTS ix_outbox_event_pending")
    op.drop_table("outbox_event")

    op.drop_index("ix_audit_event_organization_id", table_name="audit_event")
    op.drop_index("ix_audit_event_actor_user_id", table_name="audit_event")
    op.drop_index("ix_audit_event_occurred_at", table_name="audit_event")
    op.drop_table("audit_event")

    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
