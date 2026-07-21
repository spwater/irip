"""artifacts: artifact_blob + artifact tables.

创建内容寻址工件存储两张表（docs/arch-v0.md §3.1 第 294-298 行）：
- artifact_blob: 内容寻址 blob（sha256 PK, object_key UNIQUE, 去重）；
- artifact: 工件业务链接（id UUID PK, sha256 FK→artifact_blob, uploaded_by FK→app_user）。

设计要点：
- 相同内容多业务引用共享同一 artifact_blob；
- object_key = sha256/<前2位>/<digest>；
- irip_app 角色获得 artifact 表的 CRUD 权限。

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 artifact_blob + artifact 表 + 索引 + 权限。"""
    # ---- artifact_blob（内容寻址 blob）----
    op.create_table(
        "artifact_blob",
        sa.Column("sha256", sa.TEXT, primary_key=True),
        sa.Column("object_key", sa.TEXT, nullable=False),
        sa.Column("size_bytes", sa.BIGINT, nullable=False),
        sa.Column("media_type", sa.TEXT, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("object_key", name="uq_artifact_blob_object_key"),
    )

    # ---- artifact（工件业务链接）----
    op.create_table(
        "artifact",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("sha256", sa.TEXT, nullable=False),
        sa.Column("filename", sa.TEXT, nullable=False),
        sa.Column("media_type", sa.TEXT, nullable=False),
        sa.Column("size_bytes", sa.BIGINT, nullable=False),
        sa.Column("uploaded_by", sa.UUID, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sha256"], ["artifact_blob.sha256"],
            name="fk_artifact_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["app_user.id"],
            name="fk_artifact_uploaded_by",
        ),
    )

    # ---- 索引 ----
    op.create_index(
        "ix_artifact_organization_id", "artifact", ["organization_id"]
    )
    op.create_index("ix_artifact_uploaded_by", "artifact", ["uploaded_by"])
    op.create_index("ix_artifact_sha256", "artifact", ["sha256"])

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON artifact_blob, artifact TO irip_app"
    )


def downgrade() -> None:
    """回滚：删除表与索引，撤销权限。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON artifact_blob, artifact FROM irip_app"
    )

    op.drop_index("ix_artifact_sha256", table_name="artifact")
    op.drop_index("ix_artifact_uploaded_by", table_name="artifact")
    op.drop_index("ix_artifact_organization_id", table_name="artifact")
    op.drop_table("artifact")
    op.drop_table("artifact_blob")
