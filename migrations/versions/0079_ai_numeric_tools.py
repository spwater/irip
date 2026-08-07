"""0079: AI 数值计算工具种子数据

插入两个内置数值工具到 ai_tool 表：
- evaluate_expression：标量和序列上的受限数学表达式
- describe_series：口径明确的序列描述统计

升级要求：
- INSERT ... ON CONFLICT (name) DO NOTHING，不覆盖管理员已编辑的记录；
- 默认 enabled=true，category='ai_tool'，required_permission='assistant:use'；
- parameters_schema 引用 contracts.py 的 canonical schema 常量，防漂移。

降级：按 name 删除这两个内置工具，不影响其他记录。

Revision ID: 0079
Revises: 0078
Create Date: 2026-08-07
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

# 固定 UUID（确定性插入，便于幂等验证）
_EVALUATE_EXPRESSION_ID = "018f0000-0000-7000-8000-000000000010"
_DESCRIBE_SERIES_ID = "018f0000-0000-7000-8000-000000000011"


def _get_schemas() -> tuple[str, str]:
    """从 contracts.py 获取 canonical schema 的 JSON 字符串。

    通过 Python import 引用同一常量，防止迁移和代码漂移。
    """
    from packages.ai.numeric.contracts import (
        DESCRIBE_SERIES_SCHEMA,
        EVALUATE_EXPRESSION_SCHEMA,
    )

    return (
        json.dumps(EVALUATE_EXPRESSION_SCHEMA, ensure_ascii=False, sort_keys=True),
        json.dumps(DESCRIBE_SERIES_SCHEMA, ensure_ascii=False, sort_keys=True),
    )


def upgrade() -> None:
    """插入 evaluate_expression 和 describe_series 两个数值工具。"""
    eval_schema, desc_schema = _get_schemas()

    op.execute(
        sa.text(
            """
            INSERT INTO ai_tool (
                id, name, display_name, description,
                required_permission, parameters_schema,
                category, enabled, lock_version, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid), :name, :display_name, :description,
                :required_permission, CAST(:parameters_schema AS jsonb),
                :category, true, 0, now(), now()
            )
            ON CONFLICT (name) DO NOTHING
            """
        ).bindparams(
            sa.bindparam("id", _EVALUATE_EXPRESSION_ID),
            sa.bindparam("name", "evaluate_expression"),
            sa.bindparam("display_name", "数值表达式计算"),
            sa.bindparam(
                "description",
                "对标量和序列执行精确的数学表达式计算。"
                "支持算术运算和白名单函数。"
                "可引用所选 Fact/Artifact 的序列。"
                "需要单个最终值时应在表达式中聚合。",
            ),
            sa.bindparam("required_permission", "assistant:use"),
            sa.bindparam("parameters_schema", eval_schema),
            sa.bindparam("category", "ai_tool"),
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO ai_tool (
                id, name, display_name, description,
                required_permission, parameters_schema,
                category, enabled, lock_version, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid), :name, :display_name, :description,
                :required_permission, CAST(:parameters_schema AS jsonb),
                :category, true, 0, now(), now()
            )
            ON CONFLICT (name) DO NOTHING
            """
        ).bindparams(
            sa.bindparam("id", _DESCRIBE_SERIES_ID),
            sa.bindparam("name", "describe_series"),
            sa.bindparam("display_name", "序列描述统计"),
            sa.bindparam(
                "description",
                "计算序列的描述统计量：count、sum、mean、"
                "总体/样本方差、标准差、min、max、median、分位数、"
                "偏度和峰度，以及缺失值计数。",
            ),
            sa.bindparam("required_permission", "assistant:use"),
            sa.bindparam("parameters_schema", desc_schema),
            sa.bindparam("category", "ai_tool"),
        )
    )


def downgrade() -> None:
    """删除两个数值工具种子数据。"""
    op.execute(
        "DELETE FROM ai_tool WHERE name IN ('evaluate_expression', 'describe_series')"
    )
