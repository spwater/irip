"""流程执行结果入库为事实的 DB 操作服务。

封装 ``persist_run_as_fact`` 端点中的全部数据库查询/写入：
- Artifact 文件名解析与存在性校验
- 任务信息快照（多表 JOIN）
- FactDataIndex 批量写入

依赖注入：
- 继承 ScopedSessionMixin，通过 ``_scoped_session()`` 获取带 GUC 的会话；
- 需要实例属性 ``_factory``, ``_dept_id``, ``_actor_id``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.artifacts import Artifact
from packages.common.database import ScopedSessionMixin
from packages.common.ids import new_id
from packages.components.flow.entities import (
    FlowDefinition,
    FlowDefinitionVersionORM,
)
from packages.departments.entities import Department
from packages.equipment.entities import Equipment
from packages.facts.entities import FactDataIndex

_logger = logging.getLogger(__name__)


@dataclass
class TaskSnapshot:
    """任务信息快照（入库时保存，避免后续反查 JOIN）。

    所有字段在查询失败时为 None，保证异常容忍行为。

    Attributes:
        task_code: 流程编码（FlowDefinition.code）。
        task_name: 流程显示名（FlowDefinition.display_name）。
        department_name: 部门显示名（Department.display_name）。
        operator: 流程执行人（FlowDefinition.operator）。
        run_operator: 实际运行执行人（input_snapshot._operator）。
        equipment_name: 设备显示名（Equipment.display_name）。
    """

    task_code: str | None = None
    task_name: str | None = None
    department_name: str | None = None
    operator: str | None = None
    run_operator: str | None = None
    equipment_name: str | None = None


class FlowFactService(ScopedSessionMixin):
    """流程执行结果入库为事实的 DB 操作服务。

    封装 persist_run_as_fact 端点中的全部数据库查询/写入：
    - Artifact 文件名解析与存在性校验
    - 任务信息快照（多表 JOIN）
    - FactDataIndex 批量写入

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作者用户 ID（可选，Artifact 查询不需要 user GUC）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化流程事实入库服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID（可选，默认 None）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id

    async def resolve_artifact_filename(self, artifact_id: UUID) -> str | None:
        """根据 artifact_id 查询文件名。

        Args:
            artifact_id: 工件 UUID。

        Returns:
            str | None: 文件名，不存在时返回 None。
        """
        try:
            async with self._scoped_session() as session:
                art: Artifact | None = await session.scalar(
                    sa.select(Artifact).where(Artifact.id == artifact_id)
                )
                if art and art.filename:
                    return art.filename
                return None
        except Exception:
            return None

    async def check_artifact_exists(self, artifact_id: UUID) -> bool:
        """校验 artifact 是否仍存在。

        Args:
            artifact_id: 工件 UUID。

        Returns:
            bool: 存在返回 True，否则 False。
        """
        try:
            async with self._scoped_session() as session:
                result = await session.scalar(
                    sa.select(Artifact.id).where(Artifact.id == artifact_id)
                )
                return result is not None
        except Exception:
            return False

    async def get_task_snapshot(
        self,
        flow_version_id: UUID,
        input_snapshot: dict[str, Any],
    ) -> TaskSnapshot:
        """查询任务信息快照（task_code/task_name/department_name/operator/equipment_name）。

        通过 FlowDefinitionVersionORM → FlowDefinition JOIN 查询，
        从 nodes_json 获取 component_name 后关联 Equipment 表查设备名，
        关联 Department 表查部门名。
        异常时返回空 TaskSnapshot（不阻塞入库流程）。

        Args:
            flow_version_id: 流程版本 ID。
            input_snapshot: 流程输入快照（从中提取 _operator 作为 run_operator）。

        Returns:
            TaskSnapshot: 任务信息快照，查询失败时所有字段为 None。
        """
        task_code: str | None = None
        task_name: str | None = None
        department_name: str | None = None
        operator: str | None = None
        run_operator: str | None = None
        equipment_name: str | None = None

        try:
            async with self._scoped_session() as session:
                # 查流程版本
                fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == flow_version_id
                )
                fv: FlowDefinitionVersionORM | None = (
                    await session.execute(fv_stmt)
                ).scalar_one_or_none()

                if fv:
                    # 查流程定义
                    fd_stmt = sa.select(FlowDefinition).where(
                        FlowDefinition.id == fv.flow_definition_id
                    )
                    fd: FlowDefinition | None = (
                        await session.execute(fd_stmt)
                    ).scalar_one_or_none()

                    if fd:
                        task_code = fd.code
                        task_name = fd.display_name
                        operator = fd.operator
                        run_operator = (input_snapshot or {}).get("_operator")

                        # 从 nodes_json 获取 component_name，查 equipment_name
                        nodes = fv.nodes_json or []
                        if isinstance(nodes, list) and len(nodes) > 0:
                            comp_name = (
                                (nodes[0] or {}).get("component_name")
                                if isinstance(nodes[0], dict)
                                else None
                            )
                            if comp_name:
                                from packages.components.registry import (
                                    Component as _C,
                                )
                                from packages.components.registry import (
                                    ComponentVersion as _CV,
                                )

                                eq_stmt = (
                                    sa.select(Equipment.display_name)
                                    .select_from(_C)
                                    .join(
                                        _CV,
                                        _CV.component_id == _C.id,
                                    )
                                    .outerjoin(
                                        Equipment,
                                        _CV.equipment_id == sa.cast(Equipment.id, sa.Text),
                                    )
                                    .where(_C.name == comp_name)
                                    .where(_CV.equipment_id.isnot(None))
                                    .order_by(_CV.version.desc())
                                    .limit(1)
                                )
                                eq_row = (await session.execute(eq_stmt)).first()
                                if eq_row:
                                    equipment_name = eq_row[0]

                        # 查部门名
                        if fd.department_id:
                            dept_stmt = sa.select(Department).where(
                                Department.id == fd.department_id
                            )
                            dept_record: Department | None = (
                                await session.execute(dept_stmt)
                            ).scalar_one_or_none()
                            if dept_record:
                                department_name = dept_record.display_name
        except Exception as exc:
            _logger.warning("fact ingest snapshot failed: %s", exc)

        return TaskSnapshot(
            task_code=task_code,
            task_name=task_name,
            department_name=department_name,
            operator=operator,
            run_operator=run_operator,
            equipment_name=equipment_name,
        )

    async def write_fact_data_index(
        self,
        fact_id: UUID,
        points: list[dict[str, Any]],
    ) -> None:
        """将 points 展平写入 FactDataIndex 通用数据索引表。

        将每个 point 的 name/value 展平为 key-value 对：
        - 数值存 value_number + value_text；
        - 其他类型存 value_text；
        - None 值跳过。

        异常时仅 warning 日志，不向上抛出（异常容忍行为）。

        Args:
            fact_id: 关联的事实 ID。
            points: 数据点列表，每个元素为 dict（含 name, value 键）。
        """
        try:
            index_rows: list[dict[str, Any]] = []
            for row_idx, point in enumerate(points):
                if not isinstance(point, dict):
                    continue
                key = point.get("name", f"item_{row_idx}")
                value = point.get("value")
                # 数值存 value_number，其他存 value_text
                val_num: float | None = None
                val_text: str | None = None
                if isinstance(value, (int, float)):
                    val_num = float(value)
                    val_text = str(value)
                elif value is not None:
                    val_text = str(value)
                else:
                    continue
                index_rows.append(
                    {
                        "id": new_id(),
                        "fact_id": fact_id,
                        "row_index": row_idx,
                        "key": str(key),
                        "value_text": val_text,
                        "value_number": val_num,
                    }
                )

            if index_rows:
                async with self._scoped_session() as session:
                    await session.execute(
                        sa.insert(FactDataIndex),
                        index_rows,
                    )
        except Exception as exc:
            _logger.warning("Failed to write data index: %s", exc)
