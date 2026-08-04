"""部门可见性辅助函数。

提供 ``compute_visible_dept_ids``：在事务内计算当前操作上下文的可见部门 ID 集合
（自身 + 全部子孙 + 全部祖先），用于替换应用层查询中硬编码的
``Entity.department_id == self._dept_id`` 等值过滤，实现层级向下穿透可见性。

背景（多租户层级可见性模型）：
- 一个部门成员可见：本部门 + 全部后代部门（向下）+ 全部祖先部门（向上）；
- root 哨兵部门成员可见全部部门（root 是所有部门的祖先，向下递归即全集）；
- 该模型由 DB 函数 ``current_visible_dept_ids()`` 实现，已被 RLS 策略锚定。

实现说明：
- DB 函数 ``current_visible_dept_ids()`` 读取 GUC ``app.current_user_id``，通过
  ``app_user_department`` 查询用户所有挂载部门后做向下 ∪ 向上递归，与 RLS 策略同源；
- 当传入 ``actor_id``（操作者用户 ID）时，本函数设置该 GUC 并调用 DB 函数，结果与
  RLS 完全一致（含多部门并集）；
- 当无法获取 ``actor_id``（如纯部门上下文的服务）时，退化为按 ``dept_id`` 直接做
  向下 ∪ 向上递归（直接查 ``department`` 表，该表未启用 RLS，全员可读）；
- 无论哪种路径，结果始终并入 ``dept_id``，保证当前部门上下文必定可见
  （兼容 worker 以系统服务用户处理任意部门任务的场景），且结果永不为空。

安全约定：
- ``SET LOCAL`` 不支持参数绑定，复用 ``tenant_guc.set_user_guc`` 做 quote_literal 安全引用；
- 递归 SQL 使用参数绑定（``:dept_id``），仅传 UUID 字符串，无注入风险。
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.tenant_guc import set_dept_guc, set_user_guc

#: 按 dept_id 直接做向下 ∪ 向上递归的 SQL（部门表未启用 RLS，全员可读）。
#: 与 current_visible_dept_ids() 中 down/up CTE 逻辑一致，只是起点为单个 dept_id。
_DEPT_SCOPE_SQL = sa.text(
    """
    WITH RECURSIVE
    down AS (
        SELECT id FROM department WHERE id = :dept_id
        UNION ALL
        SELECT d.id FROM department d JOIN down s ON d.parent_id = s.id
    ),
    up AS (
        SELECT d.parent_id AS id FROM department d
        WHERE d.id = :dept_id AND d.parent_id IS NOT NULL
        UNION ALL
        SELECT d.parent_id FROM department d JOIN up ON d.id = up.id
        WHERE d.parent_id IS NOT NULL
    )
    SELECT id FROM down UNION SELECT id FROM up
    """
)


def _coerce_uuid(value: object) -> UUID:
    """将数据库返回的值统一转为 UUID。"""
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


async def compute_visible_dept_ids(
    session: AsyncSession,
    dept_id: UUID | None,
    actor_id: UUID | None = None,
) -> list[UUID]:
    """计算可见部门 ID 集合（自身 + 子孙 + 祖先），保证 ``dept_id`` 必在其中。

    优先使用 ``actor_id`` 走 DB 函数 ``current_visible_dept_ids()``（与 RLS 同源，
    含多部门并集）；无 ``actor_id`` 时退化为按 ``dept_id`` 递归。两种路径均并入
    ``dept_id``，结果永不为空（至少包含 ``dept_id``），可安全用于 ``.in_()`` 过滤。

    Args:
        session: 已开启事务（或 autobegin）的异步会话。
        dept_id: 当前部门上下文 ID（始终并入结果）。
        actor_id: 当前操作者用户 ID。提供时设置 ``app.current_user_id`` GUC 并调用
            DB 函数，结果与 RLS 一致；为 None 时走 dept_id 递归退路。

    Returns:
        list[UUID]: 可见部门 ID 列表（去重，至少含 ``dept_id``）。
    """
    visible: set[UUID] = set()
    if dept_id is not None:
        visible.add(dept_id)

    if actor_id is not None and dept_id is not None:
        # 两条路径取并集，兼容 worker 场景（actor=系统用户, dept=任务部门）：
        # 1. 按 actor_id 走 DB 函数（含多部门并集，与 RLS 同源）
        # 设置两个 GUC：user_id 供 current_visible_dept_ids() 递归，
        # dept_id 供 RLS 策略的 visible_departments @> [dept_id] 条件
        await set_dept_guc(session, dept_id)
        await set_user_guc(session, actor_id)
        result = await session.execute(sa.text("SELECT current_visible_dept_ids()"))
        for row in result.fetchall():
            visible.add(_coerce_uuid(row[0]))
        # 2. 按 dept_id 走递归 SQL（保证目标部门的向下+向上祖先链都在结果里）
        result2 = await session.execute(_DEPT_SCOPE_SQL, {"dept_id": str(dept_id)})
        for row in result2.fetchall():
            visible.add(_coerce_uuid(row[0]))
    elif actor_id is not None:
        # 只有 actor_id，无 dept_id：纯按用户可见集
        await set_user_guc(session, actor_id)
        result = await session.execute(sa.text("SELECT current_visible_dept_ids()"))
        for row in result.fetchall():
            visible.add(_coerce_uuid(row[0]))
    elif dept_id is not None:
        # 退路：按 dept_id 直接做向下 ∪ 向上递归
        # 仍设 dept GUC，使 RLS 策略的 visible_departments 条件可生效
        await set_dept_guc(session, dept_id)
        result = await session.execute(_DEPT_SCOPE_SQL, {"dept_id": str(dept_id)})
        for row in result.fetchall():
            visible.add(_coerce_uuid(row[0]))

    return list(visible)
