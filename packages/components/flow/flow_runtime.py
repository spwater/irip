"""IRIP 流程运行时：ORM 实体 + 定义管理 + 执行服务。

提供：
- FlowDefinition: 流程定义主表 ORM（组织内按 code 唯一）；
- FlowDefinitionVersionORM: 流程版本表 ORM（按 definition+version 唯一，
  已发布不可变）；
- FlowRun: 流程执行记录 ORM（关联 job + 输入快照 + 输出摘要）；
- FlowNodeExecution: 节点执行记录 ORM（逐节点状态 + 输入/输出摘要）；
- FlowRuntimeService: 流程定义管理 + 执行编排服务。

设计要点（IRIP V2-T03）：
- 流程定义创建后处于 draft 状态，发布版本后变为 published；
- 已发布版本不可变（无更新端点），digest 校验完整性；
- 执行采用拓扑排序逐节点执行，记录每个节点的执行状态；
- 支持 resume（跳过已成功节点）、cancel（协作式取消）、retry_node
  （重试单个失败节点）；
- cancel 通过 asyncio.Event 实现协作式取消，执行循环在节点间检查。

向后兼容：``flow_runtime.py`` 重新导出所有从子模块移出的符号
（``FlowDefinition``, ``FlowDefinitionVersionORM``, ``FlowRun``,
``FlowNodeExecution``, ``PROTECTED_PARAMS``），外部
``from packages.components.flow.flow_runtime import X`` 不受影响。
"""

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import ScopedSessionMixin
from packages.components.flow.constants import PROTECTED_PARAMS  # noqa: F401
from packages.components.flow.dag import resolve_input, topological_sort
from packages.components.flow.definition_service import FlowDefinitionService  # noqa: F401
from packages.components.flow.entities import (  # noqa: F401
    FlowDefinition,
    FlowDefinitionVersionORM,
    FlowNodeExecution,
    FlowRun,
)
from packages.components.flow.execution_engine import FlowExecutionEngine  # noqa: F401
from packages.components.flow.flows import (
    FlowEdge,
    FlowNode,
)
from packages.components.flow.manifest_utils import build_manifest_from_version
from packages.components.flow.run_service import FlowRunService  # noqa: F401
from packages.components.flow.serialization import (
    compute_output_digest,
    serialize_input_summary,
    serialize_output_summary,
)
from packages.components.registry import (
    ComponentRegistryService,
)
from packages.components.sdk import ComponentRunner
from packages.departments.entities import (
    Department,  # noqa: F401 — ensure FK target registered in metadata
)

# ---- 向后兼容别名（原内部函数移出后的旧名称映射） ----
_topological_sort = topological_sort
_resolve_input = resolve_input
_compute_output_digest = compute_output_digest
_serialize_output_summary = serialize_output_summary
_serialize_input_summary = serialize_input_summary
_build_manifest_from_version = build_manifest_from_version


# ---- 运行时服务 ----


