"""结果组装 Mixin：拓扑排序、事件发布、状态与覆盖率聚合。

拆分自 orchestrator.py（IRIP 拆分任务）。``ResultAssemblerMixin`` 承载
DAG 拓扑排序（_topological_sort，Kahn 算法）、SSE 事件发布（_publish_event）、
Run 最终状态判定（_determine_final_status）、覆盖率聚合（_aggregate_coverage）
与 Run 失败标记（_fail_run）。
"""

import json
import os
from collections import deque
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from packages.research.execution.models_trusted import CoverageDeclaration
from packages.research.execution.orchestrator_base import ResearchOrchestratorBase, logger


class ResultAssemblerMixin(ResearchOrchestratorBase):
    """结果组装功能域：拓扑排序 + 事件 + 状态 + 覆盖率聚合。"""

    def _topological_sort(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """DAG 拓扑排序（Kahn 算法）。

        Args:
            steps: 步骤定义列表。

        Returns:
            list[dict] | None: 拓扑排序后的步骤列表，存在环时返回 None。
        """
        # 构建邻接表和入度表
        step_map: dict[str, dict[str, Any]] = {
            s.get("step_key", f"step_{i}"): s for i, s in enumerate(steps)
        }
        in_degree: dict[str, int] = dict.fromkeys(step_map, 0)
        adjacency: dict[str, list[str]] = {k: [] for k in step_map}

        for step in steps:
            key = step.get("step_key", "")
            deps = step.get("dependencies", [])
            for dep in deps:
                if dep in step_map:
                    adjacency[dep].append(key)
                    in_degree[key] += 1

        # Kahn 算法
        queue: deque[str] = deque(k for k, d in in_degree.items() if d == 0)
        sorted_keys: list[str] = []

        while queue:
            current = queue.popleft()
            sorted_keys.append(current)
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_keys) != len(step_map):
            # 存在环
            logger.error("DAG has cycles, cannot topological sort")
            return None

        return [step_map[k] for k in sorted_keys]

    async def _publish_event(
        self,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """发布 SSE 事件到 Redis pub/sub。

        Args:
            run_id: Run ID。
            event_type: 事件类型。
            payload: 事件载荷。
        """
        try:
            import redis as redis_lib

            redis_url = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")
            r = redis_lib.from_url(redis_url)
            channel = f"research:run:{run_id}:events"
            message = json.dumps(
                {"event": event_type, "data": json.dumps(payload, ensure_ascii=False)},
                ensure_ascii=False,
            )
            r.publish(channel, message)
        except Exception as exc:
            logger.warning("Failed to publish event: %s", exc)

    def _determine_final_status(
        self,
        steps: list[dict[str, Any]],
        succeeded: set[str],
        failed: set[str],
    ) -> str:
        """确定 Run 最终状态。

        Args:
            steps: 步骤定义列表。
            succeeded: 成功步骤 key 集合。
            failed: 失败步骤 key 集合。

        Returns:
            str: 最终状态（succeeded / partially_succeeded / failed）。
        """
        if not failed:
            return "succeeded"
        if succeeded:
            return "partially_succeeded"
        return "failed"

    def _aggregate_coverage(self, declarations: list[dict[str, Any]]) -> dict[str, Any]:
        """聚合覆盖率声明。

        Args:
            declarations: 各步骤的覆盖声明列表。

        Returns:
            dict: 聚合后的覆盖声明。
        """
        if not declarations:
            return CoverageDeclaration(
                analysis_mode="mixed",
                data_coverage_rate=0.0,
                llm_read_rate=0.0,
                is_sampled=False,
                mode_reason="无覆盖数据",
            ).to_dict()

        total = len(declarations)
        avg_data_rate = sum(d.get("data_coverage_rate", 0.0) for d in declarations) / total
        avg_llm_rate = sum(d.get("llm_read_rate", 0.0) for d in declarations) / total
        any_sampled = any(d.get("is_sampled", False) for d in declarations)

        return CoverageDeclaration(
            analysis_mode="mixed",
            data_coverage_rate=avg_data_rate,
            llm_read_rate=avg_llm_rate,
            is_sampled=any_sampled,
            mode_reason="聚合覆盖声明",
        ).to_dict()

    async def _fail_run(self, run_id: UUID, error_msg: str) -> None:
        """标记 Run 为 failed。

        Args:
            run_id: Run ID。
            error_msg: 错误消息。
        """
        if self._factory is None:
            return
        async with self._factory() as session:
            await self._repo.update_run_status(
                session,
                run_id,
                "failed",
                completed_at=datetime.now(UTC),
                error_summary=error_msg,
            )
        await self._publish_event(
            run_id, "run.status_changed", {"status": "failed", "error": error_msg}
        )
