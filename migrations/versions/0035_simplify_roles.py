"""精简内置角色：7 → 5。

删除旧角色（standard_owner, data_steward, researcher, model_engineer,
reviewer, read_only_user），新增新角色（platform_auditor, lab_director,
lab_member, lab_viewer）。保留 platform_administrator 不变。

已分配旧角色的用户自动迁移到最近的新角色：
  standard_owner / data_steward / model_engineer → lab_director
  researcher                     → lab_member
  reviewer                       → lab_member
  read_only_user                 → lab_viewer

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-27
"""

import json
import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

#: 旧角色 → 新角色 映射（用于用户角色迁移）。
_ROLE_MIGRATION: dict[str, str] = {
    "standard_owner": "lab_director",
    "data_steward": "lab_director",
    "model_engineer": "lab_director",
    "researcher": "lab_member",
    "reviewer": "lab_member",
    "read_only_user": "lab_viewer",
}

#: 旧角色代码列表（需删除）。
_OLD_ROLE_CODES = list(_ROLE_MIGRATION.keys())

#: 新角色定义（code, display_name, permissions）。
_NEW_ROLES = [
    (
        "platform_auditor",
        "平台监督员",
        [
            "standard:read", "fact:read", "artifact:read", "job:read",
            "model:read", "parameter:read", "department:read", "equipment:read",
            "ingestion:read", "provenance:read", "component:read", "flow:read",
            "audit:read", "system:health",
        ],
    ),
    (
        "lab_director",
        "实验室负责人",
        [
            "standard:read", "standard:write", "standard:publish",
            "fact:read", "fact:write",
            "artifact:read", "artifact:upload", "artifact:download",
            "job:read", "job:submit", "job:cancel",
            "model:read", "model:manage", "model:write", "model:publish", "model:predict",
            "parameter:read", "parameter:write", "parameter:review", "parameter:approve", "parameter:publish",
            "department:manage", "department:read",
            "equipment:manage", "equipment:read",
            "ingestion:read", "ingestion:write", "ingestion:publish",
            "provenance:read", "provenance:write", "provenance:publish",
            "component:manage", "component:read",
            "flow:manage", "flow:execute", "flow:read",
            "assistant:use",
        ],
    ),
    (
        "lab_member",
        "实验室成员",
        [
            "fact:read", "fact:write",
            "artifact:read", "artifact:upload", "artifact:download",
            "job:read", "job:submit", "job:cancel",
            "model:read", "model:predict",
            "parameter:read", "parameter:write",
            "department:read", "equipment:read",
            "ingestion:read", "ingestion:write",
            "provenance:read", "provenance:write",
            "component:read",
            "flow:execute", "flow:read",
            "assistant:use",
        ],
    ),
    (
        "lab_viewer",
        "实验室成员（只读）",
        [
            "standard:read", "fact:read", "artifact:read", "job:read",
            "model:read", "parameter:read", "department:read", "equipment:read",
            "ingestion:read", "provenance:read", "component:read", "flow:read",
            "assistant:use",
        ],
    ),
]


def upgrade() -> None:
    """精简角色：删除旧角色，新增新角色，迁移用户角色引用。"""

    # 1. 迁移已有用户的角色引用（角色代码是硬编码常量，直接内联到 SQL）
    for old_code, new_code in _ROLE_MIGRATION.items():
        op.execute(
            sa.text(
                f"UPDATE app_user "
                f"SET roles = ("
                f"  SELECT jsonb_agg("
                f"    CASE WHEN elem #>> '{{}}' = '{old_code}' THEN to_jsonb('{new_code}'::text) ELSE elem END"
                f"  ) FROM jsonb_array_elements(roles) AS elem"
                f") "
                f"WHERE roles @> CAST('{json.dumps([old_code])}' AS jsonb)"
            )
        )

    # 2. 删除旧角色
    for old_code in _OLD_ROLE_CODES:
        op.execute(
            sa.text("DELETE FROM role WHERE code = :code").bindparams(code=old_code)
        )

    # 3. 插入/更新新角色（INSERT ON CONFLICT DO UPDATE）
    for code, display_name, permissions in _NEW_ROLES:
        op.execute(
            sa.text(
                "INSERT INTO role (id, code, display_name, permissions) "
                "VALUES (gen_random_uuid(), :code, :name, CAST(:perms AS jsonb)) "
                "ON CONFLICT (code) DO UPDATE SET "
                "  display_name = EXCLUDED.display_name, "
                "  permissions = EXCLUDED.permissions"
            ).bindparams(
                code=code,
                name=display_name,
                perms=json.dumps(permissions),
            )
        )


def downgrade() -> None:
    """恢复 7 个旧角色（仅恢复角色定义，不恢复用户角色引用）。"""

    _RESTORED_ROLES = [
        ("standard_owner", "标准负责人", [
            "standard:read", "standard:write", "standard:publish",
            "department:read", "equipment:manage", "equipment:read",
            "component:manage", "component:read", "flow:manage", "flow:read",
            "assistant:use",
        ]),
        ("data_steward", "数据管家", [
            "fact:read", "fact:write", "artifact:read", "artifact:upload", "artifact:download",
            "department:read", "equipment:read", "ingestion:read", "ingestion:write",
            "ingestion:publish", "provenance:read", "provenance:write", "provenance:publish",
            "parameter:read", "parameter:write", "component:read", "flow:execute", "flow:read",
            "assistant:use",
        ]),
        ("researcher", "研究员", [
            "fact:read", "artifact:read", "artifact:download", "job:read", "job:submit",
            "department:read", "equipment:read", "provenance:read", "provenance:write",
            "parameter:read", "parameter:write", "component:read", "flow:execute", "flow:read",
            "assistant:use",
        ]),
        ("model_engineer", "模型工程师", [
            "model:read", "model:manage", "model:write", "model:publish", "model:predict",
            "department:read", "equipment:read", "component:manage", "component:read",
            "flow:manage", "flow:execute", "flow:read", "assistant:use",
        ]),
        ("reviewer", "审核员", [
            "parameter:read", "parameter:review", "parameter:approve", "parameter:publish",
            "department:read", "equipment:read", "assistant:use",
        ]),
        ("read_only_user", "只读用户", [
            "fact:read", "standard:read", "parameter:read", "department:read",
            "equipment:read", "component:read", "flow:read", "assistant:use",
        ]),
    ]

    # 删除新角色
    for code, _, _ in _NEW_ROLES:
        op.execute(sa.text("DELETE FROM role WHERE code = :code").bindparams(code=code))

    # 恢复旧角色
    for code, display_name, permissions in _RESTORED_ROLES:
        op.execute(
            sa.text(
                "INSERT INTO role (id, code, display_name, permissions) "
                "VALUES (gen_random_uuid(), :code, :name, CAST(:perms AS jsonb)) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=display_name, perms=json.dumps(permissions))
        )
