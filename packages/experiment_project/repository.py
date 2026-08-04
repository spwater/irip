"""实验项目数据仓库：ExperimentProject 的数据库操作。

所有方法接受 AsyncSession 参数，由调用方（ExperimentProjectService）管理事务边界。
查询使用乐观锁（lock_version）和条件 UPDATE 保证并发安全。

关键操作：
- ExperimentProjectRepository:
  - insert: INSERT experiment_project；
  - select_by_id: SELECT by id；
  - select_by_dept_and_code: SELECT by (department_id, code) — 编码唯一性校验；
  - select_list: 分页列表 + department_name JOIN（含可见部门过滤）；
  - count_flows_by_project: 统计项目下任务数；
  - update: UPDATE with lock_version（乐观锁，不含 code 列）；
  - update_status: UPDATE status with lock_version（乐观锁）。

风格参考 packages/equipment/repository.py。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.experiment_project.entities import ExperimentProject


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


class ExperimentProjectRepository:
    """实验项目持久化仓库。

    所有方法为纯数据访问，不含业务逻辑——业务编排由 ExperimentProjectService 负责。
    """

    @staticmethod
    async def insert(
        session: AsyncSession,
        project: ExperimentProject,
    ) -> ExperimentProject:
        """INSERT 实验项目记录。"""
        session.add(project)
        await session.flush()
        return project

    @staticmethod
    async def select_by_id(
        session: AsyncSession,
        project_id: UUID,
    ) -> ExperimentProject | None:
        """按 ID 查询项目（RLS 处理租户隔离）。"""
        result = await session.execute(
            sa.select(ExperimentProject).where(
                ExperimentProject.id == project_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_by_dept_and_code(
        session: AsyncSession,
        department_id: UUID,
        code: str,
    ) -> ExperimentProject | None:
        """按部门 ID 和编码查询项目（编码唯一性校验）。"""
        result = await session.execute(
            sa.select(ExperimentProject).where(
                ExperimentProject.department_id == department_id,
                ExperimentProject.code == code,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def select_list(
        session: AsyncSession,
        department_id: UUID | None = None,
        visible_dept_id: UUID | None = None,
        status: str | None = None,
        cursor_created_at: datetime | None = None,
        cursor_id: UUID | None = None,
        limit: int = 20,
    ) -> list[tuple[ExperimentProject, str]]:
        """分页查询项目列表（含部门名）。

        排序：created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (created_at_iso, id)。

        Args:
            session: 异步会话。
            department_id: 部门 ID 筛选（含后代部门）。
            visible_dept_id: 可见性部门 ID，用于 OR visible_departments 过滤。
            status: 状态筛选。
            cursor_created_at: 游标创建时间。
            cursor_id: 游标 ID。
            limit: 查询上限。
        """
        # 延迟导入避免 experiment_project ↔ departments 循环依赖
        from packages.departments.entities import Department

        query = (
            sa.select(ExperimentProject, Department.display_name)
            .select_from(ExperimentProject)
            .outerjoin(
                Department,
                Department.id == ExperimentProject.department_id,
            )
            .order_by(
                ExperimentProject.created_at.asc(),
                ExperimentProject.id.asc(),
            )
            .limit(limit)
        )

        # 部门过滤 + 可见性过滤
        if department_id is not None and visible_dept_id is not None:
            dept_ids = await _get_descendant_dept_ids(session, department_id)
            dept_condition = (
                ExperimentProject.department_id.in_(dept_ids)
                if dept_ids
                else ExperimentProject.department_id == department_id
            )
            visible_condition = ExperimentProject.visible_departments.contains(
                [str(visible_dept_id)]
            )
            query = query.where(sa.or_(dept_condition, visible_condition))
        elif department_id is not None:
            dept_ids = await _get_descendant_dept_ids(session, department_id)
            if dept_ids:
                query = query.where(ExperimentProject.department_id.in_(dept_ids))
            else:
                query = query.where(ExperimentProject.department_id == department_id)
        elif visible_dept_id is not None:
            query = query.where(
                ExperimentProject.visible_departments.contains([str(visible_dept_id)])
            )

        if status is not None:
            query = query.where(ExperimentProject.status == status)

        if cursor_created_at is not None and cursor_id is not None:
            query = query.where(
                sa.or_(
                    ExperimentProject.created_at > cursor_created_at,
                    sa.and_(
                        ExperimentProject.created_at == cursor_created_at,
                        ExperimentProject.id > cursor_id,
                    ),
                )
            )

        result = await session.execute(query)
        rows = result.all()
        return [(row[0], row[1] if row[1] is not None else "") for row in rows]

    @staticmethod
    async def count_flows_by_project(
        session: AsyncSession,
        project_id: UUID,
    ) -> int:
        """统计项目下任务数（flow_definition.project_id 匹配）。

        Args:
            session: 异步会话。
            project_id: 项目 ID。

        Returns:
            int: 任务数量。
        """
        from packages.components.flow.flow_runtime import FlowDefinition

        count: int | None = await session.scalar(
            sa.select(sa.func.count(FlowDefinition.id)).where(
                FlowDefinition.project_id == project_id
            )
        )
        return count or 0

    @staticmethod
    async def count_facts_by_project(
        session: AsyncSession,
        project_id: UUID,
    ) -> int:
        """统计项目下的数据数（fact 通过 flow_run → flow_definition_version → flow_definition 关联到 project）。

        Args:
            session: 异步会话。
            project_id: 项目 ID。

        Returns:
            int: 数据数量。
        """
        from packages.components.flow.flow_runtime import (
            FlowDefinition,
            FlowDefinitionVersionORM,
            FlowRun,
        )
        from packages.facts.entities import Fact

        count: int | None = await session.scalar(
            sa.select(sa.func.count(Fact.id))
            .select_from(Fact)
            .join(FlowRun, Fact.flow_run_id == FlowRun.id)
            .join(
                FlowDefinitionVersionORM,
                FlowRun.flow_version_id == FlowDefinitionVersionORM.id,
            )
            .join(
                FlowDefinition,
                FlowDefinitionVersionORM.flow_definition_id == FlowDefinition.id,
            )
            .where(FlowDefinition.project_id == project_id)
        )
        return count or 0

    @staticmethod
    async def update(
        session: AsyncSession,
        project_id: UUID,
        display_name: str,
        description: str | None,
        lock_version: int,
        visible_departments: list[str] | None = None,
        owner_user_id: UUID | None = None,
    ) -> ExperimentProject | None:
        """UPDATE 项目（乐观锁，不含 code 列）。"""
        values: dict[str, object] = {
            "display_name": display_name,
            "description": description,
            "updated_at": sa.func.now(),
            "lock_version": ExperimentProject.lock_version + 1,
        }
        if visible_departments is not None:
            values["visible_departments"] = visible_departments
        if owner_user_id is not None:
            values["owner_user_id"] = owner_user_id
        result = await session.execute(
            sa.update(ExperimentProject)
            .values(**values)
            .where(
                ExperimentProject.id == project_id,
                ExperimentProject.lock_version == lock_version,
            )
            .returning(ExperimentProject)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_status(
        session: AsyncSession,
        project_id: UUID,
        status: str,
        lock_version: int,
    ) -> ExperimentProject | None:
        """UPDATE 项目状态（乐观锁，归档/恢复，RLS 处理租户隔离）。"""
        result = await session.execute(
            sa.update(ExperimentProject)
            .values(
                status=status,
                updated_at=sa.func.now(),
                lock_version=ExperimentProject.lock_version + 1,
            )
            .where(
                ExperimentProject.id == project_id,
                ExperimentProject.lock_version == lock_version,
            )
            .returning(ExperimentProject)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def delete(session: AsyncSession, project_id: UUID) -> None:
        """删除项目（物理删除）。"""
        await session.execute(
            sa.delete(ExperimentProject).where(ExperimentProject.id == project_id)
        )
