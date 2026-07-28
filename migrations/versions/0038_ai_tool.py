"""0038: ai_tool 表 — AI 工具声明层从硬编码迁移到数据库管理。

将 AI 工具的"声明层"（name / display_name / description / required_permission /
candidate / parameters_schema）从 ``packages/ai/tools.py`` 的硬编码元组迁移到
数据库 ``ai_tool`` 表，运行时由 ``ToolRegistry`` 从 DB 加载。执行逻辑
（``AIService._execute_tool`` 的 if-elif 分派）保持硬编码不变。

变更内容：
1. 创建 ``ai_tool`` 表（全局表，无 organization_id，D-6）：
   - id: UUID 主键（gen_random_uuid）
   - name: TEXT NOT NULL UNIQUE — 工具唯一键，创建后不可改
   - display_name / description / required_permission: TEXT NOT NULL
   - candidate: BOOLEAN NOT NULL DEFAULT false — true=候选(需审批) false=只读
   - parameters_schema: JSONB NOT NULL DEFAULT '{}'::jsonb
   - enabled: BOOLEAN NOT NULL DEFAULT true
   - lock_version: INTEGER NOT NULL DEFAULT 0 — 乐观锁（D-2）
   - created_at / updated_at: TIMESTAMPTZ NOT NULL DEFAULT now()
   - updated_by: UUID NULL — 最后修改人
2. irip_app GRANT SELECT, INSERT, UPDATE（不授予 DELETE，D-5 不支持删除）；
3. RLS：ai_tool 为全局表（D-6），不启用 tenant_isolation 策略。

种子数据由应用启动时 ``seed_tools_if_empty`` 写入（12 条内置工具）。

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 ai_tool 表 + 授权。"""

    # ---- ai_tool 表 ----
    op.create_table(
        "ai_tool",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=False),
        sa.Column("required_permission", sa.TEXT, nullable=False),
        sa.Column(
            "candidate",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "parameters_schema",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean,
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
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
        sa.Column("updated_by", sa.UUID, nullable=True),
        sa.UniqueConstraint("name", name="uq_ai_tool_name"),
    )

    # ---- irip_app 权限（不含 DELETE，D-5 不支持删除） ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON ai_tool TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除表。"""
    op.execute("REVOKE SELECT, INSERT, UPDATE ON ai_tool FROM irip_app")
    op.drop_table("ai_tool")
