"""0031: 补全本地开发时手动添加但未固化到迁移的列

Docker 化交付后，数据库卷被清空并从 0001 重新迁移到 0030，
暴露出 3 个在本地开发时手动 ALTER TABLE 添加、但从未写入 alembic 迁移脚本的列。
缺少这些列会导致对应接口 500：

1. ai_conversation.system_context —— AI 对话创建/配置 500
   （packages/ai/service.py 中 AIConversation.system_context 定义）
2. fact_revision.operator —— 入库实验记录 500
   （packages/facts/entities.py 中 FactRevision.operator 定义）
3. flow_definition.operator —— 新建/编辑任务 500
   （packages/components/flow_runtime.py 中 FlowDefinition.operator 定义）

原始 SQL:
  ALTER TABLE ai_conversation ADD COLUMN IF NOT EXISTS system_context TEXT NULL;
  ALTER TABLE fact_revision ADD COLUMN IF NOT EXISTS operator TEXT NULL;
  ALTER TABLE flow_definition ADD COLUMN IF NOT EXISTS operator TEXT NULL;

Revision ID: 0031_missing_columns
Revises: 0030_component_exp_object_code
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_conversation", sa.Column("system_context", sa.TEXT, nullable=True))
    op.add_column("fact_revision", sa.Column("operator", sa.TEXT, nullable=True))
    op.add_column("flow_definition", sa.Column("operator", sa.TEXT, nullable=True))


def downgrade() -> None:
    op.drop_column("flow_definition", "operator")
    op.drop_column("fact_revision", "operator")
    op.drop_column("ai_conversation", "system_context")
