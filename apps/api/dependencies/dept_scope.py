"""实验室级数据隔离辅助函数。

提供：
- should_filter_by_department: 判断当前用户是否需要按实验室过滤数据；
- get_department_filter: 获取当前用户的 department_id 用于过滤；
- get_visible_department_ids: 获取用户可见的全部实验室 ID（含后代）；
- can_edit_department: 判断用户是否可以编辑指定实验室的设备/对象。

隔离规则（含层级继承）：
- platform_administrator 和 platform_auditor：不受限制，看全部数据；
- lab_director、lab_member、lab_viewer：看自己实验室 + 所有后代实验室的数据；
- 上级单位自动拥有下级单位的可见权限和编辑权限。
- 如果用户的 department_id 为 NULL，非管理员角色看不到任何数据。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser


def should_filter_by_department(user: CurrentUser) -> bool:
    """判断当前用户是否需要按实验室过滤数据。

    平台管理员（platform_administrator）和平台监督员（platform_auditor）
    不受限制，可查看全部数据。其他角色（lab_director、lab_member、lab_viewer）
    只能查看自己实验室及后代实验室的数据。

    Args:
        user: 当前认证用户。

    Returns:
        bool: True 表示需要按实验室过滤，False 表示不受限制。
    """
    if "platform_administrator" in user.roles or "platform_auditor" in user.roles:
        return False
    return True


def get_department_filter(user: CurrentUser) -> UUID | None:
    """获取当前用户的 department_id 用于过滤。

    返回 None 有两种情况，调用方需通过 should_filter_by_department 区分：
    1. 平台管理员/监督员 → None 表示不过滤（看全部数据）；
    2. 非管理员用户且 department_id 为 NULL → None 表示无实验室（看不到任何数据）。

    Args:
        user: 当前认证用户。

    Returns:
        UUID | None: department_id 用于过滤，None 表示不过滤或无实验室。
    """
    if not should_filter_by_department(user):
        return None  # 管理员/监督员：不过滤
    return user.department_id  # 非管理员：用户的 department_id（可能为 None）


async def get_visible_department_ids(
    user: CurrentUser,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[UUID]:
    """获取用户可见的全部实验室 ID（含后代实验室）。

    上级单位自动拥有下级单位的可见权限。例如用户属于"研发中心"，
    则能看到研发中心及其所有子实验室（热工、粉磨等）的设备/对象。

    Args:
        user: 当前认证用户。
        session_factory: 数据库会话工厂。

    Returns:
        list[UUID]: 可见实验室 ID 列表。管理员返回空列表（表示不过滤）。
    """
    if not should_filter_by_department(user):
        return []  # 管理员不过滤
    if user.department_id is None:
        return []  # 无实验室用户

    # 递归查询用户实验室及其所有后代
    async with session_factory() as session:
        result = await session.execute(
            sa.text(
                """
                WITH RECURSIVE descendants AS (
                    SELECT id FROM department WHERE id = :dept_id
                    UNION ALL
                    SELECT d.id FROM department d
                    INNER JOIN descendants dt ON d.parent_id = dt.id
                )
                SELECT id FROM descendants
                """
            ),
            {"dept_id": str(user.department_id)},
        )
        return [
            row[0] if isinstance(row[0], UUID) else UUID(str(row[0])) for row in result.fetchall()
        ]  # noqa: E501


def can_edit_department(user: CurrentUser, target_dept_id: UUID | None) -> bool:
    """判断用户是否可以编辑指定实验室的设备/对象（同步快查，不含后代）。

    上级单位自动拥有下级单位的编辑权限。但此函数只做精确匹配，
    后代判断在路由层通过 get_visible_department_ids 实现。

    Args:
        user: 当前认证用户。
        target_dept_id: 设备/对象的所属实验室 ID。

    Returns:
        bool: True 表示可以编辑。
    """
    if not should_filter_by_department(user):
        return True  # 管理员
    if target_dept_id is None:
        return True  # 无所属单位
    if user.department_id is None:
        return False
    # 精确匹配（后代判断在路由层做）
    return user.department_id == target_dept_id
