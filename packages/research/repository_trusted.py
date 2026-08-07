"""可信执行数据访问层扩展（阶段 2 新增方法）。

ResearchRepositoryTrusted 封装阶段 2 新增的 6 张表的数据库操作：
- 计划版本 CRUD（insert_plan_version / get_plan / list_plans
  / get_latest_plan_version / update_plan_status）
- Run CRUD（insert_run / get_run / list_runs / update_run_status / update_run_queue_position /
  get_active_run_for_workspace / get_next_run_number）
- 步骤 CRUD（insert_step / get_step / list_steps_by_run / update_step_status /
  update_step_progress / batch_insert_steps / get_step_by_key）
- 工件 CRUD（insert_artifact / get_artifact / list_artifacts_by_run / list_artifacts_by_step /
  update_artifact_publishable）
- 对话 CRUD（insert_conversation_message / list_messages / count_messages）
- 记忆文档 CRUD（get_memory / upsert_memory / update_memory_version）

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。

参照 packages/research/repository.py 的模式。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities_trusted import (
    ResearchAiConversation,
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
    ResearchAnalysisStep,
    ResearchMemoryDocument,
    ResearchRunArtifact,
)


class ResearchRepositoryTrusted:
    """可信执行数据访问层（阶段 2 新增）。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    # ============================================================
    # 计划版本
    # ============================================================

    @staticmethod
    async def insert_plan_version(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        version_number: int,
        dag_structure: dict,
        coverage_declaration: dict | None = None,
        created_by: UUID,
    ) -> ResearchAnalysisPlanVersion:
        """插入分析计划版本，返回 ORM 实体。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            version_number: 版本号。
            dag_structure: DAG 步骤结构（JSONB）。
            coverage_declaration: 覆盖声明（可选）。
            created_by: 创建人 ID。

        Returns:
            ResearchAnalysisPlanVersion: 计划版本 ORM 实体。
        """
        plan = ResearchAnalysisPlanVersion(
            id=new_id(),
            workspace_id=workspace_id,
            version_number=version_number,
            dag_structure=dag_structure,
            coverage_declaration=coverage_declaration,
            status="draft",
            created_by=created_by,
        )
        session.add(plan)
        await session.flush()
        return plan

    @staticmethod
    async def get_plan(
        session: AsyncSession,
        plan_id: UUID,
    ) -> ResearchAnalysisPlanVersion | None:
        """获取计划版本。

        Args:
            session: 异步会话。
            plan_id: 计划版本 ID。

        Returns:
            ResearchAnalysisPlanVersion | None: 计划版本实体，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisPlanVersion).where(ResearchAnalysisPlanVersion.id == plan_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_plans(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchAnalysisPlanVersion]:
        """列出工作空间的全部计划版本（按版本号降序）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchAnalysisPlanVersion]: 计划版本列表。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisPlanVersion)
            .where(ResearchAnalysisPlanVersion.workspace_id == workspace_id)
            .order_by(ResearchAnalysisPlanVersion.version_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_plan_version(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> ResearchAnalysisPlanVersion | None:
        """获取工作空间的最新计划版本。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            ResearchAnalysisPlanVersion | None: 最新版本，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisPlanVersion)
            .where(ResearchAnalysisPlanVersion.workspace_id == workspace_id)
            .order_by(ResearchAnalysisPlanVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_plan_status(
        session: AsyncSession,
        plan_id: UUID,
        status: str,
        confirmed_at: datetime | None = None,
        confirmed_by: UUID | None = None,
    ) -> None:
        """更新计划版本状态。

        Args:
            session: 异步会话。
            plan_id: 计划版本 ID。
            status: 新状态（confirmed / superseded）。
            confirmed_at: 确认时间（确认时设置）。
            confirmed_by: 确认人 ID（确认时设置）。
        """
        values: dict = {"status": status}
        if confirmed_at is not None:
            values["confirmed_at"] = confirmed_at
        if confirmed_by is not None:
            values["confirmed_by"] = confirmed_by
        await session.execute(
            sa.update(ResearchAnalysisPlanVersion)
            .where(ResearchAnalysisPlanVersion.id == plan_id)
            .values(**values)
        )

    @staticmethod
    async def supersede_old_plans(
        session: AsyncSession,
        workspace_id: UUID,
        exclude_plan_id: UUID,
    ) -> None:
        """将旧版本计划标记为 superseded（新版本生成时调用）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            exclude_plan_id: 排除的计划 ID（新版本）。
        """
        await session.execute(
            sa.update(ResearchAnalysisPlanVersion)
            .where(
                ResearchAnalysisPlanVersion.workspace_id == workspace_id,
                ResearchAnalysisPlanVersion.id != exclude_plan_id,
                ResearchAnalysisPlanVersion.status == "confirmed",
            )
            .values(status="superseded")
        )

    # ============================================================
    # 分析运行
    # ============================================================

    @staticmethod
    async def insert_run(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        plan_version_id: UUID,
        snapshot_id: UUID,
        run_number: int,
        image_digest: str,
        created_by: UUID,
    ) -> ResearchAnalysisRun:
        """插入分析运行，返回 ORM 实体。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            plan_version_id: 计划版本 ID。
            snapshot_id: 快照 ID。
            run_number: Run 编号。
            image_digest: 镜像 digest。
            created_by: 创建人 ID。

        Returns:
            ResearchAnalysisRun: Run ORM 实体。
        """
        run = ResearchAnalysisRun(
            id=new_id(),
            workspace_id=workspace_id,
            plan_version_id=plan_version_id,
            snapshot_id=snapshot_id,
            run_number=run_number,
            status="queued",
            image_digest=image_digest,
            created_by=created_by,
        )
        session.add(run)
        await session.flush()
        return run

    @staticmethod
    async def get_run(
        session: AsyncSession,
        run_id: UUID,
    ) -> ResearchAnalysisRun | None:
        """获取 Run。

        Args:
            session: 异步会话。
            run_id: Run ID。

        Returns:
            ResearchAnalysisRun | None: Run 实体，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisRun).where(ResearchAnalysisRun.id == run_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_runs(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchAnalysisRun]:
        """列出工作空间的全部 Run（按编号降序）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchAnalysisRun]: Run 列表。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisRun)
            .where(ResearchAnalysisRun.workspace_id == workspace_id)
            .order_by(ResearchAnalysisRun.run_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_run_status(
        session: AsyncSession,
        run_id: UUID,
        status: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
        cancelled_by: UUID | None = None,
        error_summary: str | None = None,
        coverage_summary: dict | None = None,
    ) -> None:
        """更新 Run 状态。

        Args:
            session: 异步会话。
            run_id: Run ID。
            status: 新状态。
            started_at: 开始时间。
            completed_at: 完成时间。
            cancelled_at: 取消时间。
            cancelled_by: 取消人 ID。
            error_summary: 错误摘要。
            coverage_summary: 覆盖率汇总。
        """
        values: dict = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if cancelled_at is not None:
            values["cancelled_at"] = cancelled_at
        if cancelled_by is not None:
            values["cancelled_by"] = cancelled_by
        if error_summary is not None:
            values["error_summary"] = error_summary
        if coverage_summary is not None:
            values["coverage_summary"] = coverage_summary
        await session.execute(
            sa.update(ResearchAnalysisRun).where(ResearchAnalysisRun.id == run_id).values(**values)
        )

    @staticmethod
    async def update_run_queue_position(
        session: AsyncSession,
        run_id: UUID,
        position: int | None,
    ) -> None:
        """更新 Run 排队位置。

        Args:
            session: 异步会话。
            run_id: Run ID。
            position: 排队位置（None 表示已出队）。
        """
        await session.execute(
            sa.update(ResearchAnalysisRun)
            .where(ResearchAnalysisRun.id == run_id)
            .values(queue_position=position)
        )

    @staticmethod
    async def get_active_run_for_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> ResearchAnalysisRun | None:
        """获取工作空间的活跃 Run（status IN queued/planning/running）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            ResearchAnalysisRun | None: 活跃 Run，无时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisRun).where(
                ResearchAnalysisRun.workspace_id == workspace_id,
                ResearchAnalysisRun.status.in_(["queued", "planning", "running"]),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_next_run_number(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """获取下一个 Run 编号（当前最大编号 + 1）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            int: 下一个 Run 编号（从 1 开始）。
        """
        result = await session.execute(
            sa.select(sa.func.max(ResearchAnalysisRun.run_number)).where(
                ResearchAnalysisRun.workspace_id == workspace_id
            )
        )
        max_num = result.scalar()
        return (int(max_num) + 1) if max_num is not None else 1

    # ============================================================
    # 分析步骤
    # ============================================================

    @staticmethod
    async def insert_step(
        session: AsyncSession,
        *,
        run_id: UUID,
        step_key: str,
        step_index: int,
        method: str,
        depends_on: list | None = None,
    ) -> ResearchAnalysisStep:
        """插入分析步骤，返回 ORM 实体。

        Args:
            session: 异步会话。
            run_id: Run ID。
            step_key: 步骤键。
            step_index: 步骤序号。
            method: 执行方式。
            depends_on: 依赖步骤 key 列表。

        Returns:
            ResearchAnalysisStep: 步骤 ORM 实体。
        """
        step = ResearchAnalysisStep(
            id=new_id(),
            run_id=run_id,
            step_key=step_key,
            step_index=step_index,
            status="pending",
            method=method,
            depends_on=depends_on if depends_on is not None else [],
        )
        session.add(step)
        await session.flush()
        return step

    @staticmethod
    async def batch_insert_steps(
        session: AsyncSession,
        run_id: UUID,
        steps_data: list[dict],
    ) -> list[ResearchAnalysisStep]:
        """批量插入步骤。

        Args:
            session: 异步会话。
            run_id: Run ID。
            steps_data: 步骤数据列表（每项含 step_key, step_index, method, depends_on）。

        Returns:
            list[ResearchAnalysisStep]: 步骤 ORM 实体列表。
        """
        entities: list[ResearchAnalysisStep] = []
        for sd in steps_data:
            step = ResearchAnalysisStep(
                id=new_id(),
                run_id=run_id,
                step_key=sd["step_key"],
                step_index=sd["step_index"],
                status="pending",
                method=sd.get("method", "python"),
                depends_on=sd.get("depends_on", []),
            )
            session.add(step)
            entities.append(step)
        await session.flush()
        return entities

    @staticmethod
    async def get_step(
        session: AsyncSession,
        step_id: UUID,
    ) -> ResearchAnalysisStep | None:
        """获取步骤。

        Args:
            session: 异步会话。
            step_id: 步骤 ID。

        Returns:
            ResearchAnalysisStep | None: 步骤实体，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisStep).where(ResearchAnalysisStep.id == step_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_step_by_key(
        session: AsyncSession,
        run_id: UUID,
        step_key: str,
    ) -> ResearchAnalysisStep | None:
        """按 step_key 获取步骤。

        Args:
            session: 异步会话。
            run_id: Run ID。
            step_key: 步骤键。

        Returns:
            ResearchAnalysisStep | None: 步骤实体，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisStep).where(
                ResearchAnalysisStep.run_id == run_id,
                ResearchAnalysisStep.step_key == step_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_steps_by_run(
        session: AsyncSession,
        run_id: UUID,
    ) -> list[ResearchAnalysisStep]:
        """列出 Run 的全部步骤（按序号排序）。

        Args:
            session: 异步会话。
            run_id: Run ID。

        Returns:
            list[ResearchAnalysisStep]: 步骤列表。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisStep)
            .where(ResearchAnalysisStep.run_id == run_id)
            .order_by(ResearchAnalysisStep.step_index)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_step_status(
        session: AsyncSession,
        step_id: UUID,
        status: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error_message: str | None = None,
        error_classification: str | None = None,
    ) -> None:
        """更新步骤状态。

        Args:
            session: 异步会话。
            step_id: 步骤 ID。
            status: 新状态。
            started_at: 开始时间。
            completed_at: 完成时间。
            error_message: 错误消息。
            error_classification: 错误分类。
        """
        values: dict = {"status": status, "updated_at": sa.func.now()}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if error_message is not None:
            values["error_message"] = error_message
        if error_classification is not None:
            values["error_classification"] = error_classification
        await session.execute(
            sa.update(ResearchAnalysisStep)
            .where(ResearchAnalysisStep.id == step_id)
            .values(**values)
        )

    @staticmethod
    async def update_step_progress(
        session: AsyncSession,
        step_id: UUID,
        *,
        analysis_mode: str | None = None,
        data_budget_tokens: int | None = None,
        coverage_rate: float | None = None,
        llm_read_rate: float | None = None,
        is_sampled: bool | None = None,
        mode_reason: str | None = None,
        attempt_count: int | None = None,
    ) -> None:
        """更新步骤进度（覆盖率/模式/尝试次数）。

        Args:
            session: 异步会话。
            step_id: 步骤 ID。
            analysis_mode: 分析模式。
            data_budget_tokens: 数据预算。
            coverage_rate: 数据覆盖率。
            llm_read_rate: LLM 阅读率。
            is_sampled: 是否抽样。
            mode_reason: 模式选择原因。
            attempt_count: 尝试次数。
        """
        values: dict = {"updated_at": sa.func.now()}
        if analysis_mode is not None:
            values["analysis_mode"] = analysis_mode
        if data_budget_tokens is not None:
            values["data_budget_tokens"] = data_budget_tokens
        if coverage_rate is not None:
            values["coverage_rate"] = coverage_rate
        if llm_read_rate is not None:
            values["llm_read_rate"] = llm_read_rate
        if is_sampled is not None:
            values["is_sampled"] = is_sampled
        if mode_reason is not None:
            values["mode_reason"] = mode_reason
        if attempt_count is not None:
            values["attempt_count"] = attempt_count
        await session.execute(
            sa.update(ResearchAnalysisStep)
            .where(ResearchAnalysisStep.id == step_id)
            .values(**values)
        )

    @staticmethod
    async def get_all_step_statuses(
        session: AsyncSession,
        run_id: UUID,
    ) -> list[tuple[str, str]]:
        """获取 Run 全部步骤的 (step_key, status) 列表。

        Args:
            session: 异步会话。
            run_id: Run ID。

        Returns:
            list[tuple[str, str]]: (step_key, status) 列表。
        """
        result = await session.execute(
            sa.select(ResearchAnalysisStep.step_key, ResearchAnalysisStep.status)
            .where(ResearchAnalysisStep.run_id == run_id)
            .order_by(ResearchAnalysisStep.step_index)
        )
        return [(row[0], row[1]) for row in result.all()]

    # ============================================================
    # 工件
    # ============================================================

    @staticmethod
    async def insert_artifact(
        session: AsyncSession,
        *,
        run_id: UUID,
        step_id: UUID | None,
        artifact_type: str,
        artifact_key: str,
        storage_path: str,
        content_hash: str | None = None,
        size_bytes: int | None = None,
        is_publishable: bool = False,
    ) -> ResearchRunArtifact:
        """插入工件记录，返回 ORM 实体。

        Args:
            session: 异步会话。
            run_id: Run ID。
            step_id: 步骤 ID（可选）。
            artifact_type: 工件类型。
            artifact_key: 工件键名。
            storage_path: MinIO 存储路径。
            content_hash: 内容哈希。
            size_bytes: 文件大小。
            is_publishable: 是否可发布。

        Returns:
            ResearchRunArtifact: 工件 ORM 实体。
        """
        artifact = ResearchRunArtifact(
            id=new_id(),
            run_id=run_id,
            step_id=step_id,
            artifact_type=artifact_type,
            artifact_key=artifact_key,
            storage_path=storage_path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            is_publishable=is_publishable,
        )
        session.add(artifact)
        await session.flush()
        return artifact

    @staticmethod
    async def get_artifact(
        session: AsyncSession,
        artifact_id: UUID,
    ) -> ResearchRunArtifact | None:
        """获取工件。

        Args:
            session: 异步会话。
            artifact_id: 工件 ID。

        Returns:
            ResearchRunArtifact | None: 工件实体，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchRunArtifact).where(ResearchRunArtifact.id == artifact_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_artifacts_by_run(
        session: AsyncSession,
        run_id: UUID,
        artifact_type: str | None = None,
    ) -> list[ResearchRunArtifact]:
        """列出 Run 的工件。

        Args:
            session: 异步会话。
            run_id: Run ID。
            artifact_type: 工件类型过滤（可选）。

        Returns:
            list[ResearchRunArtifact]: 工件列表。
        """
        stmt = sa.select(ResearchRunArtifact).where(ResearchRunArtifact.run_id == run_id)
        if artifact_type is not None:
            stmt = stmt.where(ResearchRunArtifact.artifact_type == artifact_type)
        stmt = stmt.order_by(ResearchRunArtifact.created_at)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_artifacts_by_step(
        session: AsyncSession,
        step_id: UUID,
    ) -> list[ResearchRunArtifact]:
        """列出步骤的工件。

        Args:
            session: 异步会话。
            step_id: 步骤 ID。

        Returns:
            list[ResearchRunArtifact]: 工件列表。
        """
        result = await session.execute(
            sa.select(ResearchRunArtifact)
            .where(ResearchRunArtifact.step_id == step_id)
            .order_by(ResearchRunArtifact.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_artifact_publishable(
        session: AsyncSession,
        artifact_id: UUID,
        is_publishable: bool,
    ) -> None:
        """更新工件发布资格。

        Args:
            session: 异步会话。
            artifact_id: 工件 ID。
            is_publishable: 是否可发布。
        """
        await session.execute(
            sa.update(ResearchRunArtifact)
            .where(ResearchRunArtifact.id == artifact_id)
            .values(is_publishable=is_publishable)
        )

    # ============================================================
    # AI 对话
    # ============================================================

    @staticmethod
    async def insert_conversation_message(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        role: str,
        content: dict,
        run_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> ResearchAiConversation:
        """插入对话消息，返回 ORM 实体。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            role: 角色（user / assistant / system）。
            content: 消息内容（JSONB dict）。
            run_id: 关联 Run ID（可选）。
            created_by: 创建人 ID（AI 消息可为空）。

        Returns:
            ResearchAiConversation: 对话消息 ORM 实体。
        """
        msg = ResearchAiConversation(
            id=new_id(),
            workspace_id=workspace_id,
            role=role,
            content=content,
            run_id=run_id,
            created_by=created_by,
        )
        session.add(msg)
        await session.flush()
        return msg

    @staticmethod
    async def list_messages(
        session: AsyncSession,
        workspace_id: UUID,
        run_id: UUID | None = None,
        limit: int = 50,
    ) -> list[ResearchAiConversation]:
        """列出对话消息（最近 N 条，按时间正序返回）。

        长对话截断策略：查询时仅返回最近 limit 条，旧消息保留在表中不删除。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            run_id: 关联 Run ID（可选过滤）。
            limit: 返回条数上限（默认 50）。

        Returns:
            list[ResearchAiConversation]: 消息列表（按时间正序）。
        """
        stmt = sa.select(ResearchAiConversation).where(
            ResearchAiConversation.workspace_id == workspace_id
        )
        if run_id is not None:
            stmt = stmt.where(ResearchAiConversation.run_id == run_id)
        # 先按时间倒序取最近 limit 条，再反转为正序
        stmt = stmt.order_by(ResearchAiConversation.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    @staticmethod
    async def count_messages(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """统计工作空间的对话消息总数。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            int: 消息总数。
        """
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(ResearchAiConversation)
            .where(ResearchAiConversation.workspace_id == workspace_id)
        )
        return int(result.scalar() or 0)

    # ============================================================
    # 研究记忆文档
    # ============================================================

    @staticmethod
    async def get_memory(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> ResearchMemoryDocument | None:
        """获取工作空间的研究记忆文档。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            ResearchMemoryDocument | None: 记忆文档，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(ResearchMemoryDocument).where(
                ResearchMemoryDocument.workspace_id == workspace_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_memory(
        session: AsyncSession,
        workspace_id: UUID,
        document: dict,
    ) -> ResearchMemoryDocument:
        """插入或更新研究记忆文档。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            document: 记忆文档（JSONB dict）。

        Returns:
            ResearchMemoryDocument: 记忆文档 ORM 实体。
        """
        existing = await ResearchRepositoryTrusted.get_memory(session, workspace_id)
        if existing is not None:
            existing.document = document
            existing.version = existing.version + 1
            existing.updated_at = sa.func.now()
            await session.flush()
            return existing
        mem = ResearchMemoryDocument(
            id=new_id(),
            workspace_id=workspace_id,
            document=document,
            version=1,
        )
        session.add(mem)
        await session.flush()
        return mem

    @staticmethod
    async def update_memory_version(
        session: AsyncSession,
        workspace_id: UUID,
        document: dict,
    ) -> None:
        """更新记忆文档内容和版本号。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            document: 新的记忆文档。
        """
        existing = await ResearchRepositoryTrusted.get_memory(session, workspace_id)
        if existing is not None:
            existing.document = document
            existing.version = existing.version + 1
            existing.updated_at = sa.func.now()
            await session.flush()
