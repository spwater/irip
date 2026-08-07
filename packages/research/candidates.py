"""候选产物识别服务：CandidateService。

CandidateService 负责：
- Run 完成后识别候选产物（data 工件 → 候选 DerivedDataset /
  chart 工件 → 候选 ResearchView / Insight 候选）
- 组装预览数据（三段式摘要 / 图表元数据 / Insight 结构化字段）
- 处理 Insight 候选拒绝

参照 PRD 6.8 节候选产物识别逻辑。
"""

import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.entities import ResearchInsightCandidate
from packages.research.models import (
    CandidateDetail,
    CandidateProductSummary,
    InsightCandidateRef,
)
from packages.research.repository import ResearchRepository
from packages.research.repository_trusted import ResearchRepositoryTrusted
from packages.research.validation import ThreeSegmentValidator

logger = logging.getLogger("research.candidates")


class CandidateService(ScopedSessionMixin):
    """候选产物识别服务。

    依赖注入 session_factory / department_id / actor_id / RunArtifactService。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _artifact_service: RunArtifactService（工件内容读取和下载）。
        _rls_dept_id: RLS 部门 ID（可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        artifact_service: object,
    ) -> None:
        """初始化候选产物服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            artifact_service: RunArtifactService 实例。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._artifact_service = artifact_service
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    async def identify_candidates(
        self,
        workspace_id: UUID,
        run_id: UUID,
    ) -> list[CandidateProductSummary]:
        """识别 Run 的全部候选产物。

        流程：
        1. 查询 research_run_artifact WHERE run_id=? AND is_publishable=true
        2. data 工件 → 下载内容 → ThreeSegmentValidator.validate()
           → 校验通过标记为候选 DerivedDataset，失败标记为不可用
        3. chart 工件 → 读取元数据 → 标记为候选 ResearchView
        4. 查询 research_insight_candidate WHERE run_id=? AND status='pending'
        5. 汇总返回候选列表

        Args:
            workspace_id: 工作空间 ID。
            run_id: Run ID。

        Returns:
            list[CandidateProductSummary]: 候选产物列表。
        """
        candidates: list[CandidateProductSummary] = []

        async with self._scoped_session() as session:
            # 1. 查询 publishable 工件
            artifacts = await ResearchRepositoryTrusted.list_artifacts_by_run(session, run_id)
            # 获取步骤信息用于展示
            steps = await ResearchRepositoryTrusted.list_steps_by_run(session, run_id)
            step_map: dict[UUID, object] = {s.id: s for s in steps}

            # 2. 识别 data 候选
            for artifact in artifacts:
                if not artifact.is_publishable:
                    continue
                if artifact.artifact_type == "data":
                    candidate = await self._identify_data_candidate(artifact, step_map)
                    candidates.append(candidate)
                elif artifact.artifact_type == "chart":
                    candidate = self._identify_chart_candidate(artifact, step_map)
                    candidates.append(candidate)

            # 3. 查询 Insight 候选
            insight_candidates = await ResearchRepository.list_insight_candidates(
                session, run_id, status="pending"
            )
            for ic in insight_candidates:
                step_name = ""
                step_status = ""
                if ic.step_id is not None and ic.step_id in step_map:
                    step = step_map[ic.step_id]
                    step_name = step.step_key
                    step_status = step.status
                candidates.append(
                    CandidateProductSummary(
                        candidate_type="insight",
                        source_artifact_id=None,
                        candidate_id=ic.id,
                        source_run_id=run_id,
                        source_step_id=ic.step_id,
                        step_name=step_name,
                        step_status=step_status,
                        preview_data={
                            "conclusion": ic.conclusion,
                            "scope": ic.scope,
                            "evidence_refs": ic.evidence_refs,
                            "method_refs": ic.method_refs,
                            "confidence_level": ic.confidence_level,
                            "limitations": ic.limitations,
                            "evidence_source_label": ic.evidence_source_label,
                            "ai_raw_text": ic.ai_raw_text[:500] if ic.ai_raw_text else "",
                            "status": ic.status,
                        },
                        status=ic.status,
                    )
                )

            return candidates

    async def _identify_data_candidate(
        self,
        artifact: object,
        step_map: dict[UUID, object],
    ) -> CandidateProductSummary:
        """识别 data 工件为候选 DerivedDataset。

        下载工件内容并校验三段式结构。

        Args:
            artifact: ResearchRunArtifact ORM 实体。
            step_map: 步骤映射。

        Returns:
            CandidateProductSummary: 候选摘要。
        """
        step_name = ""
        step_status = ""
        if artifact.step_id is not None and artifact.step_id in step_map:
            step = step_map[artifact.step_id]
            step_name = step.step_key
            step_status = step.status

        # 下载并校验工件内容
        try:
            artifact_content = await self._artifact_service.get_artifact(artifact.id)
            if artifact_content is None:
                return CandidateProductSummary(
                    candidate_type="derived_dataset",
                    source_artifact_id=artifact.id,
                    candidate_id=artifact.id,
                    source_run_id=artifact.run_id,
                    source_step_id=artifact.step_id,
                    step_name=step_name,
                    step_status=step_status,
                    preview_data={},
                    status="unavailable",
                    error_reason="无法下载工件内容",
                )

            result = ThreeSegmentValidator.validate(artifact_content.content)
            if not result.valid:
                return CandidateProductSummary(
                    candidate_type="derived_dataset",
                    source_artifact_id=artifact.id,
                    candidate_id=artifact.id,
                    source_run_id=artifact.run_id,
                    source_step_id=artifact.step_id,
                    step_name=step_name,
                    step_status=step_status,
                    preview_data={},
                    status="unavailable",
                    error_reason="; ".join(result.errors),
                )

            assert result.data is not None
            # 组装预览数据
            points_preview = [
                {"name": pt.get("name", ""), "value": pt.get("value"), "unit": pt.get("unit", "")}
                for pt in result.data.points[:10]
            ]
            series_preview = [
                {
                    "name": sr.get("name", ""),
                    "row_count": len(sr.get("rows", [])),
                    "column_count": len(sr.get("columns", []))
                    or (len(sr.get("rows", [{}])[0]) if sr.get("rows") else 0),
                    "columns": sr.get("columns", []),
                }
                for sr in result.data.series
            ]
            metadata_keys = (
                list(result.data.metadata.keys()) if isinstance(result.data.metadata, dict) else []
            )
            field_names = [fm.get("field_name", "") for fm in result.field_manifest]

            return CandidateProductSummary(
                candidate_type="derived_dataset",
                source_artifact_id=artifact.id,
                candidate_id=artifact.id,
                source_run_id=artifact.run_id,
                source_step_id=artifact.step_id,
                step_name=step_name,
                step_status=step_status,
                preview_data={
                    "metadata_keys": metadata_keys,
                    "metadata_preview": dict(list(result.data.metadata.items())[:5])
                    if isinstance(result.data.metadata, dict)
                    else {},
                    "points_preview": points_preview,
                    "points_count": len(result.data.points),
                    "series_preview": series_preview,
                    "series_count": len(result.data.series),
                    "field_manifest": result.field_manifest,
                    "field_names": field_names,
                },
                status="available",
            )
        except Exception as exc:
            logger.warning("Data candidate identification failed: %s", exc)
            return CandidateProductSummary(
                candidate_type="derived_dataset",
                source_artifact_id=artifact.id,
                candidate_id=artifact.id,
                source_run_id=artifact.run_id,
                source_step_id=artifact.step_id,
                step_name=step_name,
                step_status=step_status,
                preview_data={},
                status="unavailable",
                error_reason=f"识别异常: {str(exc)}",
            )

    def _identify_chart_candidate(
        self,
        artifact: object,
        step_map: dict[UUID, object],
    ) -> CandidateProductSummary:
        """识别 chart 工件为候选 ResearchView。

        读取工件元数据（格式、尺寸、content_hash）。

        Args:
            artifact: ResearchRunArtifact ORM 实体。
            step_map: 步骤映射。

        Returns:
            CandidateProductSummary: 候选摘要。
        """
        step_name = ""
        step_status = ""
        if artifact.step_id is not None and artifact.step_id in step_map:
            step = step_map[artifact.step_id]
            step_name = step.step_key
            step_status = step.status

        # 推断图片格式
        image_format = "png"
        if artifact.artifact_key.lower().endswith(".pdf"):
            image_format = "pdf"

        return CandidateProductSummary(
            candidate_type="view",
            source_artifact_id=artifact.id,
            candidate_id=artifact.id,
            source_run_id=artifact.run_id,
            source_step_id=artifact.step_id,
            step_name=step_name,
            step_status=step_status,
            preview_data={
                "artifact_key": artifact.artifact_key,
                "storage_path": artifact.storage_path,
                "image_format": image_format,
                "content_hash": artifact.content_hash or "",
                "size_bytes": artifact.size_bytes or 0,
            },
            status="available",
        )

    async def get_candidate_detail(
        self,
        workspace_id: UUID,
        run_id: UUID,
        candidate_id: UUID,
    ) -> CandidateDetail:
        """获取候选产物详情。

        Args:
            workspace_id: 工作空间 ID。
            run_id: Run ID。
            candidate_id: 候选 ID。

        Returns:
            CandidateDetail: 候选详情。

        Raises:
            AppError: code="not_found"，当候选不存在时。
        """
        async with self._scoped_session() as session:
            # 先检查是否为 Insight 候选
            candidate = await ResearchRepository.get_insight_candidate(session, candidate_id)
            if candidate is not None and candidate.run_id == run_id:
                return CandidateDetail(
                    candidate_type="insight",
                    candidate_id=candidate_id,
                    source_run_id=run_id,
                    source_step_id=candidate.step_id,
                    preview_data={
                        "conclusion": candidate.conclusion,
                        "scope": candidate.scope,
                        "evidence_refs": candidate.evidence_refs,
                        "method_refs": candidate.method_refs,
                        "confidence_level": candidate.confidence_level,
                        "limitations": candidate.limitations,
                        "evidence_source_label": candidate.evidence_source_label,
                        "ai_raw_text": candidate.ai_raw_text,
                        "status": candidate.status,
                    },
                )

            # 否则检查是否为工件候选
            artifact = await ResearchRepositoryTrusted.get_artifact(session, candidate_id)
            if artifact is not None and artifact.run_id == run_id:
                candidate_type = (
                    "derived_dataset"
                    if artifact.artifact_type == "data"
                    else "view"
                    if artifact.artifact_type == "chart"
                    else "unknown"
                )
                preview_data: dict = {
                    "artifact_key": artifact.artifact_key,
                    "storage_path": artifact.storage_path,
                    "content_hash": artifact.content_hash or "",
                    "is_publishable": artifact.is_publishable,
                }

                # 如果是 data 工件，尝试下载并校验
                if artifact.artifact_type == "data":
                    try:
                        artifact_content = await self._artifact_service.get_artifact(candidate_id)
                        if artifact_content is not None:
                            result = ThreeSegmentValidator.validate(artifact_content.content)
                            if result.valid and result.data is not None:
                                preview_data["metadata"] = result.data.metadata
                                preview_data["points"] = result.data.points
                                preview_data["series"] = result.data.series
                                preview_data["field_manifest"] = result.field_manifest
                    except Exception:
                        pass

                return CandidateDetail(
                    candidate_type=candidate_type,
                    candidate_id=candidate_id,
                    source_run_id=run_id,
                    source_step_id=artifact.step_id,
                    preview_data=preview_data,
                )

            raise AppError(
                code="not_found",
                message="候选产物不存在",
                retryable=False,
                fields={"candidate_id": str(candidate_id)},
            )

    async def reject_insight_candidate(
        self,
        workspace_id: UUID,
        run_id: UUID,
        candidate_id: UUID,
        reason: str | None = None,
    ) -> None:
        """拒绝 Insight 候选 → 物理删除 + 清除 dag_structure 中的候选信息。

        幂等：候选不存在时静默返回（不报错），因为期望的终态就是"不存在"。

        Args:
            workspace_id: 工作空间 ID。
            run_id: Run ID（用于审计日志）。
            candidate_id: 候选 ID。
            reason: 拒绝原因（可选）。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 物理删除候选（幂等：不存在则无操作）
            await session.execute(
                sa.delete(ResearchInsightCandidate).where(
                    ResearchInsightCandidate.id == candidate_id
                )
            )

            # 2. 清除 plan dag_structure 中的 insight_candidate 信息
            #    防止刷新页面后从 dag_structure 恢复已拒绝的候选
            from packages.research.entities_trusted import ResearchAnalysisPlanVersion

            plan_result = await session.execute(
                sa.select(ResearchAnalysisPlanVersion)
                .where(ResearchAnalysisPlanVersion.workspace_id == workspace_id)
                .order_by(ResearchAnalysisPlanVersion.created_at.desc())
                .limit(1)
            )
            plan = plan_result.scalar_one_or_none()
            if plan is not None and plan.dag_structure:
                dag = dict(plan.dag_structure)
                steps = list(dag.get("steps", []))
                changed = False
                for i, step in enumerate(steps):
                    if step.get("insight_candidate_id") == str(candidate_id):
                        steps[i] = {
                            k: v
                            for k, v in step.items()
                            if k
                            not in ("insight_candidate", "insight_candidate_id", "insight_run_id")
                        }
                        changed = True
                if changed:
                    dag["steps"] = steps
                    await session.execute(
                        sa.update(ResearchAnalysisPlanVersion)
                        .where(ResearchAnalysisPlanVersion.id == plan.id)
                        .values(dag_structure=dag)
                    )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.insight_candidate.reject",
                    actor_user_id=actor_id,
                    resource_type="research_insight_candidate",
                    resource_id=candidate_id,
                    payload={"reason": reason or ""},
                ),
            )

    async def list_insight_candidates(
        self,
        workspace_id: UUID,
        run_id: UUID,
    ) -> list[InsightCandidateRef]:
        """列出 Run 的 Insight 候选。

        Args:
            workspace_id: 工作空间 ID。
            run_id: Run ID。

        Returns:
            list[InsightCandidateRef]: 候选引用列表。
        """
        async with self._scoped_session() as session:
            candidates = await ResearchRepository.list_insight_candidates(session, run_id)
            return [
                InsightCandidateRef(
                    candidate_id=c.id,
                    run_id=run_id,
                    step_id=c.step_id,
                    status=c.status,
                    conclusion=c.conclusion,
                    evidence_source_label=c.evidence_source_label,
                    created_at=c.created_at,
                )
                for c in candidates
            ]
