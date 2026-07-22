"""设备仪器数据仓库：Equipment + EquipmentVariable 的数据库操作。

所有方法接受 AsyncSession 参数，由调用方（EquipmentService）管理事务边界。
查询使用乐观锁（lock_version）和条件 UPDATE 保证并发安全。

关键操作：
- EquipmentRepository:
  - insert: INSERT equipment；
  - select_by_id: SELECT equipment by id；
  - select_by_org_and_code: SELECT equipment by (organization_id, code) — 编码唯一性校验；
  - select_list: 分页列表 + department_name JOIN + variable_count 聚合；
  - update: UPDATE with lock_version（乐观锁，不含 code 列）；
  - update_status: UPDATE status with lock_version（乐观锁）。
- EquipmentVariableRepository:
  - list_by_equipment: SELECT equipment_variable WHERE equipment_id=?；
  - set_variables: 全量替换（DELETE + INSERT）；
  - count_by_equipment: COUNT WHERE equipment_id=?。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.departments.entities import Department
from packages.equipment.entities import Equipment, EquipmentVariable


class EquipmentRepository:
    """设备仪器持久化仓库。

    所有方法为纯数据访问，不含业务逻辑——业务编排由 EquipmentService 负责。
    """

    @staticmethod
    async def insert(session: AsyncSession, equipment: Equipment) -> Equipment:
        """INSERT 设备记录。

        Args:
            session: 异步会话（事务由调用方管理）。
            equipment: 待插入的 Equipment 实体。

        Returns:
            Equipment: 插入后的实体（含数据库生成的默认值）。
        """
        session.add(equipment)
        await session.flush()
        return equipment

    @staticmethod
    async def select_by_id(
        session: AsyncSession, equipment_id: UUID
    ) -> Equipment | None:
        """按 ID 查询设备。

        Args:
            session: 异步会话。
            equipment_id: 设备 UUID。

        Returns:
            Equipment | None: 设备实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(Equipment).where(Equipment.id == equipment_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_by_org_and_code(
        session: AsyncSession,
        organization_id: UUID,
        code: str,
    ) -> Equipment | None:
        """按组织 ID 和编码查询设备（编码唯一性校验）。

        Args:
            session: 异步会话。
            organization_id: 组织 ID。
            code: 设备编码。

        Returns:
            Equipment | None: 设备实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(Equipment).where(
                Equipment.organization_id == organization_id,
                Equipment.code == code,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_list(
        session: AsyncSession,
        organization_id: UUID,
        department_id: UUID | None = None,
        status: str | None = None,
        cursor_sort_order: int | None = None,
        cursor_created_at: datetime | None = None,
        cursor_id: UUID | None = None,
        limit: int = 20,
    ) -> list[tuple[Equipment, str, int]]:
        """分页查询设备列表（含部门名 + 物理量数聚合）。

        通过 LEFT JOIN department 获取部门显示名，
        LEFT JOIN equipment_variable + GROUP BY + COUNT 获取物理量数，
        避免多次查询。

        排序：sort_order ASC, created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (sort_order, created_at, id)。

        Args:
            session: 异步会话。
            organization_id: 组织 ID（过滤条件）。
            department_id: 部门 ID 筛选（None = 不过滤）。
            status: 状态筛选（None = 不过滤，"active" / "disabled"）。
            cursor_sort_order: 游标 sort_order（None = 第一页）。
            cursor_created_at: 游标 created_at（None = 第一页）。
            cursor_id: 游标 id（None = 第一页）。
            limit: 每页数量。

        Returns:
            list[tuple[Equipment, str, int]]: (Equipment, department_name, variable_count) 列表。
        """
        variable_count = sa.func.count(EquipmentVariable.variable_id).label(
            "variable_count"
        )

        query = (
            sa.select(Equipment, Department.display_name, variable_count)
            .select_from(Equipment)
            .outerjoin(
                Department,
                Department.id == Equipment.department_id,
            )
            .outerjoin(
                EquipmentVariable,
                EquipmentVariable.equipment_id == Equipment.id,
            )
            .where(Equipment.organization_id == organization_id)
            .group_by(Equipment.id, Department.display_name)
            .order_by(
                Equipment.sort_order.asc(),
                Equipment.created_at.asc(),
                Equipment.id.asc(),
            )
            .limit(limit)
        )

        if department_id is not None:
            query = query.where(Equipment.department_id == department_id)

        if status is not None:
            query = query.where(Equipment.status == status)

        # Keyset 分页条件
        if (
            cursor_sort_order is not None
            and cursor_created_at is not None
            and cursor_id is not None
        ):
            query = query.where(
                sa.or_(
                    Equipment.sort_order > cursor_sort_order,
                    sa.and_(
                        Equipment.sort_order == cursor_sort_order,
                        Equipment.created_at > cursor_created_at,
                    ),
                    sa.and_(
                        Equipment.sort_order == cursor_sort_order,
                        Equipment.created_at == cursor_created_at,
                        Equipment.id > cursor_id,
                    ),
                )
            )

        result = await session.execute(query)
        rows = result.all()
        return [
            (row[0], row[1] if row[1] is not None else "", int(row[2]))
            for row in rows
        ]

    @staticmethod
    async def update(
        session: AsyncSession,
        equipment_id: UUID,
        display_name: str,
        description: str | None,
        department_id: UUID,
        sort_order: int,
        lock_version: int,
    ) -> Equipment | None:
        """UPDATE 设备（乐观锁，不含 code 列）。

        UPDATE equipment SET display_name=?, description=?, department_id=?,
        sort_order=?, updated_at=now(), lock_version=lock_version+1
        WHERE id=? AND lock_version=?

        Args:
            session: 异步会话。
            equipment_id: 设备 UUID。
            display_name: 新显示名。
            description: 新描述。
            department_id: 新部门 ID。
            sort_order: 新排序权重。
            lock_version: 客户端持有的乐观锁版本号。

        Returns:
            Equipment | None: 更新后的实体；None 表示 lock_version 不匹配或不存在。
        """
        result = await session.execute(
            sa.update(Equipment)
            .values(
                display_name=display_name,
                description=description,
                department_id=department_id,
                sort_order=sort_order,
                updated_at=sa.func.now(),
                lock_version=Equipment.lock_version + 1,
            )
            .where(
                Equipment.id == equipment_id,
                Equipment.lock_version == lock_version,
            )
            .returning(Equipment)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_status(
        session: AsyncSession,
        equipment_id: UUID,
        status: str,
        lock_version: int,
    ) -> Equipment | None:
        """UPDATE 设备状态（乐观锁，软禁用/启用）。

        UPDATE equipment SET status=?, updated_at=now(), lock_version=lock_version+1
        WHERE id=? AND lock_version=?

        Args:
            session: 异步会话。
            equipment_id: 设备 UUID。
            status: 新状态（"active" / "disabled"）。
            lock_version: 客户端持有的乐观锁版本号。

        Returns:
            Equipment | None: 更新后的实体；None 表示 lock_version 不匹配或不存在。
        """
        result = await session.execute(
            sa.update(Equipment)
            .values(
                status=status,
                updated_at=sa.func.now(),
                lock_version=Equipment.lock_version + 1,
            )
            .where(
                Equipment.id == equipment_id,
                Equipment.lock_version == lock_version,
            )
            .returning(Equipment)
        )
        return result.scalar_one_or_none()


class EquipmentVariableRepository:
    """设备-物理量关联持久化仓库。"""

    @staticmethod
    async def list_by_equipment(
        session: AsyncSession, equipment_id: UUID
    ) -> list[EquipmentVariable]:
        """查询设备的所有物理量关联。

        Args:
            session: 异步会话。
            equipment_id: 设备 UUID。

        Returns:
            list[EquipmentVariable]: 关联记录列表。
        """
        result = await session.execute(
            sa.select(EquipmentVariable).where(
                EquipmentVariable.equipment_id == equipment_id
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def set_variables(
        session: AsyncSession,
        equipment_id: UUID,
        variable_ids: list[UUID],
    ) -> None:
        """全量替换设备的物理量关联（DELETE + INSERT）。

        先删除该设备的所有关联，再插入新的关联列表。

        Args:
            session: 异步会话。
            equipment_id: 设备 UUID。
            variable_ids: 物理量 ID 列表（全量替换）。
        """
        # 删除旧关联
        await session.execute(
            sa.delete(EquipmentVariable).where(
                EquipmentVariable.equipment_id == equipment_id
            )
        )

        # 插入新关联
        for vid in variable_ids:
            session.add(
                EquipmentVariable(
                    equipment_id=equipment_id,
                    variable_id=vid,
                )
            )
        await session.flush()

    @staticmethod
    async def count_by_equipment(
        session: AsyncSession, equipment_id: UUID
    ) -> int:
        """统计设备的物理量关联数。

        Args:
            session: 异步会话。
            equipment_id: 设备 UUID。

        Returns:
            int: 关联数。
        """
        result = await session.execute(
            sa.select(sa.func.count()).where(
                EquipmentVariable.equipment_id == equipment_id
            )
        )
        count = result.scalar()
        return int(count) if count is not None else 0
