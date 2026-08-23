"""工件关联 Mixin：Insight 生命周期 + 产物列表。

拆分自 products.py（IRIP 拆分任务）。``ArtifactLinkMixin`` 承载
从 InsightCandidate 接受/修改创建 Insight、Insight 列表/详情/元数据编辑/
版本历史/删除，以及按类型分组的产物列表（list_products）。

核心不变量：
1. 版本实体创建后不可变；
2. 编辑 API 仅接受 stable identity 元数据字段；
3. 所有写操作产生审计记录。
"""

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.dtos import (
    InsightDetail,
    InsightRef,
    InsightVersionRef,
    ProductSummary,
)
from packages.research.products.product_base import ProductServiceBase
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.products")


class ArtifactLinkMixin(ProductServiceBase):
    """工件关联功能域：Insight CRUD + 产物列表。"""

    async def create_insight_from_accept(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> InsightRef:
        """接受候选 → 创建 Insight + v1（is_modified=false，保留 AI 原稿）。

        流程：
        1. 获取 InsightCandidate（校验 status=pending）
        2. 创建 ResearchInsight（stable identity, name=conclusion 摘要）
        3. 创建 ResearchInsightVersion v1（is_modified=false,
           ai_original_text=candidate.ai_raw_text）
        4. 更新候选 status=accepted, accepted_insight_id, reviewed_at, reviewed_by
        5. 审计

        Args:
            workspace_id: 工作空间 ID。
            candidate_id: 候选 ID。

        Returns:
            InsightRef: Insight 引用。

        Raises:
            AppError: code="not_found"，当候选不存在时。
            AppError: code="validation_failed"，当候选状态不为 pending 时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 获取候选并校验
            candidate = await ResearchRepository.get_insight_candidate(session, candidate_id)
            if candidate is None:
                raise AppError(
                    code="not_found",
                    message="Insight 候选不存在",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )
            if candidate.status != "pending":
                raise AppError(
                    code="validation_failed",
                    message=f"候选状态不为 pending: {candidate.status}",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )

            # 2. 创建稳定身份（name 使用 conclusion 前 50 字符）
            insight_name = candidate.conclusion[:50] + (
                "..." if len(candidate.conclusion) > 50 else ""
            )
            insight = await ResearchRepository.insert_insight(
                session,
                workspace_id=workspace_id,
                owner_user_id=actor_id,
                name=insight_name,
                status="confirmed",
                source_run_id=candidate.run_id,
            )

            # 3. 创建 v1 不可变版本（is_modified=false）
            await ResearchRepository.insert_insight_version(
                session,
                insight_id=insight.id,
                version_number=1,
                conclusion=candidate.conclusion,
                scope=candidate.scope,
                evidence_refs=candidate.evidence_refs,
                method_refs=candidate.method_refs,
                confidence_level=candidate.confidence_level,
                limitations=candidate.limitations,
                evidence_source_label=candidate.evidence_source_label,
                ai_original_text=candidate.ai_raw_text,
                is_modified=False,
                modification_note=None,
                source_candidate_id=candidate_id,
                source_run_id=candidate.run_id,
                created_by=actor_id,
            )

            # 4. 更新候选状态
            await ResearchRepository.update_insight_candidate_status(
                session,
                candidate_id,
                "accepted",
                accepted_insight_id=insight.id,
                reviewed_by=actor_id,
            )

            # 5. 更新 Insight 当前版本号
            await ResearchRepository.update_insight_current_version(session, insight.id, 1)

            # 6. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.insight.create",
                    actor_user_id=actor_id,
                    resource_type="research_insight",
                    resource_id=insight.id,
                    payload={"candidate_id": str(candidate_id)},
                ),
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.insight_candidate.accept",
                    actor_user_id=actor_id,
                    resource_type="research_insight_candidate",
                    resource_id=candidate_id,
                    payload={"insight_id": str(insight.id)},
                ),
            )

            # 7. 清除 dag_structure 里的候选数据（避免刷新后重复显示）
            try:
                import json as _json

                _dag_result = await session.execute(
                    sa.text(
                        "SELECT id, dag_structure FROM research_analysis_plan_version "
                        "WHERE workspace_id = :wid ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"wid": str(workspace_id)},
                )
                row = _dag_result.fetchone()
                if row and row[1]:
                    plan_id, dag = row[0], row[1]
                    steps = dag.get("steps", []) if isinstance(dag, dict) else []
                    changed = False
                    for s in steps:
                        if s.get("insight_candidate"):
                            s.pop("insight_candidate", None)
                            changed = True
                        if s.get("insight_candidate_id"):
                            s.pop("insight_candidate_id", None)
                            changed = True
                        if s.get("insight_run_id"):
                            s.pop("insight_run_id", None)
                            changed = True
                    if changed:
                        await session.execute(
                            sa.text(
                                "UPDATE research_analysis_plan_version "
                                "SET dag_structure = :dag WHERE id = :pid"
                            ),
                            {"dag": _json.dumps(dag, ensure_ascii=False), "pid": str(plan_id)},
                        )
            except Exception as exc:
                logger.warning("Failed to clear insight candidate from dag_structure: %s", exc)

            result = InsightRef(
                insight_id=insight.id,
                name=insight_name,
                status="confirmed",
                current_version=1,
            )
            _hook_run_id = candidate.run_id
            _hook_insight_id = insight.id

        # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
        if self._lineage_writer is not None:
            try:
                await self._lineage_writer.on_product_confirmed(
                    _hook_run_id,
                    "research:insight",
                    _hook_insight_id,
                    "insight",
                )
            except Exception as exc:
                logger.warning("on_product_confirmed hook failed: %s", exc)

        return result

    async def create_insight_from_modify(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        modified_fields: dict[str, Any],
        modification_note: str,
    ) -> InsightRef:
        """修改候选 → 创建 Insight + v1（is_modified=true，保留 AI 原稿 + 修改记录）。

        流程：
        1. 获取 InsightCandidate（校验 status=pending）
        2. 创建 ResearchInsight（stable identity）
        3. 创建 ResearchInsightVersion v1（is_modified=true,
           ai_original_text, modification_note, 用户修改后的字段值）
        4. 更新候选 status=modified, accepted_insight_id, reviewed_at, reviewed_by
        5. 审计

        Args:
            workspace_id: 工作空间 ID。
            candidate_id: 候选 ID。
            modified_fields: 用户修改后的字段（可包含 conclusion/scope/evidence_refs/
                method_refs/confidence_level/limitations/evidence_source_label）。
            modification_note: 修改原因。

        Returns:
            InsightRef: Insight 引用。

        Raises:
            AppError: code="not_found"，当候选不存在时。
            AppError: code="validation_failed"，当候选状态不为 pending 或缺少修改原因时。
        """
        actor_id = self._require_actor()
        if not modification_note or not modification_note.strip():
            raise AppError(
                code="validation_failed",
                message="修改原因为必填",
                retryable=False,
                fields={},
            )

        async with self._scoped_session() as session:
            # 1. 获取候选并校验
            candidate = await ResearchRepository.get_insight_candidate(session, candidate_id)
            if candidate is None:
                raise AppError(
                    code="not_found",
                    message="Insight 候选不存在",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )
            if candidate.status != "pending":
                raise AppError(
                    code="validation_failed",
                    message=f"候选状态不为 pending: {candidate.status}",
                    retryable=False,
                    fields={"candidate_id": str(candidate_id)},
                )

            # 合并修改字段（用户修改覆盖候选原始值）
            conclusion = str(modified_fields.get("conclusion", candidate.conclusion))
            scope = str(modified_fields.get("scope", candidate.scope))
            evidence_refs = list(modified_fields.get("evidence_refs", candidate.evidence_refs))
            method_refs = list(modified_fields.get("method_refs", candidate.method_refs))
            confidence_level = str(
                modified_fields.get("confidence_level", candidate.confidence_level)
            )
            limitations = str(modified_fields.get("limitations", candidate.limitations))
            evidence_source_label = str(
                modified_fields.get("evidence_source_label", candidate.evidence_source_label)
            )

            # 2. 创建稳定身份
            insight_name = conclusion[:50] + ("..." if len(conclusion) > 50 else "")
            insight = await ResearchRepository.insert_insight(
                session,
                workspace_id=workspace_id,
                owner_user_id=actor_id,
                name=insight_name,
                status="confirmed",
                source_run_id=candidate.run_id,
            )

            # 3. 创建 v1 不可变版本（is_modified=true）
            await ResearchRepository.insert_insight_version(
                session,
                insight_id=insight.id,
                version_number=1,
                conclusion=conclusion,
                scope=scope,
                evidence_refs=evidence_refs,
                method_refs=method_refs,
                confidence_level=confidence_level,
                limitations=limitations,
                evidence_source_label=evidence_source_label,
                ai_original_text=candidate.ai_raw_text,
                is_modified=True,
                modification_note=modification_note,
                source_candidate_id=candidate_id,
                source_run_id=candidate.run_id,
                created_by=actor_id,
            )

            # 4. 更新候选状态
            await ResearchRepository.update_insight_candidate_status(
                session,
                candidate_id,
                "modified",
                accepted_insight_id=insight.id,
                reviewed_by=actor_id,
            )

            # 5. 更新 Insight 当前版本号
            await ResearchRepository.update_insight_current_version(session, insight.id, 1)

            # 6. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.insight.create",
                    actor_user_id=actor_id,
                    resource_type="research_insight",
                    resource_id=insight.id,
                    payload={"candidate_id": str(candidate_id), "modified": True},
                ),
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.insight_candidate.modify",
                    actor_user_id=actor_id,
                    resource_type="research_insight_candidate",
                    resource_id=candidate_id,
                    payload={
                        "insight_id": str(insight.id),
                        "modification_note": modification_note[:100],
                    },
                ),
            )

            result = InsightRef(
                insight_id=insight.id,
                name=insight_name,
                status="confirmed",
                current_version=1,
            )
            _hook_run_id = candidate.run_id
            _hook_insight_id = insight.id

        # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
        if self._lineage_writer is not None:
            try:
                await self._lineage_writer.on_product_confirmed(
                    _hook_run_id,
                    "research:insight",
                    _hook_insight_id,
                    "insight",
                )
            except Exception as exc:
                logger.warning("on_product_confirmed hook failed: %s", exc)

        return result

    async def list_insights(self, workspace_id: UUID) -> list[InsightRef]:
        """列出工作空间内的 Insight。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[InsightRef]: Insight 引用列表。
        """
        async with self._scoped_session() as session:
            insights = await ResearchRepository.list_insights(session, workspace_id)
            return [
                InsightRef(
                    insight_id=ins.id,
                    name=ins.name,
                    status=ins.status,
                    current_version=ins.current_version,
                )
                for ins in insights
            ]

    async def get_insight(
        self,
        workspace_id: UUID,
        insight_id: UUID,
    ) -> InsightDetail:
        """获取 Insight 详情（含当前版本结构化字段 + AI 原稿 + 修改记录）。

        Args:
            workspace_id: 工作空间 ID。
            insight_id: Insight ID。

        Returns:
            InsightDetail: Insight 详情。

        Raises:
            AppError: code="not_found"，当 Insight 不存在时。
        """
        async with self._scoped_session() as session:
            insight = await ResearchRepository.get_insight(session, insight_id, workspace_id)
            if insight is None:
                raise AppError(
                    code="not_found",
                    message="Insight 不存在",
                    retryable=False,
                    fields={"insight_id": str(insight_id)},
                )

            current_version_data: dict[str, Any] | None = None
            if insight.current_version > 0:
                version = await ResearchRepository.get_latest_insight_version(session, insight_id)
                if version is not None:
                    current_version_data = {
                        "version_id": str(version.id),
                        "version_number": version.version_number,
                        "conclusion": version.conclusion,
                        "scope": version.scope,
                        "evidence_refs": version.evidence_refs,
                        "method_refs": version.method_refs,
                        "confidence_level": version.confidence_level,
                        "limitations": version.limitations,
                        "evidence_source_label": version.evidence_source_label,
                        "ai_original_text": version.ai_original_text,
                        "is_modified": version.is_modified,
                        "modification_note": version.modification_note,
                        "created_at": version.created_at.isoformat()
                        if version.created_at
                        else None,
                    }

            return InsightDetail(
                insight_id=insight.id,
                workspace_id=workspace_id,
                name=insight.name,
                status=insight.status,
                current_version=insight.current_version,
                source_run_id=insight.source_run_id,
                current_version_data=current_version_data,
            )

    async def update_insight_metadata(
        self,
        workspace_id: UUID,
        insight_id: UUID,
        name: str,
    ) -> InsightRef:
        """编辑 Insight 元数据（仅 name）。

        Args:
            workspace_id: 工作空间 ID。
            insight_id: Insight ID。
            name: 新名称。

        Returns:
            InsightRef: 更新后的 Insight 引用。

        Raises:
            AppError: code="not_found"，当 Insight 不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            insight = await ResearchRepository.get_insight(session, insight_id, workspace_id)
            if insight is None:
                raise AppError(
                    code="not_found",
                    message="Insight 不存在",
                    retryable=False,
                    fields={"insight_id": str(insight_id)},
                )

            await ResearchRepository.update_insight_metadata(session, insight_id, name=name)

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.insight.edit",
                    actor_user_id=actor_id,
                    resource_type="research_insight",
                    resource_id=insight_id,
                    payload={"name": name},
                ),
            )

            return InsightRef(
                insight_id=insight.id,
                name=name,
                status=insight.status,
                current_version=insight.current_version,
            )

    async def delete_insight(
        self,
        workspace_id: UUID,
        insight_id: UUID,
    ) -> None:
        """删除 Insight（物理删除，连同版本）。

        Args:
            workspace_id: 工作空间 ID。
            insight_id: Insight ID。
        """
        self._require_actor()
        async with self._scoped_session() as session:
            # 删除版本
            await session.execute(
                sa.text("DELETE FROM research_insight_version WHERE insight_id = :iid"),
                {"iid": str(insight_id)},
            )
            # 删除 Insight
            await session.execute(
                sa.text("DELETE FROM research_insight WHERE id = :iid AND workspace_id = :wid"),
                {"iid": str(insight_id), "wid": str(workspace_id)},
            )

    async def list_insight_versions(
        self,
        workspace_id: UUID,
        insight_id: UUID,
    ) -> list[InsightVersionRef]:
        """列出 Insight 版本历史。

        Args:
            workspace_id: 工作空间 ID。
            insight_id: Insight ID。

        Returns:
            list[InsightVersionRef]: 版本引用列表。
        """
        async with self._scoped_session() as session:
            versions = await ResearchRepository.list_insight_versions(session, insight_id)
            return [
                InsightVersionRef(
                    version_id=v.id,
                    insight_id=insight_id,
                    version_number=v.version_number,
                    is_modified=v.is_modified,
                    created_at=v.created_at,
                )
                for v in versions
            ]

    async def list_products(self, workspace_id: UUID) -> list[ProductSummary]:
        """列出 Workspace 全部已确认产物（按类型分组）。

        聚合 DerivedDataset / ResearchView / Insight 三种产物。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[ProductSummary]: 产物列表（按类型分组）。
        """
        async with self._scoped_session() as session:
            products: list[ProductSummary] = []

            # DerivedDataset
            datasets = await ResearchRepository.list_datasets(session, workspace_id)
            for ds in datasets:
                products.append(
                    ProductSummary(
                        product_type="derived_dataset",
                        product_id=ds.id,
                        name=ds.name,
                        status=ds.status,
                        current_version=ds.current_version,
                    )
                )

            # ResearchView
            views = await ResearchRepository.list_views(session, workspace_id)
            for v in views:
                products.append(
                    ProductSummary(
                        product_type="view",
                        product_id=v.id,
                        name=v.name,
                        status=v.status,
                        current_version=v.current_version,
                    )
                )

            # Insight
            insights = await ResearchRepository.list_insights(session, workspace_id)
            for ins in insights:
                products.append(
                    ProductSummary(
                        product_type="insight",
                        product_id=ins.id,
                        name=ins.name,
                        status=ins.status,
                        current_version=ins.current_version,
                    )
                )

            return products
