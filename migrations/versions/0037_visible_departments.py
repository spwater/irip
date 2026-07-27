"""0037: equipment + industrial_object 新增跨实验室可见性字段

跨实验室可见性：设备 / 实验对象除归属实验室（department_id）外，可通过
visible_departments 数组声明对哪些额外实验室可见。

可见性规则（应用层在 equipment / objects 路由与仓库实现）：
- 用户能看到一个设备/对象，当且仅当：
  1. 其 department_id 属于用户实验室（equipment 含子实验室；industrial_object
     精确匹配），**或者**
  2. 其 visible_departments 数组包含用户的 department_id；
- 平台管理员/监督员不受限制，看全部。

变更内容：
1. equipment 表新增 visible_departments 列：JSONB，NOT NULL，
   默认 ``'[]'::jsonb``（向后兼容，现有设备对其它实验室不可见）；
   equipment 已有 department_id（0016），本次不加。
2. industrial_object 表新增两列：
   - department_id：UUID，nullable，无 FK（松耦合，与 0036 给 app_user 加
     department_id 的风格一致，避免部门删除时级联影响实验对象）；
   - visible_departments：JSONB，NOT NULL，默认 ``'[]'::jsonb``；
   现有实验对象 department_id 为 NULL、visible_departments 为 []，
   仅归属实验室可见（向后兼容）。
3. 索引：
   - ix_equipment_visible_departments（GIN）——加速 equipment 可见性 @> 查询；
   - ix_industrial_object_department_id（B-tree）——加速 department_id == 过滤；
   - ix_industrial_object_visible_departments（GIN）——加速可见性 @> 查询。

说明：
- 两表的表级权限已在 0009/0016（irip_app）、0034（irip_runtime）授予，
  新增列自动继承表级 SELECT/INSERT/UPDATE/DELETE 权限，无需再 GRANT；
- RLS tenant_isolation 策略（0032）仅按 organization_id 隔离，与
  visible_departments 的实验室级可见性正交，不冲突；
- industrial_object 的 department_id 不加 FK 约束，保持与 app_user.department_id
  一致的松耦合设计（部门删除时不级联，由应用层处理引用）。

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """equipment 新增 visible_departments；industrial_object 新增 department_id + visible_departments。"""

    # ---- 1. equipment 新增 visible_departments 列 ----
    # JSONB 数组，存可见实验室的 UUID 字符串列表；NOT NULL，默认空数组。
    op.add_column(
        "equipment",
        sa.Column(
            "visible_departments",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # ---- 2. equipment GIN 索引加速 visible_departments @> 包含查询 ----
    op.execute(
        "CREATE INDEX ix_equipment_visible_departments "
        "ON equipment USING GIN (visible_departments)"
    )

    # ---- 3. industrial_object 新增 department_id 列 ----
    # UUID，nullable，无 FK（松耦合，与 app_user.department_id 设计一致）。
    op.add_column(
        "industrial_object",
        sa.Column("department_id", sa.UUID, nullable=True),
    )

    # ---- 4. industrial_object 新增 visible_departments 列 ----
    # JSONB 数组，NOT NULL，默认空数组。
    op.add_column(
        "industrial_object",
        sa.Column(
            "visible_departments",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # ---- 5. industrial_object 索引 ----
    # B-tree 索引加速 department_id == 精确过滤。
    op.create_index(
        "ix_industrial_object_department_id",
        "industrial_object",
        ["department_id"],
    )
    # GIN 索引加速 visible_departments @> 包含查询。
    op.execute(
        "CREATE INDEX ix_industrial_object_visible_departments "
        "ON industrial_object USING GIN (visible_departments)"
    )


def downgrade() -> None:
    """回滚：删除索引 + 列（industrial_object 先于 equipment，互不依赖）。"""
    # ---- industrial_object ----
    op.execute(
        "DROP INDEX IF EXISTS ix_industrial_object_visible_departments"
    )
    op.drop_index(
        "ix_industrial_object_department_id", table_name="industrial_object"
    )
    op.drop_column("industrial_object", "visible_departments")
    op.drop_column("industrial_object", "department_id")

    # ---- equipment ----
    op.execute("DROP INDEX IF EXISTS ix_equipment_visible_departments")
    op.drop_column("equipment", "visible_departments")