class FlowRuntimeService(ScopedSessionMixin):
    """流程运行时服务：定义管理 + 执行编排。

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    registry（组件注册表）、runner（组件运行器）、job_service（作业服务）。

    核心操作：
    - 定义管理：create_definition, publish_version, list_definitions,
      get_definition, get_definition_by_id；
    - 执行管理：create_run, execute, resume, cancel, retry_node, get_run。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _registry: 组件注册表服务。
        _runner: 组件运行器。
        _job_service: 作业服务（创建异步作业）。
        _clock: 时钟实例。
        _artifact_service: 工件服务（可选，用于组件上传/下载）。
        _cancel_events: 运行 ID → 取消事件映射（内存中维护）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID,
        registry: ComponentRegistryService,
        runner: ComponentRunner,
        job_service: Any,
        clock: Clock | None = None,
        artifact_service: Any = None,
        ai_config_provider: Any = None,
    ) -> None:
        """初始化流程运行时服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID（用于流程定义所有者 owner_user_id）。
            registry: 组件注册表服务。
            runner: 组件运行器（PythonComponentRunner 或 CLIComponentRunner）。
            job_service: 作业服务（创建异步作业触发执行）。
            clock: 时钟（可选，默认 SystemClock）。
            artifact_service: 工件服务（可选，注入到 ComponentContext）。
            ai_config_provider: AI 配置异步提供函数（可选，注入到 ComponentContext，
                消除 packages→apps 反向依赖 T3-3）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._registry = registry
        self._runner = runner
        self._job_service = job_service
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._artifact_service = artifact_service
        self._ai_config_provider: Any = ai_config_provider
        self._cancel_events: dict[UUID, asyncio.Event] = {}

        # 创建子服务
        self._definition_svc = FlowDefinitionService(
            session_factory=session_factory,
            department_id=department_id,
            actor_id=actor_id,
            registry=registry,
            clock=self._clock,
        )
        self._run_svc = FlowRunService(
            session_factory=session_factory,
            department_id=department_id,
            actor_id=actor_id,
            job_service=job_service,
            clock=self._clock,
            definition_svc=self._definition_svc,
        )
        self._execution_engine = FlowExecutionEngine(
            session_factory=session_factory,
            department_id=department_id,
            actor_id=actor_id,
            registry=registry,
            runner=runner,
            clock=self._clock,
            artifact_service=artifact_service,
            ai_config_provider=ai_config_provider,
            cancel_events=self._cancel_events,
        )

    # ---- 公开只读属性（替代路由直接访问私有属性） ----

    @property
    def department_id(self) -> UUID:
        """当前部门 ID（公开只读访问）。"""
        return self._dept_id

    @property
    def actor_id(self) -> UUID:
        """当前操作者用户 ID（公开只读访问）。"""
        return self._actor_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂（公开只读访问，替代 ``service._factory``）。"""
        return self._factory

    # ---- 定义管理（委托 FlowDefinitionService）----

    async def create_definition(
        self,
        code: str,
        display_name: str,
        nodes: tuple[FlowNode, ...] = (),
        edges: tuple[FlowEdge, ...] = (),
        department_id: UUID | None = None,
        project_id: UUID | None = None,
        operator: str | None = None,
        experimental_object_code: str | None = None,
    ) -> FlowDefinition:
        """创建流程定义（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.create_definition(
            code,
            display_name,
            nodes,
            edges,
            department_id,
            project_id,
            operator,
            experimental_object_code,
        )

    async def publish_version(
        self,
        flow_definition_id: UUID,
        nodes: tuple[FlowNode, ...],
        edges: tuple[FlowEdge, ...],
        random_seed: int = 0,
    ) -> FlowDefinitionVersionORM:
        """发布流程版本（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.publish_version(
            flow_definition_id,
            nodes,
            edges,
            random_seed,
        )

    async def list_definitions(
        self,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[tuple[FlowDefinition, FlowDefinitionVersionORM | None]]:
        """列表查询流程定义（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.list_definitions(status, project_id)

    async def get_definition(
        self, flow_id: UUID
    ) -> tuple[FlowDefinition, FlowDefinitionVersionORM | None]:
        """获取流程定义详情（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.get_definition(flow_id)

    async def deprecate_definition(self, flow_id: UUID) -> FlowDefinition:
        """归档流程定义（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.deprecate_definition(flow_id)

    async def restore_definition(self, flow_id: UUID) -> FlowDefinition:
        """恢复流程定义（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.restore_definition(flow_id)

    async def get_definition_by_id(
        self, version_id: UUID
    ) -> tuple[FlowDefinition, FlowDefinitionVersionORM]:
        """按版本 ID 获取流程定义 + 版本（委托到 FlowDefinitionService）。"""
        return await self._definition_svc.get_definition_by_id(version_id)

    async def delete_flow(self, flow_id: UUID) -> None:
        """删除流程定义及其所有版本和运行记录（委托到 FlowDefinitionService）。"""
        await self._definition_svc.delete_flow(flow_id)

    # ---- 执行管理（list_runs/create_run/get_run/delete_run 委托 FlowRunService）----

    async def list_runs(self, flow_id: UUID) -> list[FlowRun]:
        """列出流程的所有运行记录（委托到 FlowRunService）。"""
        return await self._run_svc.list_runs(flow_id)

    async def create_run(
        self,
        flow_version_id: UUID,
        inputs: dict[str, Any] | None = None,
    ) -> FlowRun:
        """创建流程执行记录（委托到 FlowRunService）。"""
        return await self._run_svc.create_run(flow_version_id, inputs)

    async def get_run(self, run_id: UUID) -> tuple[FlowRun, list[FlowNodeExecution]]:
        """获取执行记录详情（委托到 FlowRunService）。"""
        return await self._run_svc.get_run(run_id)

    async def delete_run(self, run_id: UUID) -> None:
        """删除执行记录（委托到 FlowRunService）。"""
        await self._run_svc.delete_run(run_id)

    # ---- 执行编排（委托 FlowExecutionEngine）----

    async def execute(self, run_id: UUID) -> None:
        """执行流程（委托到 FlowExecutionEngine）。"""
        await self._execution_engine.execute(run_id)

    async def resume(self, run_id: UUID) -> None:
        """恢复执行（委托到 FlowExecutionEngine）。"""
        await self._execution_engine.resume(run_id)

    async def cancel(self, run_id: UUID) -> FlowRun:
        """取消流程执行（委托到 FlowExecutionEngine）。"""
        return await self._execution_engine.cancel(run_id)

    async def retry_node(self, run_id: UUID, node_id: str) -> FlowNodeExecution:
        """重试单个失败节点（委托到 FlowExecutionEngine）。"""
        return await self._execution_engine.retry_node(run_id, node_id)

    # ---- 内部辅助方法（委托到 FlowExecutionEngine，保持 _ 前缀向后兼容）----

    async def _execute_single_node(
        self,
        run_id: UUID,
        node: FlowNode,
        inputs: dict[str, Any],
        cancel_event: asyncio.Event,
        job_id: UUID | None,
    ) -> dict[str, Any]:
        """执行单个节点（委托到 FlowExecutionEngine，向后兼容）。"""
        return await self._execution_engine._execute_single_node(
            run_id, node, inputs, cancel_event, job_id
        )

    async def _finalize_run(
        self,
        run_id: UUID,
        status: str,
        version_digest: str,
        node_exec_summaries: list[dict[str, Any]],
    ) -> None:
        """更新 FlowRun 最终状态（委托到 FlowExecutionEngine，向后兼容）。"""
        await self._execution_engine._finalize_run(
            run_id, status, version_digest, node_exec_summaries
        )

    async def _update_job_status(self, job_id: UUID | None, status: str) -> None:
        """更新关联作业状态（委托到 FlowExecutionEngine，向后兼容）。"""
        await self._execution_engine._update_job_status(job_id, status)
