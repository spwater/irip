"""实验室级数据隔离辅助函数。

阶段2 多租户隔离键升级：从硬编码角色判断改为 root 部门成员判断。

提供：
- should_filter_by_department: 判断当前用户是否需要按实验室过滤数据（root 成员不过滤）；
- get_department_filter: 获取当前用户的 department_id 用于过滤；
- get_visible_department_ids: 获取用户可见的全部实验室 ID（调用 DB 函数）；
- can_edit_department: 判断用户是否可以编辑指定实验室的设备/对象；
- can_reparent_department: 判断用户是否可以调整部门的父子关系（哨兵保护前置检查）。

隔离规则（阶段2）：
- root 部门成员（department.code == 'root'）：不受限制，看全部数据；
- 其他部门成员：看自己部门及子树的数据（通过 current_visible_dept_ids() DB 函数）。

注意：should_filter_by_department 保持同步签名（向后兼容现有路由调用），
通过 CurrentUser.is_root_member 字段判断（在 get_current_user 时填充）。
"""

import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser

logger = logging.getLogger(__name__)


def should_filter_by_department(user: CurrentUser) -> bool:
    """判断当前用户是否需要按实验室过滤数据。

    阶段2：不再硬编码 platform_administrator/platform_auditor 角色判断，
    改为通过 CurrentUser.is_root_member 字段判断（在 get_current_user 时填充）。
    root 部门成员不受数据隔离限制。

    保持同步签名，向后兼容现有路由调用。

    Args:
        user: 当前认证用户（需含 is_root_member 字段）。

    Returns:
        bool: True 表示需要按实验室过滤，False 表示不受限制。
    """
    is_root = getattr(user, "is_root_member", False)
    if is_root:
        return False
    # 过渡期：platform_administrator/platform_auditor 也不过滤
    if "platform_administrator" in user.roles or "platform_auditor" in user.roles:
        return False
    return True


def get_department_filter(user: CurrentUser) -> UUID | None:
    """获取当前用户的 department_id 用于过滤。

    返回 None 有两种情况，调用方需通过 should_filter_by_department 区分：
    1. root 成员 → None 表示不过滤（看全部数据）；
    2. 非root 用户且 department_id 为 NULL → None 表示无实验室（看不到任何数据）。

    Args:
        user: 当前认证用户。

    Returns:
        UUID | None: department_id 用于过滤，None 表示不过滤或无实验室。
    """
    if not should_filter_by_department(user):
        return None  # root 成员：不过滤
    return user.department_id  # 非root：用户的 department_id


async def get_visible_department_ids(
    user: CurrentUser,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[UUID]:
    """获取用户可见的全部实验室 ID（含上下级）。

    阶段2：调用 DB 函数 current_visible_dept_ids()（已含上下对称递归）。
    该函数读取 GUC app.current_dept_id，需在 session_scope 中调用或手动设置 GUC。
    此处使用独立查询（带 dept_id 参数），不依赖 GUC。

    Args:
        user: 当前认证用户。
        session_factory: 数据库会话工厂。

    Returns:
        list[UUID]: 可见实验室 ID 列表。root 成员返回空列表（表示不过滤）。
    """
    if not should_filter_by_department(user):
        return []  # root 成员不过滤
    if user.department_id is None:
        return []  # 无实验室用户

    # 调用 DB 函数 current_visible_dept_ids()
    # 该函数读取 GUC app.current_user_id，需通过 set_user_guc 安全设置
    from packages.common.tenant_guc import set_user_guc

    async with session_factory() as session:
        await set_user_guc(session, user.user_id)
        result = await session.execute(
            sa.text("SELECT id FROM current_visible_dept_ids()")
        )
        return [
            row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
            for row in result.fetchall()
        ]


def can_edit_department(user: CurrentUser, target_dept_id: UUID | None) -> bool:
    """判断用户是否可以编辑指定实验室的设备/对象（同步快查，不含后代）。

    Args:
        user: 当前认证用户。
        target_dept_id: 设备/对象的所属实验室 ID。

    Returns:
        bool: True 表示可以编辑。
    """
    if not should_filter_by_department(user):
        return True  # root 成员
    if target_dept_id is None:
        return True  # 无所属单位
    if user.department_id is None:
        return False
    # 精确匹配（后代判断在路由层做）
    return user.department_id == target_dept_id


async def can_reparent_department(
    dept_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """判断是否可以调整指定部门的父子关系（哨兵保护前置检查）。

    root / system 哨兵部门不允许被 re-parent。

    Args:
        dept_id: 要调整的部门 ID。
        session_factory: 数据库会话工厂。

    Returns:
        bool: True 表示可以调整，False 表示受哨兵保护。
    """
    from packages.departments.entities import Department

    async with session_factory() as session:
        result = await session.execute(
            sa.select(Department.code).where(Department.id == dept_id)
        )
        row = result.first()
        if row is None:
            return True  # 部门不存在，允许（后续会报 not_found）
        code: str = row[0]
        return code not in ("root", "system")


async def check_is_root_member(
    department_id: UUID | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """查询 department_id 是否对应 root 哨兵部门。

    在 get_current_user 中调用，结果缓存到 CurrentUser.is_root_member。

    Args:
        department_id: 用户的主要部门 ID。
        session_factory: 数据库会话工厂。

    Returns:
        bool: True 表示用户属于 root 部门。
    """
    if department_id is None:
        return False

    from packages.departments.entities import Department

    async with session_factory() as session:
        result = await session.execute(
            sa.select(Department.code).where(Department.id == department_id)
        )
        row = result.first()
        return row is not None and row[0] == "root"
