"""flow_definition + flow_definition_version + flow_run + flow_node_execution: 流程引擎。

增量迁移（IRIP V2-T03 — 流程引擎）：
- 创建 flow_definition 表：流程定义主表，组织内按 (organization_id, code) 唯一，
  含 status/lock_version/created_at/updated_at；
- 创建 flow_definition_version 表：流程版本表，按 (flow_definition_id, version)
  唯一，含 nodes_json/edges_json(JSONB)/random_seed/digest/status/published_at；
- 创建 flow_run 表：流程执行记录，关联 flow_definition_version + job，
  含 status/input_snapshot(JSONB)/output_digest/started_at/completed_at；
- 创建 flow_node_execution 表：节点执行记录，FK→flow_run.id CASCADE，
  含 status/input_summary/output_summary/diagnostics(JSONB)/duration_ms；
- 索引：ix_flow_definition_organization_id, ix_flow_version_definition_id,
  ix_flow_run_organization_id, ix_flow_run_version_id,
  ix_flow_node_execution_run_id；
- irip_app GRANT 四表权限；
- re-seed 7 个内置角色（添加 flow:manage, flow:execute, flow:read 权限）。

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建四张表，授权，re-seed roles。"""

    # ---- flow_definition 表 ----
    op.create_table(
        "flow_definition",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
            sa.INTEGER,
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
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_flow_definition_org_code"
        ),
    )
    op.create_index(
        "ix_flow_definition_organization_id",
        "flow_definition",
        ["organization_id"],
    )

    # ---- flow_definition_version 表 ----
    op.create_table(
        "flow_definition_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("flow_definition_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column(
            "nodes_json",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "edges_json",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "random_seed",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("digest", sa.TEXT, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'published'"),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flow_definition_id"],
            ["flow_definition.id"],
            name="fk_flow_version_definition_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "flow_definition_id",
            "version",
            name="uq_flow_version_def_ver",
        ),
    )
    op.create_index(
        "ix_flow_version_definition_id",
        "flow_definition_version",
        ["flow_definition_id"],
    )

    # ---- flow_run 表 ----
    op.create_table(
        "flow_run",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("flow_version_id", sa.UUID, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("job_id", sa.UUID, nullable=True),
        sa.Column(
            "input_snapshot",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("output_digest", sa.TEXT, nullable=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flow_version_id"],
            ["flow_definition_version.id"],
            name="fk_flow_run_version_id",
        ),
    )
    op.create_index(
        "ix_flow_run_organization_id",
        "flow_run",
        ["organization_id"],
    )
    op.create_index(
        "ix_flow_run_version_id",
        "flow_run",
        ["flow_version_id"],
    )

    # ---- flow_node_execution 表 ----
    op.create_table(
        "flow_node_execution",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("flow_run_id", sa.UUID, nullable=False),
        sa.Column("node_id", sa.TEXT, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "input_summary",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output_summary",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("diagnostics", JSONB, nullable=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("duration_ms", sa.INTEGER, nullable=True),
        sa.ForeignKeyConstraint(
            ["flow_run_id"],
            ["flow_run.id"],
            name="fk_flow_node_execution_run_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_flow_node_execution_run_id",
        "flow_node_execution",
        ["flow_run_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON flow_definition, flow_definition_version, "
        "flow_run, flow_node_execution TO irip_app"
    )

    # ---- re-seed 7 个内置角色（ON CONFLICT DO UPDATE，写入 flow 权限）----
    import json

    from packages.auth.permissions import BUILTIN_ROLES

    for code, info in BUILTIN_ROLES.items():
        display_name = info["display_name"]
        permissions = info["permissions"]
        op.execute(
            sa.text(
                "INSERT INTO role (code, display_name, permissions) "
                "VALUES (:code, :display_name, "
                "CAST(:permissions AS jsonb)) "
                "ON CONFLICT (code) DO UPDATE SET "
                "display_name = EXCLUDED.display_name, "
                "permissions = EXCLUDED.permissions"
            ).bindparams(
                code=code,
                display_name=display_name,
                permissions=json.dumps([str(p) for p in permissions]),
            )
        )


def downgrade() -> None:
    """回滚：撤销权限、删除四张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON flow_definition, flow_definition_version, "
        "flow_run, flow_node_execution FROM irip_app"
    )

    op.drop_index(
        "ix_flow_node_execution_run_id",
        table_name="flow_node_execution",
    )
    op.drop_table("flow_node_execution")

    op.drop_index("ix_flow_run_version_id", table_name="flow_run")
    op.drop_index("ix_flow_run_organization_id", table_name="flow_run")
    op.drop_table("flow_run")

    op.drop_index(
        "ix_flow_version_definition_id",
        table_name="flow_definition_version",
    )
    op.drop_table("flow_definition_version")

    op.drop_index(
        "ix_flow_definition_organization_id",
        table_name="flow_definition",
    )
    op.drop_table("flow_definition")
