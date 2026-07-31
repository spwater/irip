"""Drop redundant columns on parameter_version + clean evidence_set_version.members.

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-01

清理两处冗余/遗留:

1. parameter_version 表上的 evidence_set_version_id 和 recipe_version_id 两列
   与 derivation_run 表上的同名列 100% 重复（已验证全部 4 条数据完全一致）。
   通过 parameter_version → derivation_run 一跳即可查到对应的
   evidence_set_version / recipe_version，无需冗余存储。本次删除这两列。

2. evidence_set_version.members JSONB 中残留的 fact_revision（int）与
   fact_revision_id（UUID）字段。fact_revision 表已在迁移 0055 中删除，
   这两个字段已无意义。本次用 SQL UPDATE 把每个 member 的 JSONB 去掉这两个
   key，使 members 只保留 fact_id / observation_id / decision / reason。

注意: derivation_run 表上也有同名列 evidence_set_version_id / recipe_version_id，
      这些列保留不动，本次仅删除 parameter_version 上的两列。
"""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 parameter_version 冗余列 + 清理 members JSONB 遗留字段。"""

    # 1. 删除 parameter_version 上的冗余列
    op.drop_column("parameter_version", "evidence_set_version_id")
    op.drop_column("parameter_version", "recipe_version_id")

    # 2. 清理 evidence_set_version.members JSONB 中的遗留字段
    #    去掉每个 member 的 fact_revision 和 fact_revision_id 两个 key。
    #    evidence_set_version 是不可变表（迁移 0033/0049 创建的
    #    BEFORE UPDATE OR DELETE 触发器 prevent_modify_evidence_set_version
    #    会阻止 UPDATE）。此处临时禁用触发器以执行数据清理，随后恢复。
    op.execute(
        "ALTER TABLE evidence_set_version DISABLE TRIGGER "
        "prevent_modify_evidence_set_version"
    )
    op.execute(
        """
        UPDATE evidence_set_version
        SET members = (
          SELECT jsonb_agg(
            member - 'fact_revision' - 'fact_revision_id'
          )
          FROM jsonb_array_elements(members) AS member
        )
        WHERE members IS NOT NULL;
        """
    )
    op.execute(
        "ALTER TABLE evidence_set_version ENABLE TRIGGER "
        "prevent_modify_evidence_set_version"
    )


def downgrade() -> None:
    """恢复 parameter_version 冗余列（members 已清理的字段不可恢复）。"""

    # 恢复 parameter_version 上的两列（列为空，原数据已随列删除丢失）
    op.add_column(
        "parameter_version",
        sa.Column("evidence_set_version_id", sa.UUID, nullable=True),
    )
    op.add_column(
        "parameter_version",
        sa.Column("recipe_version_id", sa.UUID, nullable=True),
    )

    # 注意: evidence_set_version.members 中已删除的 fact_revision /
    #       fact_revision_id 字段无法恢复，downgrade 不做处理。
