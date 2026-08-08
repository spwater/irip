"""上下文构建 Mixin：范围检测、输入包与快照数据加载。

拆分自 orchestrator.py（IRIP 拆分任务）。``ContextBuilderMixin`` 承载
范围越界检测（_check_scope）、受控输入包生成（_prepare_input_package）、
快照数据文本加载（_load_snapshot_data）以及研究上下文构建
（_build_research_context）。
"""

import asyncio
import json
import os
import tempfile
from typing import Any
from uuid import UUID

from packages.research.execution.models_trusted import ScopeBoundary, ScopeCheckResult
from packages.research.execution.orchestrator_base import ResearchOrchestratorBase, logger


class ContextBuilderMixin(ResearchOrchestratorBase):
    """上下文构建功能域：范围检测 + 输入包 + 快照数据 + 研究上下文。"""

    def _check_scope(
        self,
        scope: ScopeBoundary,
        current_snapshot_id: UUID,
        current_question_version: int,
        current_method: str,
        current_resource_tier: str,
    ) -> ScopeCheckResult:
        """检查范围越界。

        越界检测：
        - snapshot_id 变更 → 新增数据 → 重新确认
        - question_version 变更 → 改变研究目标 → 重新确认
        - method="knowledge" 且 knowledge_base_used=False → 首次知识库 → 重新确认
        - resource_tier > "standard" → 扩大资源级别 → 重新确认

        Args:
            scope: 计划范围边界。
            current_snapshot_id: 当前快照 ID。
            current_question_version: 当前问题版本号。
            current_method: 当前步骤方法。
            current_resource_tier: 当前资源档位。

        Returns:
            ScopeCheckResult: 范围检查结果。
        """
        if current_snapshot_id != scope.snapshot_id:
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="snapshot_changed",
                message="证据快照已变更，需重新确认计划",
            )

        if current_question_version != scope.question_version:
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="question_changed",
                message="研究问题已变更，需重新确认计划",
            )

        if current_method == "knowledge" and not scope.knowledge_base_used:
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="knowledge_first_use",
                message="首次使用知识库，需重新确认计划",
            )

        _TIER_ORDER = {"standard": 0, "heavy": 1}
        if _TIER_ORDER.get(current_resource_tier, 0) > _TIER_ORDER.get(scope.resource_tier, 0):
            return ScopeCheckResult(
                is_within_scope=False,
                violation_type="resource_upgraded",
                message="资源级别已升级，需重新确认计划",
            )

        return ScopeCheckResult(is_within_scope=True)

    async def _prepare_input_package(self, snapshot_id: UUID) -> str:
        """生成受控输入包（沙箱只读挂载）。

        从 CoreFactProvider 获取快照数据 → 序列化为 JSON → 写入临时目录。

        Args:
            snapshot_id: 快照 ID。

        Returns:
            str: 输入包文件路径。
        """
        # 创建临时目录
        tmp_dir = tempfile.mkdtemp(prefix=f"research_input_{snapshot_id}_")
        input_path = os.path.join(tmp_dir, "evidence.json")

        # 从数据库加载快照数据
        input_data: dict[str, Any] = {"snapshot_id": str(snapshot_id), "evidence": []}

        if self._factory is not None:
            async with self._factory() as session:
                from packages.research.repository import ResearchRepository

                await ResearchRepository.list_snapshots(session, UUID(int=0))
                # 获取快照关联的证据引用
                # 此处简化：实际需要通过 CoreFactProvider 获取数据
                # 构建输入包结构
                input_data["evidence"] = [
                    {
                        "source_namespace": "core:fact",
                        "source_id": "placeholder",
                        "field_manifest": [],
                        "data": {"metadata": {}, "points": [], "series": []},
                    }
                ]

        # 写入 JSON 文件
        def _write_input_file() -> None:
            with open(input_path, "w", encoding="utf-8") as f:
                json.dump(input_data, f, ensure_ascii=False, indent=2)

        await asyncio.to_thread(_write_input_file)

        return tmp_dir

    async def _load_snapshot_data(self, snapshot_id: UUID) -> str:
        """加载快照数据为文本（LLM 步骤使用）。

        Args:
            snapshot_id: 快照 ID。

        Returns:
            str: 数据文本。
        """
        if self._factory is None:
            return ""

        async with self._factory() as session:
            # 获取快照的字段清单和源引用
            # 简化：返回字段清单的 JSON 文本
            import sqlalchemy as sa

            from packages.research.entities import ResearchEvidenceSnapshot

            result = await session.execute(
                sa.select(ResearchEvidenceSnapshot).where(
                    ResearchEvidenceSnapshot.id == snapshot_id
                )
            )
            snapshot = result.scalar_one_or_none()
            if snapshot is None:
                return ""

            # 读取每个证据引用的 Fact 完整数据
            evidence_data = []
            for ref in snapshot.source_refs or []:
                fact_id = ref.get("id")
                namespace = ref.get("namespace", "")
                if namespace == "core:fact" and fact_id:
                    try:
                        # 通过 factory 创建 CoreFactProvider 并读取数据
                        from apps.api.main import _build_s3_repo
                        from packages.research.lineage.core_adapter import CoreFactProviderImpl

                        s3_repo = _build_s3_repo()
                        provider = CoreFactProviderImpl(  # type: ignore[call-arg]
                            session_factory=self._factory,
                            s3_repo=s3_repo,
                        )
                        fact_data = await provider.get_fact_data(UUID(fact_id))
                        evidence_data.append(
                            {
                                "fact_id": fact_id,
                                "namespace": namespace,
                                "data": fact_data,
                            }
                        )
                    except Exception as exc:
                        logger.warning("Failed to load fact data %s: %s", fact_id, exc)
                        evidence_data.append(
                            {
                                "fact_id": fact_id,
                                "namespace": namespace,
                                "error": str(exc)[:200],
                            }
                        )

            return json.dumps(
                {
                    "field_manifest": snapshot.field_manifest,
                    "source_refs": snapshot.source_refs,
                    "evidence_data": evidence_data,
                },
                ensure_ascii=False,
            )

    def _build_research_context(self, run_id: UUID, plan: object) -> str:
        """构建研究上下文（主问题 + 计划 + 已完成步骤摘要）。

        Args:
            run_id: Run ID。
            plan: 计划版本 ORM。

        Returns:
            str: 研究上下文文本。
        """
        parts: list[str] = []

        # 从计划中提取 DAG 步骤摘要
        if plan is not None and hasattr(plan, "dag_structure"):
            dag = plan.dag_structure
            steps = dag.get("steps", []) if isinstance(dag, dict) else []
            for s in steps:
                step_key = s.get("step_key", "")
                question = s.get("question", "")
                parts.append(f"步骤 {step_key}: {question}")

        return "\n".join(parts) if parts else ""
