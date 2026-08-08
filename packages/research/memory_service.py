"""研究记忆服务：后台研究记忆文档 CRUD + 事件驱动自动更新。

ResearchMemoryService 负责：
1. get_or_create: 获取或创建工作空间的记忆文档；
2. update_from_event: 根据事件自动更新文档内容；
3. rebuild_from_events: 从审计事件重建文档（文档可重建，非权威源）。

记忆文档结构（JSONB）：
{
    "main_question": "...",
    "current_scope": "...",
    "evidence_summary": [...],
    "confirmed_plan": {"version": 1, "steps": [...]},
    "key_methods": [...],
    "completed_runs": [...],
    "accepted_insights": [...],
    "rejected_insights": [...],
    "limitations": [...],
    "next_steps": [...]
}

事件驱动更新：
- run.started → 记录 run_id
- run.completed → 更新 completed_runs + coverage + key_methods
- plan.confirmed → 更新 confirmed_plan
- insight.accepted → 加入 accepted_insights
- insight.rejected → 加入 rejected_insights
"""

import copy
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted

logger = logging.getLogger("research.memory")

#: 记忆文档默认结构。
DEFAULT_DOCUMENT: dict[str, Any] = {
    "main_question": "",
    "current_scope": "",
    "evidence_summary": [],
    "confirmed_plan": None,
    "key_methods": [],
    "completed_runs": [],
    "accepted_insights": [],
    "rejected_insights": [],
    "limitations": [],
    "next_steps": [],
}


class ResearchMemoryService(ScopedSessionMixin):
    """后台研究记忆文档服务。

    依赖注入 session_factory。

    Attributes:
        _factory: 异步会话工厂。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """初始化研究记忆服务。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory
        self._dept_id: UUID | None = None
        self._actor_id: UUID | None = None
        self._rls_dept_id: UUID | None = None

    def set_context(self, department_id: UUID, actor_id: UUID | None) -> None:
        """设置租户上下文（Worker 调用时使用）。

        Args:
            department_id: 部门 ID。
            actor_id: 操作人 ID。
        """
        self._dept_id = department_id
        self._actor_id = actor_id

    async def get_or_create(self, workspace_id: UUID) -> dict[str, Any]:
        """获取或创建工作空间的记忆文档。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            dict: 记忆文档内容。
        """
        async with self._scoped_session() as session:
            mem = await ResearchRepositoryTrusted.get_memory(session, workspace_id)
            if mem is not None:
                return dict(mem.document)
            # 创建空文档
            document = copy.deepcopy(DEFAULT_DOCUMENT)
            await ResearchRepositoryTrusted.upsert_memory(session, workspace_id, document)
            return document

    async def update_from_event(
        self,
        workspace_id: UUID,
        event_type: str,
        event_data: dict[str, Any],
    ) -> dict[str, Any]:
        """根据事件自动更新记忆文档。

        事件类型处理：
        - run.started → 记录 run_id
        - run.completed → 更新 completed_runs + coverage + key_methods
        - plan.confirmed → 更新 confirmed_plan
        - insight.accepted → 加入 accepted_insights
        - insight.rejected → 加入 rejected_insights

        文档与原始事件冲突时以原始事件为准。

        Args:
            workspace_id: 工作空间 ID。
            event_type: 事件类型。
            event_data: 事件数据。

        Returns:
            dict: 更新后的记忆文档。
        """
        async with self._scoped_session() as session:
            mem = await ResearchRepositoryTrusted.get_memory(session, workspace_id)
            if mem is not None:
                document = copy.deepcopy(dict(mem.document))
            else:
                document = copy.deepcopy(DEFAULT_DOCUMENT)

            # 确保所有字段存在
            for key, default in DEFAULT_DOCUMENT.items():
                if key not in document:
                    document[key] = copy.deepcopy(default)

            # 根据事件类型更新
            if event_type == "run.started":
                run_id = event_data.get("run_id", "")
                if run_id and run_id not in document["completed_runs"]:
                    document["completed_runs"].append(
                        {
                            "run_id": run_id,
                            "status": "started",
                        }
                    )

            elif event_type == "run.completed":
                run_id = event_data.get("run_id", "")
                status = event_data.get("status", "unknown")
                coverage = event_data.get("coverage", {})

                # 更新或添加 Run 记录
                found = False
                for run_entry in document["completed_runs"]:
                    if run_entry.get("run_id") == run_id:
                        run_entry["status"] = status
                        run_entry["coverage"] = coverage
                        found = True
                        break
                if not found:
                    document["completed_runs"].append(
                        {
                            "run_id": run_id,
                            "status": status,
                            "coverage": coverage,
                        }
                    )

                # 提取关键方法
                if coverage and "analysis_mode" in coverage:
                    mode = coverage["analysis_mode"]
                    if mode not in document["key_methods"]:
                        document["key_methods"].append(mode)

            elif event_type == "plan.confirmed":
                plan_version = event_data.get("version_number", 1)
                steps = event_data.get("steps", [])
                document["confirmed_plan"] = {
                    "version": plan_version,
                    "steps": steps,
                }

            elif event_type == "insight.accepted":
                insight = event_data.get("insight_id", "")
                if insight:
                    document["accepted_insights"].append(event_data)

            elif event_type == "insight.rejected":
                insight = event_data.get("insight_id", "")
                if insight:
                    document["rejected_insights"].append(event_data)

            # 更新文档
            await ResearchRepositoryTrusted.upsert_memory(session, workspace_id, document)

            logger.debug(
                "Memory document updated: workspace=%s, event=%s",
                workspace_id,
                event_type,
            )
            return document

    async def rebuild_from_events(self, workspace_id: UUID) -> dict[str, Any]:
        """从审计事件重建记忆文档。

        文档可重建（非权威源），原始事件为权威源。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            dict: 重建后的记忆文档。
        """
        # 获取全部审计事件
        document = copy.deepcopy(DEFAULT_DOCUMENT)

        # 简化实现：基于当前数据重建
        # 实际实现应从 audit_event 表查询研究域相关事件
        async with self._scoped_session() as session:
            # 获取已确认的计划
            plans = await ResearchRepositoryTrusted.list_plans(session, workspace_id)
            for plan in plans:
                if plan.status == "confirmed":
                    document["confirmed_plan"] = {
                        "version": plan.version_number,
                        "steps": plan.dag_structure.get("steps", []),
                    }
                    break

            # 获取已完成的 Run
            runs = await ResearchRepositoryTrusted.list_runs(session, workspace_id)
            for run in runs:
                if run.status in ("succeeded", "partially_succeeded", "failed"):
                    document["completed_runs"].append(
                        {
                            "run_id": str(run.id),
                            "status": run.status,
                            "run_number": run.run_number,
                            "coverage": run.coverage_summary or {},
                        }
                    )

        # 更新文档
        async with self._scoped_session() as session:
            await ResearchRepositoryTrusted.upsert_memory(session, workspace_id, document)

        logger.info("Memory document rebuilt: workspace=%s", workspace_id)
        return document
