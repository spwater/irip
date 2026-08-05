"""流程执行引擎。

从 ``flow_runtime.py`` 提取的执行编排逻辑。
职责：执行流程、恢复执行、取消执行、重试单个节点、更新运行/作业状态。

依赖注入：
- 继承 ScopedSessionMixin，通过 ``_scoped_session()`` 获取带 GUC 的会话；
- 需要实例属性 ``_factory``, ``_dept_id``, ``_actor_id``, ``_registry``,
  ``_runner``, ``_clock``, ``_artifact_service``, ``_ai_config_provider``；
- ``_cancel_events`` 由 FlowRuntimeService 持有并共享引用。

关键设计：
- ``execute`` 和 ``resume`` 共享节点执行循环逻辑 ``_run_node_loop``，
  消除约 100 行重复代码；
- ``cancel`` 通过 ``_cancel_events`` 字典实现协作式取消。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.components.flow.constants import PROTECTED_PARAMS
from packages.components.flow.dag import resolve_input, topological_sort
from packages.components.flow.entities import (
    FlowDefinitionVersionORM,
    FlowNodeExecution,
    FlowRun,
)
from packages.components.flow.flows import (
    FlowEdge,
    FlowNode,
    edges_from_json,
    nodes_from_json,
)
from packages.components.flow.manifest_utils import build_manifest_from_version
from packages.components.flow.serialization import (
    compute_output_digest,
    serialize_input_summary,
    serialize_output_summary,
)
from packages.components.manifest import ComponentManifest
from packages.components.registry import (
    ComponentRegistryService,
    ComponentVersion,
)
from packages.components.sdk import ComponentContext, ComponentResult, ComponentRunner


class FlowExecutionEngine(ScopedSessionMixin):
    """流程执行引擎。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作者用户 ID。
        _registry: 组件注册表服务。
        _runner: 组件运行器。
        _clock: 时钟实例。
        _artifact_service: 工件服务（可选）。
        _ai_config_provider: AI 配置异步提供函数（可选）。
        _cancel_events: 运行 ID → 取消事件映射（与 FlowRuntimeService 共享引用）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID,
        registry: ComponentRegistryService,
        runner: ComponentRunner,
        clock: Clock,
        artifact_service: Any = None,
        ai_config_provider: Any = None,
        cancel_events: dict[UUID, asyncio.Event] | None = None,
    ) -> None:
        """初始化执行引擎。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID。
            registry: 组件注册表服务。
            runner: 组件运行器。
            clock: 时钟实例。
            artifact_service: 工件服务（可选）。
            ai_config_provider: AI 配置异步提供函数（可选）。
            cancel_events: 取消事件字典（与 FlowRuntimeService 共享引用）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._registry = registry
        self._runner = runner
        self._clock = clock
        self._artifact_service = artifact_service
        self._ai_config_provider: Any = ai_config_provider
        self._cancel_events: dict[UUID, asyncio.Event] = (
            cancel_events if cancel_events is not None else {}
        )

    async def execute(self, run_id: UUID) -> None:
        """执行流程：拓扑排序 → 逐节点执行 → 记录 → 计算摘要。

        流程：
        1. 加载 FlowRun 及其 FlowDefinitionVersionORM；
        2. 解析节点和边，拓扑排序；
        3. 创建 cancel_event；
        4. 更新 FlowRun 状态为 running；
        5. 逐节点执行（获取 manifest → 绑定输入 → 运行 → 记录）；
        6. 节点间检查 cancel_event；
        7. 计算输出摘要；
        8. 更新 FlowRun 最终状态。

        Args:
            run_id: 执行记录 ID。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        # 1. 加载执行记录与版本
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

            version: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == run.flow_version_id
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {run.flow_version_id}",
                    retryable=False,
                    fields={"flow_version_id": str(run.flow_version_id)},
                )

            nodes: tuple[FlowNode, ...] = nodes_from_json(version.nodes_json)
            edges: tuple[FlowEdge, ...] = edges_from_json(version.edges_json)
            input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
            job_id: UUID | None = run.job_id
            version_digest: str = version.digest

            # 更新状态为 running
            run.status = "running"
            run.started_at = self._clock.now()
            await session.flush()

        # 2. 创建取消事件
        cancel_event: asyncio.Event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event

        # 3. 拓扑排序
        order: list[str] = topological_sort(nodes, edges)
        node_map: dict[str, FlowNode] = {n.node_id: n for n in nodes}

        # 4. 逐节点执行
        await self._run_node_loop(
            run_id=run_id,
            order=order,
            node_map=node_map,
            input_snapshot=input_snapshot,
            job_id=job_id,
            version_digest=version_digest,
            cancel_event=cancel_event,
            succeeded_nodes=set(),
            existing_summaries=[],
        )

    async def resume(self, run_id: UUID) -> None:
        """恢复执行：跳过已成功节点，重新执行失败/待执行节点。

        流程：
        1. 加载 FlowRun 及版本；
        2. 加载已存在的 FlowNodeExecution 记录；
        3. 跳过 status=succeeded 的节点（复用其输出）；
        4. 重新执行 status=failed/pending 的节点；
        5. 继续执行剩余未执行节点。

        Args:
            run_id: 执行记录 ID。

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

            version: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == run.flow_version_id
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {run.flow_version_id}",
                    retryable=False,
                    fields={"flow_version_id": str(run.flow_version_id)},
                )

            nodes: tuple[FlowNode, ...] = nodes_from_json(version.nodes_json)
            edges: tuple[FlowEdge, ...] = edges_from_json(version.edges_json)
            input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
            job_id: UUID | None = run.job_id
            version_digest: str = version.digest

            # 加载已存在的节点执行记录
            existing_execs: list[FlowNodeExecution] = list(
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

            # 更新状态为 running，重置 started_at（重新计时）
            run.status = "running"
            run.started_at = self._clock.now()
            await session.flush()

        # 拓扑排序
        order: list[str] = topological_sort(nodes, edges)
        node_map: dict[str, FlowNode] = {n.node_id: n for n in nodes}

        # 构建已成功节点的输出映射
        node_outputs: dict[str, dict[str, Any]] = {}
        succeeded_nodes: set[str] = set()
        for exec_record in existing_execs:
            if exec_record.status == "succeeded":
                succeeded_nodes.add(exec_record.node_id)
                node_outputs[exec_record.node_id] = dict(exec_record.output_summary or {})

        # 创建取消事件
        cancel_event: asyncio.Event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event

        # 逐节点执行（跳过已成功的）
        node_exec_summaries: list[dict[str, Any]] = []
        for exec_record in existing_execs:
            node_exec_summaries.append(
                {
                    "node_id": exec_record.node_id,
                    "status": exec_record.status,
                    "output_summary": exec_record.output_summary or {},
                }
            )

        await self._run_node_loop(
            run_id=run_id,
            order=order,
            node_map=node_map,
            input_snapshot=input_snapshot,
            job_id=job_id,
            version_digest=version_digest,
            cancel_event=cancel_event,
            succeeded_nodes=succeeded_nodes,
            existing_summaries=node_exec_summaries,
            node_outputs=node_outputs,
        )

    async def _run_node_loop(
        self,
        run_id: UUID,
        order: list[str],
        node_map: dict[str, FlowNode],
        input_snapshot: dict[str, Any],
        job_id: UUID | None,
        version_digest: str,
        cancel_event: asyncio.Event,
        succeeded_nodes: set[str],
        existing_summaries: list[dict[str, Any]],
        node_outputs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """execute / resume 共享的节点执行循环逻辑。

        遍历拓扑排序后的节点列表，逐节点解析输入并执行。
        检查取消信号，处理成功/失败/取消状态。
        成功节点可跳过（resume 场景）。

        Args:
            run_id: 执行记录 ID。
            order: 拓扑排序后的节点 ID 列表。
            node_map: 节点 ID → FlowNode 映射。
            input_snapshot: 流程外部输入快照。
            job_id: 关联作业 ID。
            version_digest: 版本摘要。
            cancel_event: 取消事件。
            succeeded_nodes: 已成功节点 ID 集合（resume 时跳过）。
            existing_summaries: 已存在的节点执行摘要列表（resume 时复用）。
            node_outputs: 已执行节点的输出映射（resume 时预填充）。
        """
        if node_outputs is None:
            node_outputs = {}
        node_exec_summaries: list[dict[str, Any]] = list(existing_summaries)

        for node_id in order:
            # 检查取消信号
            if cancel_event.is_set():
                await self._finalize_run(run_id, "cancelled", version_digest, node_exec_summaries)
                await self._update_job_status(job_id, "cancelled")
                return

            # 跳过已成功节点（resume 场景）
            if node_id in succeeded_nodes:
                continue

            node: FlowNode = node_map[node_id]

            # 解析输入
            inputs: dict[str, Any] = {}
            for port_name, binding in node.input_bindings.items():
                inputs[port_name] = resolve_input(binding, node_outputs, input_snapshot)
            # 合并外部输入（input_snapshot 里的参数覆盖节点默认值）
            # 安全约束（F-13）：禁止 inputs 覆盖文件路径类受保护参数
            # 但允许 artifact: 前缀的值通过（用户上传文件的合法引用）
            for key, val in input_snapshot.items():
                if not val:
                    continue
                if key in PROTECTED_PARAMS:
                    if isinstance(val, str) and val.startswith("artifact:"):
                        inputs[key] = val
                    continue
                if key not in inputs:
                    inputs[key] = val

            # 执行节点
            exec_result: dict[str, Any] = await self._execute_single_node(
                run_id, node, inputs, cancel_event, job_id
            )

            node_outputs[node_id] = exec_result.get("outputs", {})

            # 更新摘要
            found: bool = False
            for i, s in enumerate(node_exec_summaries):
                if s["node_id"] == node_id:
                    node_exec_summaries[i] = {
                        "node_id": node_id,
                        "status": exec_result["status"],
                        "output_summary": exec_result.get("output_summary", {}),
                    }
                    found = True
                    break
            if not found:
                node_exec_summaries.append(
                    {
                        "node_id": node_id,
                        "status": exec_result["status"],
                        "output_summary": exec_result.get("output_summary", {}),
                    }
                )

            if exec_result["status"] != "succeeded":
                await self._finalize_run(
                    run_id,
                    "failed",
                    version_digest,
                    node_exec_summaries,
                )
                await self._update_job_status(job_id, "failed")
                return

        # 全部成功
        await self._finalize_run(run_id, "succeeded", version_digest, node_exec_summaries)
        await self._update_job_status(job_id, "succeeded")

    async def cancel(self, run_id: UUID) -> FlowRun:
        """取消流程执行（设置 cancel_event）。

        协作式取消：设置 cancel_event 后，执行循环在下一个节点前退出。
        若执行不在进行中，直接更新数据库状态为 cancelled。

        Args:
            run_id: 执行记录 ID。

        Returns:
            FlowRun: 更新后的执行记录。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        # 设置取消事件
        event: asyncio.Event | None = self._cancel_events.get(run_id)
        if event is not None:
            event.set()

        # 更新数据库状态
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

            # 若处于 pending/running 状态，标记为 cancelled
            if run.status in ("pending", "running"):
                run.status = "cancelled"
                run.completed_at = self._clock.now()
                await session.flush()

            return run

    async def retry_node(self, run_id: UUID, node_id: str) -> FlowNodeExecution:
        """重试单个失败节点。

        重新执行指定节点，不影响其他已成功节点。

        Args:
            run_id: 执行记录 ID。
            node_id: 节点 ID。

        Returns:
            FlowNodeExecution: 重试后的节点执行记录。

        Raises:
            AppError: code="not_found"，当执行记录或节点不存在。
            AppError: code="validation_failed"，当节点非失败状态。
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

            version: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == run.flow_version_id
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {run.flow_version_id}",
                    retryable=False,
                    fields={"flow_version_id": str(run.flow_version_id)},
                )

            nodes: tuple[FlowNode, ...] = nodes_from_json(version.nodes_json)
            edges_from_json(version.edges_json)
            input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
            job_id: UUID | None = run.job_id

            # 查找节点定义
            node_map: dict[str, FlowNode] = {n.node_id: n for n in nodes}
            if node_id not in node_map:
                raise AppError(
                    code="not_found",
                    message=f"节点不存在: {node_id}",
                    retryable=False,
                    fields={"node_id": node_id},
                )

            # 查找已有的执行记录
            existing_exec: FlowNodeExecution | None = await session.scalar(
                sa.select(FlowNodeExecution).where(
                    FlowNodeExecution.flow_run_id == run_id,
                    FlowNodeExecution.node_id == node_id,
                )
            )
            if existing_exec is not None and existing_exec.status != "failed":
                raise AppError(
                    code="validation_failed",
                    message=(f"节点 {node_id} 状态为 {existing_exec.status}，仅失败节点可重试"),
                    retryable=False,
                    fields={
                        "node_id": node_id,
                        "status": existing_exec.status,
                    },
                )

            # 更新 run 状态为 running
            run.status = "running"
            await session.flush()

        # 构建上游输出（从已成功的节点执行记录获取）
        node_outputs: dict[str, dict[str, Any]] = {}
        async with self._scoped_session() as session:
            all_execs: list[FlowNodeExecution] = list(
                (
                    await session.execute(
                        sa.select(FlowNodeExecution).where(
                            FlowNodeExecution.flow_run_id == run_id,
                            FlowNodeExecution.status == "succeeded",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for exec_record in all_execs:
                node_outputs[exec_record.node_id] = dict(exec_record.output_summary or {})

        # 解析目标节点输入
        node: FlowNode = node_map[node_id]
        inputs: dict[str, Any] = {}
        for port_name, binding in node.input_bindings.items():
            inputs[port_name] = resolve_input(binding, node_outputs, input_snapshot)

        # 创建取消事件
        cancel_event: asyncio.Event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event

        # 执行节点
        exec_result: dict[str, Any] = await self._execute_single_node(
            run_id, node, inputs, cancel_event, job_id
        )

        # 更新 run 状态
        version_digest: str = version.digest
        if exec_result["status"] == "succeeded":
            # 重新检查是否所有节点都成功
            all_succeeded: bool = True
            async with self._scoped_session() as session:
                pending_or_failed: list[FlowNodeExecution] = list(
                    (
                        await session.execute(
                            sa.select(FlowNodeExecution).where(
                                FlowNodeExecution.flow_run_id == run_id,
                                FlowNodeExecution.status.in_(["pending", "failed"]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                all_succeeded = len(pending_or_failed) == 0

            if all_succeeded:
                # 重新计算摘要
                async with self._scoped_session() as session:
                    all_execs2: list[FlowNodeExecution] = list(
                        (
                            await session.execute(
                                sa.select(FlowNodeExecution).where(
                                    FlowNodeExecution.flow_run_id == run_id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                summaries: list[dict[str, Any]] = [
                    {
                        "node_id": e.node_id,
                        "status": e.status,
                        "output_summary": e.output_summary or {},
                    }
                    for e in all_execs2
                ]
                await self._finalize_run(run_id, "succeeded", version_digest, summaries)
                await self._update_job_status(job_id, "succeeded")
            else:
                await self._finalize_run(run_id, "running", version_digest, [])
        else:
            await self._finalize_run(run_id, "failed", version_digest, [])
            await self._update_job_status(job_id, "failed")

        # 返回最新的节点执行记录
        async with self._scoped_session() as session:
            latest_exec: FlowNodeExecution | None = await session.scalar(
                sa.select(FlowNodeExecution)
                .where(
                    FlowNodeExecution.flow_run_id == run_id,
                    FlowNodeExecution.node_id == node_id,
                )
                .order_by(FlowNodeExecution.id.desc())
            )
            if latest_exec is None:
                raise AppError(
                    code="not_found",
                    message=f"节点执行记录不存在: {node_id}",
                    retryable=False,
                    fields={"node_id": node_id},
                )
            return latest_exec

    # ---- 内部辅助方法 ----

    async def _execute_single_node(
        self,
        run_id: UUID,
        node: FlowNode,
        inputs: dict[str, Any],
        cancel_event: asyncio.Event,
        job_id: UUID | None,
    ) -> dict[str, Any]:
        """执行单个节点并记录 FlowNodeExecution。

        Args:
            run_id: 执行记录 ID。
            node: 流程节点。
            inputs: 已解析的节点输入。
            cancel_event: 取消事件。
            job_id: 关联作业 ID。

        Returns:
            dict[str, Any]: 执行结果，包含 status/outputs/output_summary。
        """
        now_start: datetime = self._clock.now()
        input_summary: dict[str, Any] = serialize_input_summary(inputs)

        # 创建 FlowNodeExecution（pending）
        async with self._scoped_session() as session:
            execution = FlowNodeExecution(
                flow_run_id=run_id,
                node_id=node.node_id,
                status="pending",
                input_summary=input_summary,
            )
            session.add(execution)
            await session.flush()
            execution_id: UUID = execution.id

        # 更新为 running
        async with self._scoped_session() as session:
            await session.execute(
                sa.update(FlowNodeExecution)
                .values(
                    status="running",
                    started_at=now_start,
                )
                .where(FlowNodeExecution.id == execution_id)
            )

        try:
            # 获取组件 manifest（始终取最新发布版本，而非 flow 版本中记录的版本号）
            version_row: ComponentVersion = await self._registry.get_latest(node.component_name)
            manifest: ComponentManifest = build_manifest_from_version(version_row)

            # 从组件 manifest 的参数中动态加载 prompt 和 tool_type，覆盖 flow 版本中的快照
            props = manifest.parameters.get("properties", {})
            manifest_prompt = props.get("prompt", {}).get("default")
            if manifest_prompt and "prompt" in node.params:
                node.params["prompt"] = manifest_prompt
            manifest_tool_type = props.get("tool_type", {}).get("default")
            if manifest_tool_type:
                node.params["tool_type"] = manifest_tool_type

            # 构建 ComponentContext
            context: ComponentContext = ComponentContext(
                department_id=self._dept_id,
                user_id=UUID(str(job_id)) if job_id else new_id(),
                clock=self._clock,
                artifact_service=self._artifact_service,
                job_id=job_id or new_id(),
                cancel_event=cancel_event,
                workdir=Path("/tmp/irip-flow"),
                ai_config_provider=self._ai_config_provider,
            )

            # 合并节点参数与解析后的输入（输入端口数据注入 params）
            merged_params: dict[str, Any] = {**node.params, **inputs}

            # 运行组件
            result: ComponentResult = await self._runner.run(manifest, context, merged_params)

            # 序列化输出摘要
            output_summary: dict[str, Any] = serialize_output_summary(result)

            now_end: datetime = self._clock.now()
            duration_ms: int = int((now_end - now_start).total_seconds() * 1000)

            # 更新为 succeeded
            async with self._scoped_session() as session:
                await session.execute(
                    sa.update(FlowNodeExecution)
                    .values(
                        status="succeeded",
                        output_summary=output_summary,
                        diagnostics=result.diagnostics,
                        completed_at=now_end,
                        duration_ms=duration_ms,
                    )
                    .where(FlowNodeExecution.id == execution_id)
                )

            return {
                "status": "succeeded",
                "outputs": result.outputs,
                "output_summary": output_summary,
            }

        except Exception as exc:
            now_end: datetime = self._clock.now()
            duration_ms: int = int((now_end - now_start).total_seconds() * 1000)

            diagnostics: dict[str, Any] = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

            async with self._scoped_session() as session:
                await session.execute(
                    sa.update(FlowNodeExecution)
                    .values(
                        status="failed",
                        diagnostics=diagnostics,
                        completed_at=now_end,
                        duration_ms=duration_ms,
                    )
                    .where(FlowNodeExecution.id == execution_id)
                )

            return {
                "status": "failed",
                "outputs": {},
                "output_summary": {},
                "error": str(exc),
            }

    async def _finalize_run(
        self,
        run_id: UUID,
        status: str,
        version_digest: str,
        node_exec_summaries: list[dict[str, Any]],
    ) -> None:
        """更新 FlowRun 最终状态与输出摘要。

        Args:
            run_id: 执行记录 ID。
            status: 最终状态。
            version_digest: 版本摘要。
            node_exec_summaries: 节点执行摘要列表。
        """
        output_digest: str | None = None
        if status == "succeeded":
            async with self._scoped_session() as session:
                run: FlowRun | None = await session.scalar(
                    sa.select(FlowRun).where(FlowRun.id == run_id)
                )
                if run is not None:
                    input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
                    output_digest = compute_output_digest(
                        version_digest,
                        input_snapshot,
                        node_exec_summaries,
                    )

        now: datetime = self._clock.now()
        async with self._scoped_session() as session:
            values: dict[str, Any] = {
                "status": status,
                "completed_at": now,
            }
            if output_digest is not None:
                values["output_digest"] = output_digest
            await session.execute(sa.update(FlowRun).values(**values).where(FlowRun.id == run_id))

    async def _update_job_status(self, job_id: UUID | None, status: str) -> None:
        """更新关联作业状态。

        Args:
            job_id: 作业 ID（None 时跳过）。
            status: 目标状态（succeeded/failed/cancelled）。
        """
        if job_id is None:
            return

        from packages.jobs.entities import Job, JobStatus

        status_map: dict[str, JobStatus] = {
            "succeeded": JobStatus.SUCCEEDED,
            "failed": JobStatus.FAILED,
            "cancelled": JobStatus.CANCELLED,
        }
        job_status: JobStatus = status_map.get(status, JobStatus.SUCCEEDED)

        async with self._scoped_session() as session:
            await session.execute(
                sa.update(Job)
                .values(
                    status=job_status.value,
                    updated_at=sa.func.now(),
                )
                .where(Job.id == job_id)
            )
