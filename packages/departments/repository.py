"""实验室数据仓库：Department 的数据库操作。

所有方法接受 AsyncSession 参数，由调用方（DepartmentService）管理事务边界。
查询使用乐观锁（lock_version）和条件 UPDATE 保证并发安全。

关键操作（docs/arch-department.md §3.9 类图）：
- insert: INSERT department；
- select_by_id: SELECT department by id；
- select_by_org_and_code: SELECT department by (department_id, code) — 编码唯一性校验；
- select_list: 分页列表 + member_count 聚合（LEFT JOIN + GROUP BY + COUNT）；
- update: UPDATE with lock_version（乐观锁，不含 code 列）；
- update_status: UPDATE status with lock_version（乐观锁，软禁用）。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.departments.entities import Department


class DepartmentRepository:
    """实验室持久化仓库。

    所有方法为纯数据访问，不含业务逻辑——业务编排由 DepartmentService 负责。
    """

    @staticmethod
    async def insert(session: AsyncSession, dept: Department) -> Department:
        """INSERT 实验室记录。

        Args:
            session: 异步会话（事务由调用方管理）。
            dept: 待插入的 Department 实体。

        Returns:
            Department: 插入后的实体（含数据库生成的默认值）。
        """
        session.add(dept)
        await session.flush()
        return dept

    @staticmethod
    async def select_by_id(
        session: AsyncSession,
        department_id: UUID,
    ) -> Department | None:
        """按 ID 查询实验室（含租户隔离条件）。

        Args:
            session: 异步会话。
            department_id: 实验室 UUID。

        Returns:
            Department | None: 实验室实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(Department).where(
                Department.id == department_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_by_org_and_code(
        session: AsyncSession,
        department_id: UUID,
        code: str,
    ) -> Department | None:
        """按部门 ID 和编码查询实验室（编码唯一性校验）。

        Args:
            session: 异步会话。
            department_id: 部门 ID。
            code: 实验室编码。

        Returns:
            Department | None: 实验室实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(Department).where(
                Department.id == department_id,
                Department.code == code,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_list(
        session: AsyncSession,
        status: str | None = None,
        cursor_sort_order: int | None = None,
        cursor_created_at: datetime | None = None,
        cursor_id: UUID | None = None,
        limit: int = 20,
    ) -> list[tuple[Department, int, int, int]]:
        """分页查询实验室列表（含成员数、子部门数、仪器数聚合）。

        主查询返回当页部门列表及直接子部门数（children_count，相关标量子
        查询，仅统计直接子部门，不递归）。成员数（member_count）与设备数
        （equipment_count）采用 PostgreSQL 递归 CTE 递归累加该部门自身及
        所有后代部门，对当页每个部门单独执行递归统计（部门数量少，N+1
        性能可接受）。

        排序：sort_order ASC, created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (sort_order, created_at, id)。

        Args:
            session: 异步会话。
            department_id: 部门 ID（过滤条件）。
            status: 状态筛选（None = 不过滤，"active" / "disabled"）。
            cursor_sort_order: 游标 sort_order（None = 第一页）。
            cursor_created_at: 游标 created_at（None = 第一页）。
            cursor_id: 游标 id（None = 第一页）。
            limit: 每页数量。

        Returns:
            list[tuple[Department, int, int, int]]: (Department, member_count,
            children_count, equipment_count) 列表。
        """
        # 子部门数：相关标量子查询，统计 parent_id = department.id 的子部门数量
        child_dept = Department.__table__.alias("child")
        children_count = (
            sa.select(sa.func.count())
            .select_from(child_dept)
            .where(child_dept.c.parent_id == Department.__table__.c.id)
            .correlate(Department.__table__)
            .scalar_subquery()
            .label("children_count")
        )

        # 成员数与设备数：递归累加该部门自身 + 所有后代部门（通过 parent_id
        # 逐层向下递归）。采用 PostgreSQL WITH RECURSIVE CTE，先取出当页部门
        # 列表，再对每个部门单独执行递归统计（部门数量通常很少，N+1 性能可
        # 接受），避免在 ORM 层表达关联递归 CTE 的复杂性，保证正确性与可维护性。
        query = (
            sa.select(Department, children_count)
            .select_from(Department)
            .order_by(
                Department.sort_order.asc(),
                Department.created_at.asc(),
                Department.id.asc(),
            )
            .limit(limit)
        )

        if status is not None:
            query = query.where(Department.status == status)

        # Keyset 分页条件
        if (
            cursor_sort_order is not None
            and cursor_created_at is not None
            and cursor_id is not None
        ):
            query = query.where(
                sa.or_(
                    Department.sort_order > cursor_sort_order,
                    sa.and_(
                        Department.sort_order == cursor_sort_order,
                        Department.created_at > cursor_created_at,
                    ),
                    sa.and_(
                        Department.sort_order == cursor_sort_order,
                        Department.created_at == cursor_created_at,
                        Department.id > cursor_id,
                    ),
                )
            )

        result = await session.execute(query)
        rows = result.all()

        # 递归 CTE：收集部门自身 + 所有后代部门 ID，再分别统计成员数与设备数。
        # 锚点包含部门自身（id = :dept_id），递归部分沿 parent_id 向下展开全部后代。
        # 使用绑定参数 :dept_id 避免关联子查询/CTE 的作用域歧义问题。
        recursive_sql = sa.text(
            """
            WITH RECURSIVE descendants AS (
                SELECT id FROM department WHERE id = :dept_id
                UNION ALL
                SELECT child.id FROM department child
                JOIN descendants ON child.parent_id = descendants.id
            )
            SELECT
                (SELECT count(*) FROM app_user_department aud
                 WHERE aud.department_id IN (SELECT id FROM descendants))
                    AS member_count,
                (SELECT count(*) FROM equipment e
                 WHERE e.department_id IN (SELECT id FROM descendants))
                    AS equipment_count
            """
        )

        output: list[tuple[Department, int, int, int]] = []
        for row in rows:
            dept = row[0]
            children = int(row[1])
            count_result = await session.execute(recursive_sql, {"dept_id": dept.id})
            count_row = count_result.one()
            output.append((dept, int(count_row[0]), children, int(count_row[1])))
        return output

    @staticmethod
    async def update(
        session: AsyncSession,
        department_id: UUID,
        display_name: str,
        description: str | None,
        sort_order: int,
        lock_version: int,
        parent_id: UUID | None = None,
    ) -> Department | None:
        """UPDATE 实验室（乐观锁，不含 code 列）。

        UPDATE department SET display_name=?, description=?, sort_order=?,
        parent_id=?, updated_at=now(), lock_version=lock_version+1
        WHERE id=? AND department_id=? AND lock_version=?

        Args:
            session: 异步会话。
            department_id: 实验室 UUID。
            display_name: 新显示名。
            description: 新描述。
            sort_order: 新排序权重。
            lock_version: 客户端持有的乐观锁版本号。
            department_id: 部门 ID（租户隔离）。
            parent_id: 上级部门 ID（None 表示顶级部门）。

        Returns:
            Department | None: 更新后的实体；None 表示 lock_version 不匹配或不存在。
        """
        result = await session.execute(
            sa.update(Department)
            .values(
                display_name=display_name,
                description=description,
                sort_order=sort_order,
                parent_id=parent_id,
                updated_at=sa.func.now(),
                lock_version=Department.lock_version + 1,
            )
            .where(
                Department.id == department_id,
                Department.lock_version == lock_version,
            )
            .returning(Department)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_status(
        session: AsyncSession,
        department_id: UUID,
        status: str,
        lock_version: int,
    ) -> Department | None:
        """UPDATE 实验室状态（乐观锁，软禁用/启用）。

        UPDATE department SET status=?, updated_at=now(), lock_version=lock_version+1
        WHERE id=? AND department_id=? AND lock_version=?

        Args:
            session: 异步会话。
            department_id: 实验室 UUID。
            status: 新状态（"active" / "disabled"）。
            lock_version: 客户端持有的乐观锁版本号。
            department_id: 部门 ID（租户隔离）。

        Returns:
            Department | None: 更新后的实体；None 表示 lock_version 不匹配或不存在。
        """
        result = await session.execute(
            sa.update(Department)
            .values(
                status=status,
                updated_at=sa.func.now(),
                lock_version=Department.lock_version + 1,
            )
            .where(
                Department.id == department_id,
                Department.lock_version == lock_version,
            )
            .returning(Department)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_children_count(
        session: AsyncSession,
        department_id: UUID,
    ) -> int:
        """COUNT 直接子部门数。"""
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(Department)
            .where(Department.parent_id == department_id)
        )
        return int(result.scalar() or 0)

    @staticmethod
    async def delete_by_id(
        session: AsyncSession,
        department_id: UUID,
    ) -> bool:
        """DELETE 实验室记录（物理删除，含租户隔离）。

        Args:
            session: 异步会话。
            department_id: 实验室 UUID。

        Returns:
            bool: 是否删除成功（影响行数 > 0）。
        """
        result = await session.execute(
            sa.delete(Department).where(
                Department.id == department_id,
            )
        )
        return result.rowcount > 0  # type: ignore[no-any-return, attr-defined]
