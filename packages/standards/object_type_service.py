"""实验对象类型字典管理服务。

从 ``apps/api/routers/object_types.py`` 提取的 ORM 查询逻辑。
职责：列出全部类型、创建类型（含重名检查 + sort_order 计算）、
更新类型、删除类型（含引用检查）。

依赖注入：
- 继承 ScopedSessionMixin，通过 ``_scoped_session()`` 获取带 GUC 的会话；
- 需要实例属性 ``_factory``, ``_dept_id``, ``_actor_id``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.common.ids import gen_code
from packages.standards.objects.object_type_dict import ObjectTypeDict
from packages.standards.objects.objects import IndustrialObject


class ObjectTypeService(ScopedSessionMixin):
    """实验对象类型字典管理服务。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作者用户 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化实验对象类型服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID（可选，用于 RLS GUC）。
            actor_id: 当前操作者用户 ID（可选，用于 RLS GUC）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id

    async def list_object_types(self) -> list[ObjectTypeDict]:
        """列出全部实验对象类型（按 sort_order 升序）。

        Returns:
            list[ObjectTypeDict]: 全部类型列表，按 sort_order 升序排列。
        """
        async with self._scoped_session() as session:
            result = await session.execute(
                sa.select(ObjectTypeDict).order_by(ObjectTypeDict.sort_order.asc())
            )
            return list(result.scalars().all())

    async def create_object_type(
        self,
        display_name: str,
        description: str | None = None,
    ) -> ObjectTypeDict:
        """创建实验对象类型（含重名检查 + sort_order 计算）。

        Args:
            display_name: 中文显示名（不可重复）。
            description: 描述（可选）。

        Returns:
            ObjectTypeDict: 新创建的类型对象。

        Raises:
            AppError: code="conflict"，当类型名称已存在时。
        """
        code = gen_code("obtype")
        async with self._scoped_session() as session:
            existing = await session.execute(
                sa.select(ObjectTypeDict).where(ObjectTypeDict.display_name == display_name)
            )
            if existing.scalar_one_or_none() is not None:
                raise AppError(code="conflict", message="类型名称已存在", retryable=False)
            max_order = await session.execute(sa.select(sa.func.max(ObjectTypeDict.sort_order)))
            sort_order = (max_order.scalar() or 0) + 1
            obj = ObjectTypeDict(
                code=code,
                display_name=display_name,
                description=description,
                sort_order=sort_order,
            )
            session.add(obj)
            await session.flush()
            return obj

    async def update_object_type(
        self,
        type_id: UUID,
        display_name: str | None = None,
        description: str | None = None,
    ) -> ObjectTypeDict:
        """更新实验对象类型。

        Args:
            type_id: 目标类型 UUID。
            display_name: 新显示名（None 表示不修改）。
            description: 新描述（None 表示不修改）。

        Returns:
            ObjectTypeDict: 更新后的类型对象。

        Raises:
            AppError: code="not_found"，当类型不存在时。
        """
        async with self._scoped_session() as session:
            result = await session.execute(
                sa.select(ObjectTypeDict).where(ObjectTypeDict.id == type_id)
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                raise AppError(code="not_found", message="类型不存在", retryable=False)
            if display_name is not None:
                obj.display_name = display_name
            if description is not None:
                obj.description = description
            obj.updated_at = datetime.now(UTC)
            await session.flush()
            return obj

    async def delete_object_type(self, type_id: UUID) -> None:
        """删除实验对象类型（含引用检查）。

        如果有工业对象正在使用该类型，则拒绝删除。

        Args:
            type_id: 目标类型 UUID。

        Raises:
            AppError: code="not_found"，当类型不存在时。
            AppError: code="conflict"，当类型正在被使用时。
        """
        async with self._scoped_session() as session:
            result = await session.execute(
                sa.select(ObjectTypeDict).where(ObjectTypeDict.id == type_id)
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                raise AppError(code="not_found", message="类型不存在", retryable=False)
            count_result = await session.execute(
                sa.select(sa.func.count())
                .select_from(IndustrialObject)
                .where(IndustrialObject.object_type == obj.code)
            )
            obj_count = int(count_result.scalar() or 0)
            if obj_count > 0:
                raise AppError(
                    code="conflict",
                    message=f"该类型正在使用中（{obj_count} 个实验对象），无法删除",
                    retryable=False,
                )
            await session.delete(obj)
