"""研究成果包发布逻辑：_PublishMixin + PublicationService 组装。

PublicationService 编排完整发布流程：
- 成果包组装与发布（创建 ResearchResult + ResearchResultVersion v1）
- 发布新版本（旧版本标记 superseded，创建新版本）
- 发布预览（权限包络计算，不创建数据）

PublicationService 在本模块由各功能域 mixin 组装而成：
- _PublishMixin（本模块）：发布与发布预览
- _AclMixin（acl.py）：ACL 修改
- _RevisionMixin（revision.py）：版本管理 / 详情查询
- _ReuseMixin（reuse.py）：内部对象引用 / 复用 / 收藏
共享的实例属性与辅助方法集中在 _base._PublicationBase。
"""

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.dtos import (
    ProductRefCollection,
    PublishPreviewResult,
    PublishRequest,
    ResultVersionRef,
)
from packages.research.execution.envelope import PermissionEnvelopeCalculator
from packages.research.publication._base import _PublicationBase
from packages.research.publication.acl import _AclMixin
from packages.research.publication.reuse import _ReuseMixin
from packages.research.publication.revision import _RevisionMixin
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.publication")


class _PublishMixin(_PublicationBase):
    """成果包发布相关方法 mixin。"""

    # ============================================================
    # 发布
    # ============================================================

    async def publish_result(
        self,
        workspace_id: UUID,
        request: PublishRequest,
    ) -> ResultVersionRef:
        """组装并发布研究成果包。

        流程（PRD 6.9 节 / 架构 4.1 节）：
        1. 校验选定产物全部属于该 Workspace 且 status=confirmed
        2. 校验至少包含一个 Dataset 或 View
        3. 收集 Evidence Snapshot ID 和 Analysis Run ID（去重）
        4. 校验 Analysis Run 状态（succeeded / partially_succeeded）
        5. 计算权限包络 → 校验 requested_acl
        6. 计算内容哈希
        7. 创建 ResearchResult（stable identity）
        8. 创建 ResearchResultVersion v1（不可变）
        9. 创建 ResultAclRevision #1（记录初始 ACL）
        10. 创建 ResearchLineageEdge 记录
        11. 更新 ResearchResult.current_version=1
        12. 审计
        13. 返回 ResultVersionRef

        Args:
            workspace_id: 工作空间 ID。
            request: 发布请求。

        Returns:
            ResultVersionRef: 版本引用。

        Raises:
            AppError: 校验失败时抛出。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 收集产物引用 + 校验
            product_refs = await self._collect_product_refs(session, workspace_id, request)

            # 2. 校验至少包含一个 Dataset 或 View
            if not product_refs.dataset_version_refs and not product_refs.view_version_refs:
                raise AppError(
                    code="validation_failed",
                    message="成果包至少需要包含一个数据集或图表",
                    retryable=False,
                    fields={},
                )

            # 3. 校验 Analysis Run 状态
            await self._validate_run_statuses(session, product_refs)

            # 4. 计算权限包络
            source_snapshot_ids = [UUID(sid) for sid in product_refs.evidence_snapshot_ids if sid]
            envelope = await PermissionEnvelopeCalculator.calculate_envelope(
                source_snapshot_ids, session
            )

            # 5. 校验 requested_acl
            validation = PermissionEnvelopeCalculator.validate_requested_acl(
                request.requested_acl,
                request.explicit_user_ids,
                envelope,
            )
            if not validation.valid:
                if request.is_declassify and request.declassify_reason:
                    # declassify 操作：允许超出包络，但需记录审计
                    pass
                else:
                    raise AppError(
                        code="acl_exceeds_envelope",
                        message=(f"请求的 ACL 超出权限包络: {validation.reason}"),
                        retryable=False,
                        fields={
                            "requested_acl": request.requested_acl,
                            "effective_acl": validation.effective_acl,
                            "limiting_sources": validation.limiting_sources,
                        },
                    )

            # 6. 计算内容哈希
            content_hash = self._compute_content_hash(request, product_refs)

            # 7. 创建 ResearchResult
            result = await ResearchRepository.insert_result(
                session,
                workspace_id=workspace_id,
                owner_user_id=actor_id,
                name=request.title,
                status="published",
                current_version=0,
                current_acl_type=request.requested_acl,
                current_explicit_user_ids=[str(uid) for uid in request.explicit_user_ids],
            )

            # 8. 创建 ResearchResultVersion v1
            version_number = 1
            version = await ResearchRepository.insert_result_version(
                session,
                result_id=result.id,
                version_number=version_number,
                title=request.title,
                summary=request.summary or None,
                tags=request.tags,
                release_notes=request.release_notes or None,
                dataset_version_refs=product_refs.dataset_version_refs,
                view_version_refs=product_refs.view_version_refs,
                insight_version_refs=product_refs.insight_version_refs,
                evidence_snapshot_ids=product_refs.evidence_snapshot_ids,
                analysis_run_ids=product_refs.analysis_run_ids,
                source_run_statuses=product_refs.source_run_statuses,
                publisher=actor_id,
                content_hash=content_hash,
                published_permission_envelope={
                    "acl_type": envelope.acl_type,
                    "source_details": envelope.source_details,
                },
                status="active",
            )

            # 9. 创建初始 ACL Revision
            explicit_ids_str = [str(uid) for uid in request.explicit_user_ids]
            await ResearchRepository.insert_acl_revision(
                session,
                result_id=result.id,
                revision_number=1,
                acl_type=request.requested_acl,
                explicit_user_ids=explicit_ids_str,
                previous_acl_type=None,
                previous_explicit_user_ids=None,
                changed_by=actor_id,
                change_reason="初始发布",
                is_declassify=request.is_declassify,
                declassify_reason=request.declassify_reason or None,
            )

            # 10. 创建溯源边
            await self._lineage_service.record_publication_edges(
                session,
                result_id=result.id,
                version_number=version_number,
                workspace_id=workspace_id,
                product_refs=product_refs,
            )

            # 11. 更新 ResearchResult
            await ResearchRepository.update_result_current_version(
                session, result.id, version_number
            )

            # 12. 审计
            audit_action = (
                "research.result.declassify" if request.is_declassify else "research.result.publish"
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action=audit_action,
                    actor_user_id=actor_id,
                    resource_type="research_result_version",
                    resource_id=version.id,
                    payload={
                        "result_id": str(result.id),
                        "version_number": version_number,
                        "title": request.title,
                        "content_hash": content_hash[:16],
                    },
                ),
            )

            return ResultVersionRef(
                result_id=result.id,
                version_number=version_number,
                title=request.title,
                status="active",
                published_at=version.published_at,
            )

    async def publish_new_version(
        self,
        result_id: UUID,
        workspace_id: UUID,
        request: PublishRequest,
    ) -> ResultVersionRef:
        """发布新版本。

        1. 获取 ResearchResult（校验归属和状态）
        2. 校验选定产物（同 publish_result）
        3. 计算权限包络（重新校验当前源数据权限）
        4. 校验 requested_acl（同上）
        5. 计算内容哈希
        6. 标记旧版本为 superseded
        7. 创建 ResearchResultVersion (version_number+1)
        8. 创建溯源边记录
        9. 更新 ResearchResult.current_version
        10. 审计

        Args:
            result_id: 成果包 ID。
            workspace_id: 工作空间 ID。
            request: 发布请求。

        Returns:
            ResultVersionRef: 版本引用。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 获取成果包
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if result.owner_user_id != actor_id:
                raise AppError(
                    code="forbidden",
                    message="只有成果包所有者可以发布新版本",
                    retryable=False,
                    fields={},
                )

            # 2. 收集产物引用 + 校验
            product_refs = await self._collect_product_refs(session, workspace_id, request)

            # 3. 校验至少包含一个 Dataset 或 View
            if not product_refs.dataset_version_refs and not product_refs.view_version_refs:
                raise AppError(
                    code="validation_failed",
                    message="成果包至少需要包含一个数据集或图表",
                    retryable=False,
                    fields={},
                )

            # 4. 校验 Run 状态
            await self._validate_run_statuses(session, product_refs)

            # 5. 计算权限包络
            source_snapshot_ids = [UUID(sid) for sid in product_refs.evidence_snapshot_ids if sid]
            envelope = await PermissionEnvelopeCalculator.calculate_envelope(
                source_snapshot_ids, session
            )

            # 6. 校验 ACL
            validation = PermissionEnvelopeCalculator.validate_requested_acl(
                request.requested_acl,
                request.explicit_user_ids,
                envelope,
            )
            if not validation.valid:
                if not (request.is_declassify and request.declassify_reason):
                    raise AppError(
                        code="acl_exceeds_envelope",
                        message=f"请求的 ACL 超出权限包络: {validation.reason}",
                        retryable=False,
                        fields={
                            "requested_acl": request.requested_acl,
                            "effective_acl": validation.effective_acl,
                        },
                    )

            # 7. 计算内容哈希
            content_hash = self._compute_content_hash(request, product_refs)

            # 8. 标记旧版本为 superseded
            old_version = await ResearchRepository.get_latest_result_version(session, result_id)
            if old_version is not None and old_version.status == "active":
                await ResearchRepository.update_result_version_status(
                    session, old_version.id, "superseded"
                )

            # 9. 创建新版本
            version_number = result.current_version + 1
            version = await ResearchRepository.insert_result_version(
                session,
                result_id=result_id,
                version_number=version_number,
                title=request.title,
                summary=request.summary or None,
                tags=request.tags,
                release_notes=request.release_notes or None,
                dataset_version_refs=product_refs.dataset_version_refs,
                view_version_refs=product_refs.view_version_refs,
                insight_version_refs=product_refs.insight_version_refs,
                evidence_snapshot_ids=product_refs.evidence_snapshot_ids,
                analysis_run_ids=product_refs.analysis_run_ids,
                source_run_statuses=product_refs.source_run_statuses,
                publisher=actor_id,
                content_hash=content_hash,
                published_permission_envelope={
                    "acl_type": envelope.acl_type,
                    "source_details": envelope.source_details,
                },
                status="active",
            )

            # 10. 创建溯源边
            await self._lineage_service.record_publication_edges(
                session,
                result_id=result_id,
                version_number=version_number,
                workspace_id=workspace_id,
                product_refs=product_refs,
            )

            # 11. 更新 Result
            await ResearchRepository.update_result_current_version(
                session, result_id, version_number
            )

            # 12. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.result.new_version",
                    actor_user_id=actor_id,
                    resource_type="research_result_version",
                    resource_id=version.id,
                    payload={
                        "result_id": str(result_id),
                        "version_number": version_number,
                    },
                ),
            )

            return ResultVersionRef(
                result_id=result_id,
                version_number=version_number,
                title=request.title,
                status="active",
                published_at=version.published_at,
            )

    async def preview_publish(
        self,
        workspace_id: UUID,
        request: PublishRequest,
    ) -> PublishPreviewResult:
        """发布预览（权限包络计算，不创建数据）。

        Args:
            workspace_id: 工作空间 ID。
            request: 发布请求。

        Returns:
            PublishPreviewResult: 预览结果。
        """
        self._require_actor()
        async with self._scoped_session() as session:
            product_refs = await self._collect_product_refs(session, workspace_id, request)
            source_snapshot_ids = [UUID(sid) for sid in product_refs.evidence_snapshot_ids if sid]
            envelope = await PermissionEnvelopeCalculator.calculate_envelope(
                source_snapshot_ids, session
            )
            validation = PermissionEnvelopeCalculator.validate_requested_acl(
                request.requested_acl,
                request.explicit_user_ids,
                envelope,
            )
            return PublishPreviewResult(
                product_refs=product_refs,
                envelope=envelope,
                validation=validation,
            )

    # ============================================================
    # 发布辅助方法
    # ============================================================

    async def _collect_product_refs(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        request: PublishRequest,
    ) -> ProductRefCollection:
        """收集产物引用并校验。

        校验产物全部属于该 Workspace 且 status=confirmed。
        从产物来源收集 Evidence Snapshot ID 和 Analysis Run ID（去重）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            request: 发布请求。

        Returns:
            ProductRefCollection: 产物引用集合。
        """
        dataset_version_refs: list[dict[str, Any]] = []
        view_version_refs: list[dict[str, Any]] = []
        insight_version_refs: list[dict[str, Any]] = []
        snapshot_ids: set[str] = set()
        run_ids: set[str] = set()
        run_statuses: dict[str, str] = {}

        # 收集 DerivedDataset 引用
        for dataset_id in request.dataset_ids:
            dataset = await ResearchRepository.get_dataset(session, dataset_id, workspace_id)
            if dataset is None:
                raise AppError(
                    code="validation_failed",
                    message=f"数据集 {dataset_id} 不存在或不属于该工作空间",
                    retryable=False,
                    fields={"dataset_id": str(dataset_id)},
                )
            if dataset.status != "confirmed":
                raise AppError(
                    code="validation_failed",
                    message=f"数据集 {dataset_id} 状态不是 confirmed",
                    retryable=False,
                    fields={"dataset_id": str(dataset_id)},
                )

            version = await ResearchRepository.get_latest_dataset_version(session, dataset_id)
            if version is None:
                raise AppError(
                    code="validation_failed",
                    message=f"数据集 {dataset_id} 无可用版本",
                    retryable=False,
                    fields={"dataset_id": str(dataset_id)},
                )

            dataset_version_refs.append(
                {
                    "dataset_id": str(dataset_id),
                    "version_number": version.version_number,
                    "content_hash": version.content_hash,
                    "name": dataset.name,
                }
            )

            # 收集 snapshot 和 run
            if dataset.source_snapshot_id is not None:
                snapshot_ids.add(str(dataset.source_snapshot_id))
            run_ids.add(str(dataset.source_run_id))
            # 获取 run 状态
            run_status = await self._get_run_status(session, dataset.source_run_id)
            if run_status is not None:
                run_statuses[str(dataset.source_run_id)] = run_status

        # 收集 ResearchView 引用
        for view_id in request.view_ids:
            view = await ResearchRepository.get_view(session, view_id, workspace_id)
            if view is None:
                raise AppError(
                    code="validation_failed",
                    message=f"视图 {view_id} 不存在或不属于该工作空间",
                    retryable=False,
                    fields={"view_id": str(view_id)},
                )
            if view.status != "confirmed":
                raise AppError(
                    code="validation_failed",
                    message=f"视图 {view_id} 状态不是 confirmed",
                    retryable=False,
                    fields={"view_id": str(view_id)},
                )

            view_version = await ResearchRepository.get_view_version(
                session, view_id, view.current_version
            )
            if view_version is None:
                # 尝试获取最新版本
                view_versions = await ResearchRepository.list_view_versions(session, view_id)
                if not view_versions:
                    raise AppError(
                        code="validation_failed",
                        message=f"视图 {view_id} 无可用版本",
                        retryable=False,
                        fields={"view_id": str(view_id)},
                    )
                view_version = view_versions[0]

            view_version_refs.append(
                {
                    "view_id": str(view_id),
                    "version_number": view_version.version_number,
                    "image_content_hash": view_version.image_content_hash,
                    "name": view.name,
                }
            )

            run_ids.add(str(view.source_run_id))
            run_status = await self._get_run_status(session, view.source_run_id)
            if run_status is not None:
                run_statuses[str(view.source_run_id)] = run_status

        # 收集 Insight 引用
        for insight_id in request.insight_ids:
            insight = await ResearchRepository.get_insight(session, insight_id, workspace_id)
            if insight is None:
                raise AppError(
                    code="validation_failed",
                    message=f"Insight {insight_id} 不存在或不属于该工作空间",
                    retryable=False,
                    fields={"insight_id": str(insight_id)},
                )
            if insight.status != "confirmed":
                raise AppError(
                    code="validation_failed",
                    message=f"Insight {insight_id} 状态不是 confirmed",
                    retryable=False,
                    fields={"insight_id": str(insight_id)},
                )

            insight_version = await ResearchRepository.get_latest_insight_version(
                session, insight_id
            )
            if insight_version is None:
                raise AppError(
                    code="validation_failed",
                    message=f"Insight {insight_id} 无可用版本",
                    retryable=False,
                    fields={"insight_id": str(insight_id)},
                )

            insight_version_refs.append(
                {
                    "insight_id": str(insight_id),
                    "version_number": insight_version.version_number,
                    "name": insight.name,
                    "content_hash": hashlib.sha256(
                        json.dumps(
                            {
                                "conclusion": insight_version.conclusion,
                                "scope": insight_version.scope,
                                "evidence_refs": insight_version.evidence_refs,
                                "method_refs": insight_version.method_refs,
                                "confidence_level": insight_version.confidence_level,
                                "limitations": insight_version.limitations,
                                "evidence_source_label": insight_version.evidence_source_label,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            )

            if insight.source_run_id is not None:
                run_ids.add(str(insight.source_run_id))
                run_status = await self._get_run_status(session, insight.source_run_id)
                if run_status is not None:
                    run_statuses[str(insight.source_run_id)] = run_status

        return ProductRefCollection(
            dataset_version_refs=dataset_version_refs,
            view_version_refs=view_version_refs,
            insight_version_refs=insight_version_refs,
            evidence_snapshot_ids=list(snapshot_ids),
            analysis_run_ids=list(run_ids),
            source_run_statuses=run_statuses,
        )

    async def _validate_run_statuses(
        self,
        session: AsyncSession,
        product_refs: ProductRefCollection,
    ) -> None:
        """校验 Analysis Run 状态。

        校验所有产物的 source_run_id 对应的 Run 状态为 succeeded 或 partially_succeeded。
        cancelled 或 failed Run 的产物不允许发布。
        partially_succeeded 标注在 source_run_statuses 中。

        Args:
            session: 异步会话。
            product_refs: 产物引用集合。

        Raises:
            AppError: Run 状态校验失败时抛出。
        """
        for run_id_str, status in product_refs.source_run_statuses.items():
            if status not in ("succeeded", "partially_succeeded"):
                raise AppError(
                    code="validation_failed",
                    message=(
                        f"来源 Run {run_id_str} 状态为 {status}，"
                        f"不允许发布（仅 succeeded/partially_succeeded 可发布）"
                    ),
                    retryable=False,
                    fields={
                        "run_id": run_id_str,
                        "run_status": status,
                    },
                )

    async def _get_run_status(
        self,
        session: AsyncSession,
        run_id: UUID,
    ) -> str | None:
        """获取 Analysis Run 的状态。

        通过查询 research_analysis_run 表获取状态。
        使用 duck typing 避免 ORM 循环导入。

        Args:
            session: 异步会话。
            run_id: Run ID。

        Returns:
            str | None: Run 状态，不存在时返回 None。
        """
        try:
            from packages.research.execution.entities_trusted import ResearchAnalysisRun

            res = await session.execute(
                sa.select(ResearchAnalysisRun.status).where(ResearchAnalysisRun.id == run_id)
            )
            row = res.first()
            return row[0] if row else None
        except ImportError:
            # entities_trusted 不可用时使用原生 SQL
            res = await session.execute(
                sa.text("SELECT status FROM research_analysis_run WHERE id = :rid").bindparams(
                    rid=run_id
                ),
            )
            row = res.first()
            return row[0] if row else None

    def _compute_content_hash(
        self,
        request: PublishRequest,
        product_refs: ProductRefCollection,
    ) -> str:
        """计算内容哈希（SHA-256）。

        包含标题+摘要+标签+发布说明+所有产物版本 ID 及其 content_hash 的排序拼接。

        Args:
            request: 发布请求。
            product_refs: 产物引用集合。

        Returns:
            str: 64 字符十六进制 SHA-256 哈希。
        """
        entries: list[dict[str, Any]] = []

        # 元数据
        entries.append({"type": "title", "value": request.title})
        entries.append({"type": "summary", "value": request.summary})
        entries.append({"type": "tags", "value": sorted(request.tags)})
        entries.append({"type": "release_notes", "value": request.release_notes})

        # Dataset 版本引用
        for ref in product_refs.dataset_version_refs:
            entries.append(
                {
                    "type": "dataset_version",
                    "dataset_id": ref.get("dataset_id", ""),
                    "version_number": ref.get("version_number", 0),
                    "content_hash": ref.get("content_hash", ""),
                }
            )

        # View 版本引用
        for ref in product_refs.view_version_refs:
            entries.append(
                {
                    "type": "view_version",
                    "view_id": ref.get("view_id", ""),
                    "version_number": ref.get("version_number", 0),
                    "content_hash": ref.get("image_content_hash", ""),
                }
            )

        # Insight 版本引用
        for ref in product_refs.insight_version_refs:
            entries.append(
                {
                    "type": "insight_version",
                    "insight_id": ref.get("insight_id", ""),
                    "version_number": ref.get("version_number", 0),
                    "content_hash": ref.get("content_hash", ""),
                }
            )

        # 排序后序列化
        json_bytes = json.dumps(
            entries,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(json_bytes).hexdigest()


class PublicationService(_PublishMixin, _AclMixin, _RevisionMixin, _ReuseMixin):
    """研究成果包生命周期管理服务。

    编排完整发布流程：成果包组装与发布、新版本、ACL 修改、撤回、元数据编辑、
    详情/版本/ACL 历史、内部对象独立引用、复用（加入 Workspace / 基于此成果新建
    Workspace）、收藏/取消收藏。

    本类由功能域 mixin 组装（发布 / ACL / 版本 / 复用），共享成员集中在
    ``_PublicationBase``。构造函数注入会话工厂、部门 ID、操作人 ID、
    ProductService 与 LineageEdgeService。
    """
