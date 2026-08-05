"""流程运行记录管理服务。

从 ``flow_runtime.py`` 提取的运行记录 CRUD 逻辑。
职责：列出运行记录、创建运行记录（关联作业）、获取运行详情、删除运行记录。

依赖注入：
- 继承 ScopedSessionMixin，通过 ``_scoped_session()`` 获取带 GUC 的会话；
- 需要实例属性 ``_factory``, ``_dept_id``, ``_actor_id``, ``_job_service``, ``_clock``；
- ``_definition_service`` 用于 ``get_definition_by_id`` 验证版本存在。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.components.flow.entities import (
    FlowDefinitionVersionORM,
    FlowNodeExecution,
    FlowRun,
)
from packages.facts.entities import Fact


class FlowRunService(ScopedSessionMixin):
    """流程运行记录管理服务。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作者用户 ID。
        _job_service: 作业服务（创建异步作业触发执行）。
        _clock: 时钟实例。
        _definition_svc: 流程定义服务（用于版本验证）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID,
        job_service: Any,
        clock: Clock,
        definition_svc: Any,
    ) -> None:
        """初始化运行记录服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID。
            job_service: 作业服务。
            clock: 时钟实例。
            definition_svc: 流程定义服务（用于 get_definition_by_id）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._job_service = job_service
        self._clock = clock
        self._definition_svc = definition_svc

    async def list_runs(self, flow_id: UUID) -> list[FlowRun]:
        """列出流程的所有运行记录（按创建时间降序）。

        Args:
            flow_id: 流程定义 ID。

        Returns:
            list[FlowRun]: 运行记录列表。
        """
        async with self._scoped_session() as session:
            result = await session.execute(
                sa.select(FlowRun)
                .where(
                    FlowRun.flow_version_id.in_(
                        sa.select(FlowDefinitionVersionORM.id).where(
                            FlowDefinitionVersionORM.flow_definition_id == flow_id
                        )
                    )
                )
                .order_by(FlowRun.created_at.desc())
            )
            return list(result.scalars().all())

    async def create_run(
        self,
        flow_version_id: UUID,
        inputs: dict[str, Any] | None = None,
    ) -> FlowRun:
        """创建流程执行记录（关联作业）。

        流程：
        1. 验证流程版本存在；
        2. 通过 job_service 创建异步作业；
        3. 创建 FlowRun（status=pending, input_snapshot=inputs）。

        Args:
            flow_version_id: 流程版本 ID。
            inputs: 流程输入（存储为 input_snapshot）。

        Returns:
            FlowRun: 新创建的执行记录（status=pending）。

        Raises:
            AppError: code="not_found"，当版本不存在。
        """
        # 验证版本存在并获取流程定义（用于 department_id）
        flow_def, _version = await self._definition_svc.get_definition_by_id(flow_version_id)

        run_id: UUID = new_id()
        input_snapshot: dict[str, Any] = inputs or {}

        # 创建作业（department_id 用流程定义的归属部门，而非执行者部门）
        job_ref: Any = await self._job_service.accept(
            kind="flow_execute",
            payload={
                "run_id": str(run_id),
                "flow_version_id": str(flow_version_id),
                "department_id": str(flow_def.department_id),
            },
            idempotency_key=f"flow-run-{run_id}",
        )

        async with self._scoped_session() as session:
            run = FlowRun(
                id=run_id,
                department_id=flow_def.department_id,
                flow_version_id=flow_version_id,
                status="pending",
                job_id=job_ref.job_id,
                input_snapshot=input_snapshot,
            )
            session.add(run)
            await session.flush()

            # F-04 §8.5：不再直接 send_task，统一走 Outbox→Dispatcher→Celery 链路
            # job_service.accept() 已在同事务中 INSERT outbox_event，
            # OutboxDispatcher 会定期拉取并通过 celery_app.send_task 发送。

            return run

    async def get_run(self, run_id: UUID) -> tuple[FlowRun, list[FlowNodeExecution]]:
        """获取执行记录详情（含节点执行状态）。

        Args:
            run_id: 执行记录 ID。

        Returns:
            tuple[FlowRun, list[FlowNodeExecution]]:
                执行记录 + 节点执行记录列表。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        async with self._scoped_session() as session:
            run: FlowRun | None = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.id == run_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"流程执行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            executions: list[FlowNodeExecution] = list(
                (
                    await session.execute(
                        sa.select(FlowNodeExecution)
                        .where(FlowNodeExecution.flow_run_id == run_id)
                        .order_by(FlowNodeExecution.started_at)
                    )
                )
                .scalars()
                .all()
            )
            return run, executions

    async def delete_run(self, run_id: UUID) -> None:
        """删除执行记录及其所有节点执行记录和关联的作业。

        Args:
            run_id: 执行记录 ID。
        """
        async with self._scoped_session() as session:
            # 先查出关联的 job_id
            run = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.id == run_id,
                )
            )
            job_id = run.job_id if run else None

            # 删除节点执行记录
            await session.execute(
                sa.delete(FlowNodeExecution).where(FlowNodeExecution.flow_run_id == run_id)
            )
            # 删除执行记录
            await session.execute(
                sa.delete(FlowRun).where(
                    FlowRun.id == run_id,
                )
            )
            # 删除关联的作业（避免残留 job 在看板显示空名称）
            if job_id is not None:
                from packages.jobs.entities import Job

                await session.execute(sa.delete(Job).where(Job.id == job_id))
            await session.flush()

    async def get_run_fact_ids(
        self,
        run_ids: list[UUID],
    ) -> dict[UUID, str]:
        """批量查询 run 已入库的 fact_id 映射。

        Args:
            run_ids: 运行记录 ID 列表。

        Returns:
            dict[UUID, str]: {run_id: fact_id_str} 映射（仅包含已入库的 run）。
        """
        if not run_ids:
            return {}
        async with self._scoped_session() as session:
            persist_stmt = sa.select(Fact.id, Fact.flow_run_id).where(Fact.flow_run_id.in_(run_ids))
            persist_result = await session.execute(persist_stmt)
            fact_id_map: dict[UUID, str] = {}
            for row in persist_result:
                fact_id_map[row[1]] = str(row[0])
            return fact_id_map

    async def get_latest_node_execution(
        self,
        run_id: UUID,
    ) -> FlowNodeExecution | None:
        """查询 run 的最新节点执行记录（按 completed_at 降序取第一条）。

        Args:
            run_id: 运行记录 ID。

        Returns:
            FlowNodeExecution | None: 最新节点执行记录，无记录时返回 None。
        """
        async with self._scoped_session() as session:
            node_stmt = (
                sa.select(FlowNodeExecution)
                .where(FlowNodeExecution.flow_run_id == run_id)
                .order_by(FlowNodeExecution.completed_at.desc())
                .limit(1)
            )
            node_result = await session.execute(node_stmt)
            return node_result.scalar_one_or_none()

    async def list_facts_by_flow(
        self,
        flow_id: UUID,
    ) -> list[Fact]:
        """查询某个流程定义产出的所有事实。

        通过 flow_definition → flow_definition_version → flow_run → fact
        四表 JOIN 反查。

        Args:
            flow_id: 流程定义 ID。

        Returns:
            list[Fact]: 事实列表（按 created_at 降序）。
        """
        async with self._scoped_session() as session:
            stmt = (
                sa.select(Fact)
                .join(FlowRun, Fact.flow_run_id == FlowRun.id)
                .join(
                    FlowDefinitionVersionORM,
                    FlowRun.flow_version_id == FlowDefinitionVersionORM.id,
                )
                .where(FlowDefinitionVersionORM.flow_definition_id == flow_id)
                .order_by(Fact.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
