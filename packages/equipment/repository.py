"""设备仪器数据仓库：Equipment 的数据库操作。

所有方法接受 AsyncSession 参数，由调用方（EquipmentService）管理事务边界。
查询使用乐观锁（lock_version）和条件 UPDATE 保证并发安全。

关键操作：
- EquipmentRepository:
  - insert: INSERT equipment；
  - select_by_id: SELECT equipment by id；
  - select_by_org_and_code: SELECT equipment by (organization_id, code) — 编码唯一性校验；
  - select_list: 分页列表 + department_name JOIN；
  - update: UPDATE with lock_version（乐观锁，不含 code 列）；
  - update_status: UPDATE status with lock_version（乐观锁）。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.equipment.entities import Equipment


async def _get_descendant_dept_ids(session: AsyncSession, dept_id: UUID) -> list[UUID]:
    """递归查询某部门及其所有后代部门的 ID 列表。

    使用 PostgreSQL WITH RECURSIVE 递归 CTE 遍历 parent_id 层级。
    """
    stmt = sa.text(
        """
        WITH RECURSIVE dept_tree AS (
            SELECT id FROM department WHERE id = CAST(:root_id AS uuid)
            UNION ALL
            SELECT d.id FROM department d
            INNER JOIN dept_tree dt ON d.parent_id = dt.id
        )
        SELECT id FROM dept_tree
        """
    )
    result = await session.execute(stmt.bindparams(root_id=str(dept_id)))
    rows = result.fetchall()
    return [UUID(str(row[0])) for row in rows]


class EquipmentRepository:
    """设备仪器持久化仓库。

    所有方法为纯数据访问，不含业务逻辑——业务编排由 EquipmentService 负责。
    """

    @staticmethod
    async def insert(session: AsyncSession, equipment: Equipment) -> Equipment:
        """INSERT 设备记录。"""
        session.add(equipment)
        await session.flush()
        return equipment

    @staticmethod
    async def select_by_id(
        session: AsyncSession,
        equipment_id: UUID,
        organization_id: UUID,
    ) -> Equipment | None:
        """按 ID 查询设备（含租户隔离条件）。"""
        result = await session.execute(
            sa.select(Equipment).where(
                Equipment.id == equipment_id,
                Equipment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_by_org_and_code(
        session: AsyncSession,
        organization_id: UUID,
        code: str,
    ) -> Equipment | None:
        """按组织 ID 和编码查询设备（编码唯一性校验）。"""
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
        visible_dept_id: UUID | None = None,
        status: str | None = None,
        cursor_sort_order: int | None = None,
        cursor_created_at: datetime | None = None,
        cursor_id: UUID | None = None,
        limit: int = 20,
    ) -> list[tuple[Equipment, str]]:
        """分页查询设备列表（含部门名）。

        排序：sort_order ASC, created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (sort_order, created_at, id)。

        Args:
            session: 异步会话。
            organization_id: 组织 ID。
            department_id: 部门 ID 筛选（含后代部门），None 表示不按部门过滤。
            visible_dept_id: 可见性部门 ID，用于 OR visible_departments @> [dept_id] 过滤。
                当 department_id 和 visible_dept_id 同时存在时，取两者的 OR。
            status: 状态筛选。
            cursor_sort_order: 游标排序权重。
            cursor_created_at: 游标创建时间。
            cursor_id: 游标 ID。
            limit: 查询上限。
        """
        # 延迟导入避免 equipment ↔ departments 循环依赖（F-20）
        from packages.departments.entities import Department

        query = (
            sa.select(Equipment, Department.display_name)
            .select_from(Equipment)
            .outerjoin(
                Department,
                Department.id == Equipment.department_id,
            )
            .where(Equipment.organization_id == organization_id)
            .order_by(
                Equipment.sort_order.asc(),
                Equipment.created_at.asc(),
                Equipment.id.asc(),
            )
            .limit(limit)
        )

        # 部门过滤 + 可见性过滤
        # 可见性规则：department_id（含后代） OR visible_departments 包含 visible_dept_id
        if department_id is not None and visible_dept_id is not None:
            dept_ids = await _get_descendant_dept_ids(session, department_id)
            dept_condition = (
                Equipment.department_id.in_(dept_ids)
                if dept_ids
                else Equipment.department_id == department_id
            )
            visible_condition = Equipment.visible_departments.contains([str(visible_dept_id)])
            query = query.where(sa.or_(dept_condition, visible_condition))
        elif department_id is not None:
            dept_ids = await _get_descendant_dept_ids(session, department_id)
            if dept_ids:
                query = query.where(Equipment.department_id.in_(dept_ids))
            else:
                query = query.where(Equipment.department_id == department_id)
        elif visible_dept_id is not None:
            query = query.where(Equipment.visible_departments.contains([str(visible_dept_id)]))

        if status is not None:
            query = query.where(Equipment.status == status)

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
        return [(row[0], row[1] if row[1] is not None else "") for row in rows]

    @staticmethod
    async def update(
        session: AsyncSession,
        equipment_id: UUID,
        display_name: str,
        description: str | None,
        department_id: UUID,
        sort_order: int,
        lock_version: int,
        organization_id: UUID,
        visible_departments: list[str] | None = None,
    ) -> Equipment | None:
        """UPDATE 设备（乐观锁，不含 code 列，含租户隔离）。"""
        values: dict[str, object] = {
            "display_name": display_name,
            "description": description,
            "department_id": department_id,
            "sort_order": sort_order,
            "updated_at": sa.func.now(),
            "lock_version": Equipment.lock_version + 1,
        }
        if visible_departments is not None:
            values["visible_departments"] = visible_departments
        result = await session.execute(
            sa.update(Equipment)
            .values(**values)
            .where(
                Equipment.id == equipment_id,
                Equipment.organization_id == organization_id,
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
        organization_id: UUID,
    ) -> Equipment | None:
        """UPDATE 设备状态（乐观锁，软禁用/启用，含租户隔离）。"""
        result = await session.execute(
            sa.update(Equipment)
            .values(
                status=status,
                updated_at=sa.func.now(),
                lock_version=Equipment.lock_version + 1,
            )
            .where(
                Equipment.id == equipment_id,
                Equipment.organization_id == organization_id,
                Equipment.lock_version == lock_version,
            )
            .returning(Equipment)
        )
        return result.scalar_one_or_none()
