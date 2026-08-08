"""ResearchOrchestrator 装配模块：execute_run / cancel_run 核心流程。

拆分自 orchestrator.py（IRIP 拆分任务）。``orchestrator_core`` 将
``ContextBuilderMixin`` / ``StepExecutorMixin`` / ``ResultAssemblerMixin``
装配为 ``ResearchOrchestrator``，并承载 Run 完整执行流程（execute_run）
与取消流程（cancel_run）。

向后兼容：``orchestrator.py`` 与 ``packages.research.execution.__init__``
均 re-export 本模块的 ``ResearchOrchestrator``，使旧式导入路径
``from packages.research.execution.orchestrator import ResearchOrchestrator`` 与
``from packages.research.orchestrator import ResearchOrchestrator``（经 sys.modules 别名）
仍可工作。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.research.execution.context_builder import ContextBuilderMixin
from packages.research.execution.models_trusted import StepStatus
from packages.research.execution.orchestrator_base import ResearchOrchestratorBase, logger
from packages.research.execution.result_assembler import ResultAssemblerMixin
from packages.research.execution.step_executor import StepExecutorMixin


class ResearchOrchestrator(
    ContextBuilderMixin,
    StepExecutorMixin,
    ResultAssemblerMixin,
    ResearchOrchestratorBase,
):
    """研究分析执行编排器。

    由各功能域 Mixin 装配而成：
    - ContextBuilderMixin: 范围检测 + 输入包 + 快照数据 + 研究上下文；
    - StepExecutorMixin: 步骤执行（python/llm/mixed）+ Insight 候选提取；
    - ResultAssemblerMixin: 拓扑排序 + 事件 + 状态 + 覆盖率聚合。

    本模块直接承载 execute_run / cancel_run 核心流程。
    """

    async def execute_run(self, run_id: UUID) -> None:
        """执行分析 Run 的完整流程。

        流程：
        1. 加载 Run + Plan + Snapshot；
        2. 拓扑排序 DAG 步骤；
        3. 初始化 ResearchAnalysisStep 行；
        4. 逐步执行 _execute_step；
        5. 每步前检查依赖闭包状态 + 范围越界；
        6. 每步后发布 SSE 事件；
        7. 全部完成后聚合覆盖率 → 确定 Run 最终状态；
        8. 释放调度槽位；
        9. 更新研究记忆文档。

        Args:
            run_id: Run ID。
        """
        logger.info("Starting execute_run: run_id=%s", run_id)

        created_by: UUID | None = None

        # 设置租户上下文
        if self._factory is not None:
            async with self._factory() as session:
                run_orm = await self._repo.get_run(session, run_id)
                if run_orm is None:
                    logger.error("Run not found: %s", run_id)
                    return
                # 检查 Run 状态——如果已取消/失败/完成则不再执行
                if run_orm.status in ("cancelled", "failed", "succeeded", "partially_succeeded"):
                    logger.info("Run %s already %s, skipping execution", run_id, run_orm.status)
                    return
                workspace_id = run_orm.workspace_id
                created_by = run_orm.created_by
                # 设置工件服务和记忆服务的租户上下文
                if hasattr(self._artifact_service, "set_context"):
                    self._artifact_service.set_context(run_orm.workspace_id, run_orm.created_by)
        else:
            run_orm = None
            workspace_id = None
            created_by = None

        # 使用 session_scope 管理事务
        if self._factory is None:
            logger.error("No session_factory available for orchestrator")
            return

        try:
            # 注册心跳
            if self._scheduler is not None:
                await self._scheduler.register_heartbeat(str(run_id))

            # 发布 Run 启动事件
            await self._publish_event(run_id, "run.status_changed", {"status": "running"})

            # 更新研究记忆
            if workspace_id is not None:
                await self._memory_service.update_from_event(
                    workspace_id, "run.started", {"run_id": str(run_id)}
                )

            # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
            if self._lineage_writer is not None:
                try:
                    # 获取 Run 关联的 snapshot_id
                    async with self._factory() as session:
                        run_for_hook = await self._repo.get_run(session, run_id)
                        snapshot_id_for_hook = run_for_hook.snapshot_id if run_for_hook else None
                    if snapshot_id_for_hook is not None:
                        await self._lineage_writer.on_run_started(run_id, [snapshot_id_for_hook])
                except Exception as exc:
                    logger.warning("on_run_started hook failed: %s", exc)

            # 加载 Run + Plan
            async with self._factory() as session:
                run = await self._repo.get_run(session, run_id)
                if run is None:
                    logger.error("Run not found: %s", run_id)
                    return
                plan = await self._repo.get_plan(session, run.plan_version_id)
                if plan is None:
                    logger.error("Plan not found for run: %s", run_id)
                    await self._fail_run(run_id, "计划不存在")
                    return

                dag_structure = plan.dag_structure
                steps_defs = dag_structure.get("steps", [])

            # 拓扑排序
            sorted_steps = self._topological_sort(steps_defs)
            if sorted_steps is None:
                await self._fail_run(run_id, "DAG 存在环，无法拓扑排序")
                return

            # 初始化步骤行
            async with self._factory() as session:
                steps_data = [
                    {
                        "step_key": s.get("step_key", f"step_{i}"),
                        "step_index": i,
                        "method": s.get("method", "python"),
                        "depends_on": s.get("dependencies", []),
                    }
                    for i, s in enumerate(sorted_steps)
                ]
                step_entities = await self._repo.batch_insert_steps(session, run_id, steps_data)
                step_map: dict[str, UUID] = {}
                for sd, ent in zip(steps_data, step_entities, strict=False):
                    step_map[sd["step_key"]] = ent.id

            # 验证 step 已写入数据库
            async with self._factory() as session:
                from packages.research.execution.entities_trusted import ResearchAnalysisStep

                result = await session.execute(
                    sa.select(ResearchAnalysisStep.id).where(ResearchAnalysisStep.run_id == run_id)
                )
                db_step_ids = [row[0] for row in result.fetchall()]
            if not db_step_ids:
                logger.error(
                    "Steps were not persisted to database after batch_insert! run_id=%s", run_id
                )
                await self._fail_run(run_id, "步骤初始化失败：步骤未写入数据库")
                return
            logger.info("Steps persisted: %d steps for run %s", len(db_step_ids), run_id)

            # 逐步执行
            failed_steps: set[str] = set()
            succeeded_steps: set[str] = set()
            coverage_declarations: list[dict[str, Any]] = []

            for idx, step_def in enumerate(sorted_steps):
                step_key = step_def.get("step_key", f"step_{idx}")

                # 注册心跳
                if self._scheduler is not None:
                    await self._scheduler.register_heartbeat(str(run_id))

                # 检查依赖闭包状态
                deps = step_def.get("dependencies", [])
                dep_failed = any(d in failed_steps for d in deps)

                if dep_failed:
                    # 依赖步骤失败 → 跳过
                    async with self._factory() as session:
                        step_id = step_map.get(step_key)
                        if step_id:
                            await self._repo.update_step_status(
                                session, step_id, StepStatus.SKIPPED.value
                            )
                    failed_steps.add(step_key)
                    await self._publish_event(
                        run_id,
                        "step.status_changed",
                        {"step_key": step_key, "status": "skipped"},
                    )
                    continue

                # 检查 Run 是否被取消
                async with self._factory() as session:
                    run_check = await self._repo.get_run(session, run_id)
                    if run_check and run_check.status == "cancelled":
                        logger.info("Run cancelled, stopping: %s", run_id)
                        return

                # 执行步骤
                step_id = step_map.get(step_key)
                step_result = await self._execute_step(
                    run_id, step_id, step_def, step_map, plan, created_by
                )

                if step_result:
                    succeeded_steps.add(step_key)
                    coverage_declarations.append(step_result)
                else:
                    failed_steps.add(step_key)

            # 聚合覆盖率 → 确定 Run 最终状态
            final_status = self._determine_final_status(sorted_steps, succeeded_steps, failed_steps)
            coverage_summary = self._aggregate_coverage(coverage_declarations)

            async with self._factory() as session:
                await self._repo.update_run_status(
                    session,
                    run_id,
                    final_status,
                    completed_at=datetime.now(UTC),
                    coverage_summary=coverage_summary,
                )

            # 标记成功步骤的工件为可发布
            if succeeded_steps:
                await self._artifact_service.mark_publishable(run_id, succeeded_steps)

            # 发布最终状态事件
            await self._publish_event(
                run_id,
                "run.status_changed",
                {"status": final_status, "coverage": coverage_summary},
            )

            # 更新研究记忆
            if workspace_id is not None:
                await self._memory_service.update_from_event(
                    workspace_id,
                    "run.completed",
                    {
                        "run_id": str(run_id),
                        "status": final_status,
                        "coverage": coverage_summary,
                    },
                )

            # 释放调度槽位
            if self._scheduler is not None and created_by is not None:
                await self._scheduler.release_slot(str(created_by), str(run_id))

            # 审计（Worker 进程中 RLS 可能阻止审计写入，不阻断主流程）
            try:
                async with self._factory() as session:
                    if created_by is not None:
                        from packages.common.tenant_guc import set_user_guc

                        await set_user_guc(session, created_by)
                    await AuditRecorder.record(
                        session,
                        AuditEventData(
                            department_id=self._dept_id or workspace_id or UUID(int=0),
                            actor_user_id=created_by,
                            action="research.run.complete",
                            resource_type="research_analysis_run",
                            resource_id=run_id,
                            payload={
                                "status": final_status,
                                "succeeded": len(succeeded_steps),
                                "failed": len(failed_steps),
                            },
                        ),
                    )
            except Exception as audit_err:
                logger.warning("Audit record failed (non-blocking): %s", audit_err)

            logger.info(
                "Run %s completed: status=%s, succeeded=%d, failed=%d",
                run_id,
                final_status,
                len(succeeded_steps),
                len(failed_steps),
            )

        except Exception as exc:
            logger.exception("Run execution failed: %s", exc)
            await self._fail_run(run_id, f"执行异常: {str(exc)}")
            if self._scheduler is not None and created_by is not None:
                await self._scheduler.release_slot(str(created_by), str(run_id))

    async def cancel_run(self, run_id: UUID) -> None:
        """取消 Run（编排器层面）。

        标记当前步骤 cancelled → 下游步骤 skipped → Run cancelled → 销毁活跃容器 → 释放槽位。

        Args:
            run_id: Run ID。
        """
        async with self._factory() as session:
            steps = await self._repo.list_steps_by_run(session, run_id)
            for step in steps:
                if step.status == "running":
                    await self._repo.update_step_status(
                        session, step.id, StepStatus.CANCELLED.value
                    )
                elif step.status == "pending":
                    await self._repo.update_step_status(session, step.id, StepStatus.SKIPPED.value)

            await self._repo.update_run_status(
                session,
                run_id,
                "cancelled",
                cancelled_at=datetime.now(UTC),
            )

        await self._publish_event(run_id, "run.status_changed", {"status": "cancelled"})


__all__ = ["ResearchOrchestrator"]
