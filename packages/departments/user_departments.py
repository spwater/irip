"""用户-实验室关联服务（P1）。

提供用户与实验室的多对多关联管理：
- set_user_departments: 批量设置用户所属实验室（增删 + is_primary 唯一性）；
- get_user_departments: 查询用户所属实验室列表；
- get_department_users: 查询实验室下用户列表。

is_primary 唯一性由应用层保证（同一 user 最多一条 is_primary=true），无 DB 级唯一索引。
"""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.auth.entities import AppUser
from packages.common.database import session_scope
from packages.departments.entities import AppUserDepartment, Department


@dataclass(frozen=True)
class UserDepartmentItem:
    """用户-实验室关联项。

    Attributes:
        user_id: 用户 UUID。
        department_id: 实验室 UUID。
        department_code: 实验室编码。
        department_display_name: 实验室显示名。
        is_primary: 是否主要实验室。
    """

    user_id: UUID
    department_id: UUID
    department_code: str
    department_display_name: str
    is_primary: bool


@dataclass(frozen=True)
class DepartmentUserItem:
    """实验室下用户项。

    Attributes:
        user_id: 用户 UUID。
        email: 用户邮箱。
        display_name: 用户显示名。
        is_primary: 是否主要实验室。
    """

    user_id: UUID
    email: str
    display_name: str
    is_primary: bool


class UserDepartmentService:
    """用户-实验室关联管理服务（P1）。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
    ) -> None:
        """初始化用户-实验室关联服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
        """
        self._factory = session_factory
        self._org_id = organization_id

    async def set_user_departments(
        self,
        user_id: UUID,
        department_ids: list[UUID],
        primary_department_id: UUID | None,
    ) -> None:
        """批量设置用户所属实验室。

        事务内执行：
        1. DELETE FROM app_user_department WHERE user_id=? AND department_id NOT IN (...)；
        2. INSERT ... ON CONFLICT DO NOTHING（添加新关联）；
        3. UPDATE app_user_department SET is_primary = (department_id = ?) WHERE user_id=?。

        is_primary 唯一性：步骤 3 保证仅指定实验室为 primary。
        若 primary_department_id 不在 department_ids 中，则全部设为 false。

        Args:
            user_id: 用户 UUID。
            department_ids: 实验室 ID 列表（全量替换）。
            primary_department_id: 主要实验室 ID（None = 无主要实验室）。
        """
        async with session_scope(self._factory) as session:
            # 1. 移除不在新列表中的关联
            if department_ids:
                await session.execute(
                    sa.delete(AppUserDepartment).where(
                        AppUserDepartment.user_id == user_id,
                        AppUserDepartment.department_id.notin_(department_ids),
                    )
                )
            else:
                await session.execute(
                    sa.delete(AppUserDepartment).where(
                        AppUserDepartment.user_id == user_id,
                    )
                )

            # 2. 添加新关联（ON CONFLICT DO NOTHING）
            for dept_id in department_ids:
                await session.execute(
                    pg_insert(AppUserDepartment)
                    .values(
                        user_id=user_id,
                        department_id=dept_id,
                        is_primary=False,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "department_id"])
                )

            # 3. 设置 is_primary（仅指定实验室为 primary，其余为 false）
            if primary_department_id is not None:
                await session.execute(
                    sa.update(AppUserDepartment)
                    .values(is_primary=AppUserDepartment.department_id == primary_department_id)
                    .where(AppUserDepartment.user_id == user_id)
                )
            else:
                await session.execute(
                    sa.update(AppUserDepartment)
                    .values(is_primary=False)
                    .where(AppUserDepartment.user_id == user_id)
                )

    async def get_user_departments(self, user_id: UUID) -> list[UserDepartmentItem]:
        """查询用户所属实验室列表。

        JOIN department 获取实验室编码和显示名。

        Args:
            user_id: 用户 UUID。

        Returns:
            list[UserDepartmentItem]: 用户-实验室关联列表。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(
                    AppUserDepartment.user_id,
                    AppUserDepartment.department_id,
                    Department.code,
                    Department.display_name,
                    AppUserDepartment.is_primary,
                )
                .select_from(AppUserDepartment)
                .join(
                    Department,
                    AppUserDepartment.department_id == Department.id,
                )
                .where(AppUserDepartment.user_id == user_id)
                .order_by(AppUserDepartment.is_primary.desc())
            )
            rows = result.all()

        return [
            UserDepartmentItem(
                user_id=row[0],
                department_id=row[1],
                department_code=row[2],
                department_display_name=row[3],
                is_primary=row[4],
            )
            for row in rows
        ]

    async def get_department_users(self, department_id: UUID) -> list[DepartmentUserItem]:
        """查询实验室下用户列表。

        JOIN app_user 获取用户邮箱和显示名。

        Args:
            department_id: 实验室 UUID。

        Returns:
            list[DepartmentUserItem]: 实验室下用户列表。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(
                    AppUser.id,
                    AppUser.email,
                    AppUser.display_name,
                    AppUserDepartment.is_primary,
                )
                .select_from(AppUserDepartment)
                .join(AppUser, AppUserDepartment.user_id == AppUser.id)
                .where(AppUserDepartment.department_id == department_id)
                .order_by(AppUserDepartment.is_primary.desc())
            )
            rows = result.all()

        return [
            DepartmentUserItem(
                user_id=row[0],
                email=row[1],
                display_name=row[2],
                is_primary=row[3],
            )
            for row in rows
        ]
