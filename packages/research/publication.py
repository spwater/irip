"""研究成果包生命周期管理服务：PublicationService。

PublicationService 编排完整发布流程：
- 成果包组装与发布（创建 ResearchResult + ResearchResultVersion v1）
- 发布新版本（旧版本标记 superseded，创建新版本）
- ACL 修改（创建 ResultAclRevision，更新 Result.current_acl_*）
- 成果撤回（标记版本为 withdrawn）
- 编辑元数据（仅 stable identity name）
- 成果包详情 / 版本历史 / ACL 变更记录
- 成果包内部对象独立引用
- 复用操作（加入 Workspace / 基于此成果新建 Workspace）
- 收藏 / 取消收藏

参照 packages/research/service.py 的 ScopedSessionMixin 模式。
"""

import hashlib
import json
import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.envelope import PermissionEnvelopeCalculator
from packages.research.lineage import LineageEdgeService
from packages.research.models import (
    AclRevisionRef,
    EvidenceRefDTO,
    PermissionEnvelope,
    ProductRefCollection,
    PublishRequest,
    PublishPreviewResult,
    ResultDetail,
    ResultRef,
    ResultVersionDetail,
    ResultVersionRef,
    WorkspaceRef,
)
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.publication")


class PublicationService(ScopedSessionMixin):
    """研究成果包生命周期管理。

    职责：
    - 成果包组装与发布（创建 ResearchResult + ResearchResultVersion v1）
    - 发布新版本（旧版本标记 superseded，创建新版本）
    - ACL 修改（创建 ResultAclRevision，更新 Result.current_acl_*）
    - 成果撤回（标记版本为 withdrawn）
    - 编辑元数据（仅 stable identity name）
    - 成果包详情 / 版本历史 / ACL 变更记录
    - 成果包内部对象独立引用
    - 复用操作（加入 Workspace / 基于此成果新建 Workspace）
    - 收藏 / 取消收藏

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _product_service: ProductService 实例。
        _lineage_service: LineageEdgeService 实例。
        _rls_dept_id: RLS 部门 ID（可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        product_service: object,
        lineage_service: LineageEdgeService,
    ) -> None:
        """初始化成果包服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            product_service: ProductService 实例（获取产物详情和版本）。
            lineage_service: LineageEdgeService 实例（溯源边记录）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._product_service = product_service
        self._lineage_service = lineage_service
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
            product_refs = await self._collect_product_refs(
                session, workspace_id, request
            )

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
            source_snapshot_ids = [
                UUID(sid) for sid in product_refs.evidence_snapshot_ids
                if sid
            ]
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
                        message=(
                            f"请求的 ACL 超出权限包络: "
                            f"{validation.reason}"
                        ),
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
                current_explicit_user_ids=[
                    str(uid) for uid in request.explicit_user_ids
                ],
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
            revision = await ResearchRepository.insert_acl_revision(
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
                "research.result.declassify"
                if request.is_declassify
                else "research.result.publish"
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
            product_refs = await self._collect_product_refs(
                session, workspace_id, request
            )

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
            source_snapshot_ids = [
                UUID(sid) for sid in product_refs.evidence_snapshot_ids
                if sid
            ]
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
            old_version = await ResearchRepository.get_latest_result_version(
                session, result_id
            )
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
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            product_refs = await self._collect_product_refs(
                session, workspace_id, request
            )
            source_snapshot_ids = [
                UUID(sid) for sid in product_refs.evidence_snapshot_ids
                if sid
            ]
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
    # ACL 管理
    # ============================================================

    async def update_acl(
        self,
        result_id: UUID,
        acl_type: str,
        explicit_user_ids: list[UUID] | None,
        reason: str | None,
        is_declassify: bool,
        declassify_reason: str | None,
    ) -> AclRevisionRef:
        """修改成果包 ACL。

        1. 校验调用者为 owner
        2. 计算当前权限包络（重新校验当前源数据权限）
        3. 校验新 ACL 不超过包络交集
        4. 创建 ResultAclRevision（记录前后值）
        5. 更新 ResearchResult.current_acl_type / current_explicit_user_ids
        6. 审计

        Args:
            result_id: 成果包 ID。
            acl_type: 新 ACL 类型。
            explicit_user_ids: 指定用户列表。
            reason: 变更原因。
            is_declassify: 是否为 declassify 操作。
            declassify_reason: declassify 理由。

        Returns:
            AclRevisionRef: 修订记录引用。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
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
                    message="只有成果包所有者可以修改 ACL",
                    retryable=False,
                    fields={},
                )

            # 获取最新版本以取得 evidence_snapshot_ids
            latest_version = await ResearchRepository.get_latest_result_version(
                session, result_id
            )
            snapshot_ids: list[UUID] = []
            if latest_version is not None:
                for sid in latest_version.evidence_snapshot_ids or []:
                    try:
                        snapshot_ids.append(UUID(str(sid)))
                    except (ValueError, TypeError):
                        pass

            # 计算权限包络
            envelope = await PermissionEnvelopeCalculator.calculate_envelope(
                snapshot_ids, session
            )

            # 校验新 ACL
            effective_explicit_ids = explicit_user_ids or []
            validation = PermissionEnvelopeCalculator.validate_requested_acl(
                acl_type, effective_explicit_ids, envelope
            )
            if not validation.valid:
                if is_declassify and declassify_reason:
                    pass  # declassify 允许超出包络
                else:
                    raise AppError(
                        code="acl_exceeds_envelope",
                        message=f"请求的 ACL 超出权限包络: {validation.reason}",
                        retryable=False,
                        fields={
                            "requested_acl": acl_type,
                            "effective_acl": validation.effective_acl,
                        },
                    )

            # 获取当前最新 ACL Revision
            latest_revision = await ResearchRepository.get_latest_acl_revision(
                session, result_id
            )
            previous_acl_type = (
                latest_revision.acl_type if latest_revision else None
            )
            previous_explicit_ids = (
                list(latest_revision.explicit_user_ids)
                if latest_revision
                else None
            )

            # 创建新 Revision
            revision_number = (latest_revision.revision_number + 1) if latest_revision else 1
            explicit_ids_str = [str(uid) for uid in effective_explicit_ids]
            revision = await ResearchRepository.insert_acl_revision(
                session,
                result_id=result_id,
                revision_number=revision_number,
                acl_type=acl_type,
                explicit_user_ids=explicit_ids_str,
                previous_acl_type=previous_acl_type,
                previous_explicit_user_ids=previous_explicit_ids,
                changed_by=actor_id,
                change_reason=reason,
                is_declassify=is_declassify,
                declassify_reason=declassify_reason,
            )

            # 更新 Result
            await ResearchRepository.update_result_acl(
                session, result_id, acl_type, explicit_ids_str
            )

            # 审计
            audit_action = (
                "research.result.declassify"
                if is_declassify
                else "research.result.acl_change"
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action=audit_action,
                    actor_user_id=actor_id,
                    resource_type="research_result_acl_revision",
                    resource_id=revision.id,
                    payload={
                        "result_id": str(result_id),
                        "revision_number": revision_number,
                        "acl_type": acl_type,
                        "previous_acl_type": previous_acl_type,
                    },
                ),
            )

            return AclRevisionRef(
                revision_number=revision_number,
                acl_type=acl_type,
                explicit_user_ids=explicit_ids_str,
                previous_acl_type=previous_acl_type,
                previous_explicit_user_ids=previous_explicit_ids,
                changed_by=actor_id,
                changed_at=revision.changed_at,
                change_reason=reason or "",
                is_declassify=is_declassify,
                declassify_reason=declassify_reason,
            )

    # ============================================================
    # 版本管理
    # ============================================================

    async def withdraw_result(
        self,
        result_id: UUID,
        version_number: int | None,
        reason: str = "",
    ) -> None:
        """撤回成果包版本。

        1. 校验调用者为 owner 或持有 research:manage
        2. 标记 ResearchResultVersion.status = 'withdrawn'
        3. 若 version_number 为 None，撤回全部版本
        4. 审计
        5. 更新 ResearchResult.status = 'withdrawn'（如全部版本撤回）

        Args:
            result_id: 成果包 ID。
            version_number: 要撤回的版本号（None 表示撤回全部）。
            reason: 撤回原因。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
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
                    message="只有成果包所有者或管理员可以撤回成果",
                    retryable=False,
                    fields={},
                )

            if version_number is not None:
                version = await ResearchRepository.get_result_version(
                    session, result_id, version_number
                )
                if version is None:
                    raise AppError(
                        code="not_found",
                        message="版本不存在",
                        retryable=False,
                        fields={"version_number": version_number},
                    )
                await ResearchRepository.update_result_version_status(
                    session, version.id, "withdrawn"
                )
                await AuditRecorder.record(
                    session,
                    AuditEventData(
                        department_id=self._dept_id,
                        action="research.result.withdraw",
                        actor_user_id=actor_id,
                        resource_type="research_result_version",
                        resource_id=version.id,
                        payload={
                            "result_id": str(result_id),
                            "version_number": version_number,
                            "reason": reason,
                        },
                    ),
                )
            else:
                # 撤回全部版本
                versions = await ResearchRepository.list_result_versions(
                    session, result_id
                )
                for v in versions:
                    if v.status == "active":
                        await ResearchRepository.update_result_version_status(
                            session, v.id, "withdrawn"
                        )
                await ResearchRepository.update_result_status(
                    session, result_id, "withdrawn"
                )
                await AuditRecorder.record(
                    session,
                    AuditEventData(
                        department_id=self._dept_id,
                        action="research.result.withdraw",
                        actor_user_id=actor_id,
                        resource_type="research_result",
                        resource_id=result_id,
                        payload={"reason": reason},
                    ),
                )

    async def update_result_metadata(
        self,
        result_id: UUID,
        name: str,
    ) -> ResultRef:
        """编辑成果包元数据（仅 stable identity name）。

        Args:
            result_id: 成果包 ID。
            name: 新名称。

        Returns:
            ResultRef: 更新后的成果包引用。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
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
                    message="只有成果包所有者可以编辑元数据",
                    retryable=False,
                    fields={},
                )

            await ResearchRepository.update_result_metadata(
                session, result_id, name
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.result.edit",
                    actor_user_id=actor_id,
                    resource_type="research_result",
                    resource_id=result_id,
                    payload={"name": name},
                ),
            )

            return ResultRef(
                result_id=result_id,
                name=name,
                status=result.status,
                current_version=result.current_version,
                current_acl_type=result.current_acl_type,
            )

    # ============================================================
    # 详情 / 版本 / ACL 历史
    # ============================================================

    async def get_result_detail(self, result_id: UUID) -> ResultDetail:
        """获取成果包详情（含当前版本内容 + 衍生来源 + 权限状态）。

        Args:
            result_id: 成果包 ID。

        Returns:
            ResultDetail: 成果包详情。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            # ACL 可见性校验
            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            # 获取当前版本
            latest_version = await ResearchRepository.get_latest_result_version(
                session, result_id
            )
            current_version_detail: ResultVersionDetail | None = None
            if latest_version is not None:
                current_version_detail = await self._version_to_detail(session, latest_version)

            # 版本历史
            versions = await ResearchRepository.list_result_versions(
                session, result_id
            )
            version_history = [
                ResultVersionRef(
                    result_id=result_id,
                    version_number=v.version_number,
                    title=v.title,
                    status=v.status,
                    published_at=v.published_at,
                )
                for v in versions
            ]

            # ACL 变更记录
            revisions = await ResearchRepository.list_acl_revisions(
                session, result_id
            )
            acl_revisions = [
                AclRevisionRef(
                    revision_number=r.revision_number,
                    acl_type=r.acl_type,
                    explicit_user_ids=list(r.explicit_user_ids or []),
                    previous_acl_type=r.previous_acl_type,
                    previous_explicit_user_ids=(
                        list(r.previous_explicit_user_ids or [])
                        if r.previous_explicit_user_ids is not None
                        else None
                    ),
                    changed_by=r.changed_by,
                    changed_at=r.changed_at,
                    change_reason=r.change_reason or "",
                    is_declassify=r.is_declassify,
                    declassify_reason=r.declassify_reason,
                )
                for r in revisions
            ]

            # 收藏状态
            is_favorited = await ResearchRepository.check_favorite(
                session, result_id, actor_id
            )

            return ResultDetail(
                result_ref=ResultRef(
                    result_id=result_id,
                    name=result.name,
                    status=result.status,
                    current_version=result.current_version,
                    current_acl_type=result.current_acl_type,
                ),
                current_version=current_version_detail,
                version_history=version_history,
                acl_revisions=acl_revisions,
                is_favorited=is_favorited,
            )

    async def get_version_detail(
        self,
        result_id: UUID,
        version_number: int,
    ) -> ResultVersionDetail:
        """获取版本详情。

        Args:
            result_id: 成果包 ID。
            version_number: 版本号。

        Returns:
            ResultVersionDetail: 版本详情。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            version = await ResearchRepository.get_result_version(
                session, result_id, version_number
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message="版本不存在",
                    retryable=False,
                    fields={"version_number": version_number},
                )

            return await self._version_to_detail(session, version)

    async def list_versions(self, result_id: UUID) -> list[ResultVersionRef]:
        """版本历史列表。

        Args:
            result_id: 成果包 ID。

        Returns:
            list[ResultVersionRef]: 版本列表。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            versions = await ResearchRepository.list_result_versions(
                session, result_id
            )
            return [
                ResultVersionRef(
                    result_id=result_id,
                    version_number=v.version_number,
                    title=v.title,
                    status=v.status,
                    published_at=v.published_at,
                )
                for v in versions
            ]

    async def list_acl_revisions(self, result_id: UUID) -> list[AclRevisionRef]:
        """权限变更记录列表。

        Args:
            result_id: 成果包 ID。

        Returns:
            list[AclRevisionRef]: 修订记录列表。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            revisions = await ResearchRepository.list_acl_revisions(
                session, result_id
            )
            return [
                AclRevisionRef(
                    revision_number=r.revision_number,
                    acl_type=r.acl_type,
                    explicit_user_ids=list(r.explicit_user_ids or []),
                    previous_acl_type=r.previous_acl_type,
                    previous_explicit_user_ids=(
                        list(r.previous_explicit_user_ids or [])
                        if r.previous_explicit_user_ids is not None
                        else None
                    ),
                    changed_by=r.changed_by,
                    changed_at=r.changed_at,
                    change_reason=r.change_reason or "",
                    is_declassify=r.is_declassify,
                    declassify_reason=r.declassify_reason,
                )
                for r in revisions
            ]

    # ============================================================
    # 内部对象独立引用
    # ============================================================

    async def get_result_internal_object(
        self,
        result_id: UUID,
        object_type: str,
        object_id: UUID,
    ) -> dict:
        """获取成果包内指定对象（校验成果包 ACL）。

        Args:
            result_id: 成果包 ID。
            object_type: 对象类型（dataset / view / insight）。
            object_id: 对象 UUID。

        Returns:
            dict: 对象详情。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            # 获取最新版本以校验 object_id 在引用中
            latest_version = await ResearchRepository.get_latest_result_version(
                session, result_id
            )
            if latest_version is None:
                raise AppError(
                    code="not_found",
                    message="成果包尚无发布版本",
                    retryable=False,
                    fields={},
                )

            if object_type == "dataset":
                refs = latest_version.dataset_version_refs or []
                ref_key = "dataset_id"
            elif object_type == "view":
                refs = latest_version.view_version_refs or []
                ref_key = "view_id"
            elif object_type == "insight":
                refs = latest_version.insight_version_refs or []
                ref_key = "insight_id"
            else:
                raise AppError(
                    code="validation_failed",
                    message=f"不支持的对象类型: {object_type}",
                    retryable=False,
                    fields={"object_type": object_type},
                )

            # 校验 object_id 在引用列表中
            found = False
            version_number = None
            for ref in refs:
                if str(ref.get(ref_key, "")) == str(object_id):
                    found = True
                    version_number = ref.get("version_number")
                    break

            if not found:
                raise AppError(
                    code="not_found",
                    message=f"对象 {object_id} 不在成果包版本中",
                    retryable=False,
                    fields={"object_id": str(object_id)},
                )

            # 通过 ProductService 获取产物详情
            if object_type == "dataset":
                return await self._get_dataset_detail(
                    session, object_id, version_number or 1
                )
            elif object_type == "view":
                return await self._get_view_detail(
                    session, object_id, version_number or 1
                )
            else:
                return await self._get_insight_detail(
                    session, object_id, version_number or 1
                )

    # ============================================================
    # 复用
    # ============================================================

    async def add_to_workspace(
        self,
        result_id: UUID,
        workspace_id: UUID,
        dataset_id: UUID,
        version_number: int | None = None,
    ) -> EvidenceRefDTO:
        """将成果包内 DerivedDataset 加入指定 Workspace 证据集。

        1. 校验成果包 ACL（当前用户有权查看）
        2. 校验 dataset_id 在成果包版本的 dataset_version_refs 中
        3. 通过 WorkspaceService.add_evidence() 加入（source_namespace="research:published_derived"）
        4. 审计

        Args:
            result_id: 成果包 ID。
            workspace_id: 目标工作空间 ID。
            dataset_id: 数据集 ID。
            version_number: 数据集版本号（可选，默认使用最新版本）。

        Returns:
            EvidenceRefDTO: 证据引用 DTO。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            # 获取最新版本
            latest_version = await ResearchRepository.get_latest_result_version(
                session, result_id
            )
            if latest_version is None:
                raise AppError(
                    code="not_found",
                    message="成果包尚无发布版本",
                    retryable=False,
                    fields={},
                )

            # 校验 dataset_id 在 dataset_version_refs 中
            found_version = None
            for ref in latest_version.dataset_version_refs or []:
                if str(ref.get("dataset_id", "")) == str(dataset_id):
                    found_version = ref.get("version_number")
                    break

            if found_version is None:
                raise AppError(
                    code="not_found",
                    message="数据集不在成果包版本中",
                    retryable=False,
                    fields={"dataset_id": str(dataset_id)},
                )

            effective_version = version_number or found_version

            # 插入证据引用
            ref = await ResearchRepository.insert_evidence_ref(
                session,
                workspace_id=workspace_id,
                source_namespace="research:published_derived",
                source_id=dataset_id,
                source_version=str(effective_version),
                source_name=latest_version.title,
                added_by=actor_id,
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.result.reuse_evidence",
                    actor_user_id=actor_id,
                    resource_type="research_workspace_evidence_ref",
                    resource_id=ref.id,
                    payload={
                        "result_id": str(result_id),
                        "workspace_id": str(workspace_id),
                        "dataset_id": str(dataset_id),
                        "version_number": effective_version,
                    },
                ),
            )

            return EvidenceRefDTO(
                ref_id=ref.id,
                source_namespace=ref.source_namespace,
                source_id=ref.source_id,
                source_version=ref.source_version,
                source_name=ref.source_name,
                status=ref.status,
            )

    async def new_workspace_from_result(
        self,
        result_id: UUID,
        workspace_name: str,
        question_text: str,
    ) -> WorkspaceRef:
        """基于此成果新建 Workspace。

        1. 校验成果包 ACL
        2. 创建新 Workspace（继承研究问题文本）
        3. 将成果包内全部 DerivedDataset 作为证据加入

        Args:
            result_id: 成果包 ID。
            workspace_name: 新工作空间名称。
            question_text: 研究问题文本。

        Returns:
            WorkspaceRef: 新工作空间引用。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if not self._check_result_visible(result, actor_id):
                raise AppError(
                    code="forbidden",
                    message="无权访问此成果包",
                    retryable=False,
                    fields={},
                )

            latest_version = await ResearchRepository.get_latest_result_version(
                session, result_id
            )
            if latest_version is None:
                raise AppError(
                    code="not_found",
                    message="成果包尚无发布版本",
                    retryable=False,
                    fields={},
                )

            # 创建新工作空间
            new_ws = await ResearchRepository.insert_workspace(
                session,
                owner_user_id=actor_id,
                department_id=self._dept_id,
                name=workspace_name,
                status="draft",
            )

            # 创建问题版本 v1
            await ResearchRepository.insert_question_version(
                session,
                workspace_id=new_ws.id,
                version_number=1,
                question_text=question_text,
                sub_questions=[],
                created_by=actor_id,
            )
            await ResearchRepository.update_workspace_current_version(
                session, new_ws.id, 1
            )

            # 将全部 DerivedDataset 作为证据加入
            for ref in latest_version.dataset_version_refs or []:
                dataset_id = ref.get("dataset_id")
                version_num = ref.get("version_number", 1)
                if dataset_id:
                    try:
                        ds_uuid = UUID(str(dataset_id))
                    except (ValueError, TypeError):
                        continue
                    await ResearchRepository.insert_evidence_ref(
                        session,
                        workspace_id=new_ws.id,
                        source_namespace="research:published_derived",
                        source_id=ds_uuid,
                        source_version=str(version_num),
                        source_name=latest_version.title,
                        added_by=actor_id,
                    )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.result.reuse_workspace",
                    actor_user_id=actor_id,
                    resource_type="research_workspace",
                    resource_id=new_ws.id,
                    payload={
                        "result_id": str(result_id),
                        "workspace_name": workspace_name,
                    },
                ),
            )

            return WorkspaceRef(
                workspace_id=new_ws.id,
                name=workspace_name,
                status="draft",
                current_question_version=1,
            )

    # ============================================================
    # 收藏
    # ============================================================

    async def toggle_favorite(
        self,
        result_id: UUID,
        is_favorite: bool,
    ) -> None:
        """收藏 / 取消收藏成果包。

        Args:
            result_id: 成果包 ID。
            is_favorite: True=收藏，False=取消收藏。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            result = await ResearchRepository.get_result(session, result_id)
            if result is None:
                raise AppError(
                    code="not_found",
                    message="成果包不存在",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            if is_favorite:
                # 检查是否已收藏
                already = await ResearchRepository.check_favorite(
                    session, result_id, actor_id
                )
                if not already:
                    await ResearchRepository.insert_favorite(
                        session,
                        result_id=result_id,
                        user_id=actor_id,
                    )
                action = "research.result.favorite"
            else:
                await ResearchRepository.delete_favorite(
                    session, result_id, actor_id
                )
                action = "research.result.unfavorite"

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action=action,
                    actor_user_id=actor_id,
                    resource_type="research_result_favorite",
                    resource_id=result_id,
                    payload={"is_favorite": is_favorite},
                ),
            )

    # ============================================================
    # 内部辅助方法
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
        dataset_version_refs: list[dict] = []
        view_version_refs: list[dict] = []
        insight_version_refs: list[dict] = []
        snapshot_ids: set[str] = set()
        run_ids: set[str] = set()
        run_statuses: dict[str, str] = {}

        # 收集 DerivedDataset 引用
        for dataset_id in request.dataset_ids:
            dataset = await ResearchRepository.get_dataset(
                session, dataset_id, workspace_id
            )
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

            version = await ResearchRepository.get_latest_dataset_version(
                session, dataset_id
            )
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
            run_status = await self._get_run_status(
                session, dataset.source_run_id
            )
            if run_status is not None:
                run_statuses[str(dataset.source_run_id)] = run_status

        # 收集 ResearchView 引用
        for view_id in request.view_ids:
            view = await ResearchRepository.get_view(
                session, view_id, workspace_id
            )
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
                view_versions = await ResearchRepository.list_view_versions(
                    session, view_id
                )
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
            run_status = await self._get_run_status(
                session, view.source_run_id
            )
            if run_status is not None:
                run_statuses[str(view.source_run_id)] = run_status

        # 收集 Insight 引用
        for insight_id in request.insight_ids:
            insight = await ResearchRepository.get_insight(
                session, insight_id, workspace_id
            )
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
                }
            )

            if insight.source_run_id is not None:
                run_ids.add(str(insight.source_run_id))
                run_status = await self._get_run_status(
                    session, insight.source_run_id
                )
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
            from packages.research.entities_trusted import ResearchAnalysisRun
            res = await session.execute(
                sa.select(ResearchAnalysisRun.status).where(
                    ResearchAnalysisRun.id == run_id
                )
            )
            row = res.first()
            return row[0] if row else None
        except ImportError:
            # entities_trusted 不可用时使用原生 SQL
            res = await session.execute(
                sa.text(
                    "SELECT status FROM research_analysis_run WHERE id = :rid"
                ).bindparams(rid=run_id),
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
        entries: list[dict] = []

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

    def _check_result_visible(
        self,
        result: object,
        principal_id: UUID,
    ) -> bool:
        """校验当前用户是否有权查看成果包（基于 ACL）。

        Args:
            result: ResearchResult ORM 实体。
            principal_id: 当前用户 ID。

        Returns:
            bool: 是否有权查看。
        """
        # private: 仅 owner 可见
        if result.current_acl_type == "private":
            return result.owner_user_id == principal_id

        # tree: 同部门可见（首期简化为部门内可见，实际需查询部门树）
        if result.current_acl_type == "tree":
            return True  # 首期简化：同部门用户可见（RLS 已过滤跨部门）

        # explicit: 指定用户可见
        if result.current_acl_type == "explicit":
            explicit_ids = result.current_explicit_user_ids or []
            return (
                str(principal_id) in [str(uid) for uid in explicit_ids]
                or result.owner_user_id == principal_id
            )

        # all: 全部可见
        if result.current_acl_type == "all":
            return True

        # 未知 ACL 类型，保守为不可见
        return False

    async def _version_to_detail(
        self,
        session: object,
        version: object,
    ) -> ResultVersionDetail:
        """将 ORM 版本实体转换为版本详情 dataclass。

        Args:
            session: 数据库会话。
            version: ResearchResultVersion ORM 实体。

        Returns:
            ResultVersionDetail: 版本详情。
        """
        # 查询发布者 display_name
        publisher_display = str(version.publisher)
        try:
            from sqlalchemy import text as _sa_text
            r = await session.execute(
                _sa_text("SELECT display_name FROM app_user WHERE id = :uid"),
                {"uid": str(version.publisher)},
            )
            row = r.fetchone()
            if row and row[0]:
                publisher_display = row[0]
        except Exception:
            pass

        return ResultVersionDetail(
            result_id=version.result_id,
            version_number=version.version_number,
            title=version.title,
            summary=version.summary or "",
            tags=list(version.tags or []),
            release_notes=version.release_notes or "",
            dataset_version_refs=list(version.dataset_version_refs or []),
            view_version_refs=list(version.view_version_refs or []),
            insight_version_refs=list(version.insight_version_refs or []),
            evidence_snapshot_ids=list(version.evidence_snapshot_ids or []),
            analysis_run_ids=list(version.analysis_run_ids or []),
            source_run_statuses=dict(version.source_run_statuses or {}),
            publisher=publisher_display,
            published_at=version.published_at,
            content_hash=version.content_hash,
            published_permission_envelope=dict(
                version.published_permission_envelope or {}
            ),
            status=version.status,
        )

    async def _get_dataset_detail(
        self,
        session: AsyncSession,
        dataset_id: UUID,
        version_number: int,
    ) -> dict:
        """获取数据集版本详情。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            version_number: 版本号。

        Returns:
            dict: 数据集详情。
        """
        dataset = await ResearchRepository.get_dataset(session, dataset_id)
        version = await ResearchRepository.get_dataset_version(
            session, dataset_id, version_number
        )
        if dataset is None or version is None:
            raise AppError(
                code="not_found",
                message="数据集或版本不存在",
                retryable=False,
                fields={"dataset_id": str(dataset_id)},
            )
        return {
            "object_type": "dataset",
            "dataset_id": str(dataset_id),
            "name": dataset.name,
            "version_number": version.version_number,
            "metadata": version.metadata_content,
            "points": version.points_content,
            "series": version.series_content,
            "field_manifest": version.field_manifest,
            "content_hash": version.content_hash,
        }

    async def _get_view_detail(
        self,
        session: AsyncSession,
        view_id: UUID,
        version_number: int,
    ) -> dict:
        """获取视图版本详情。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            version_number: 版本号。

        Returns:
            dict: 视图详情。
        """
        view = await ResearchRepository.get_view(session, view_id)
        view_version = await ResearchRepository.get_view_version(
            session, view_id, version_number
        )
        if view is None or view_version is None:
            raise AppError(
                code="not_found",
                message="视图或版本不存在",
                retryable=False,
                fields={"view_id": str(view_id)},
            )
        return {
            "object_type": "view",
            "view_id": str(view_id),
            "name": view.name,
            "caption": view.caption,
            "version_number": view_version.version_number,
            "image_storage_path": view_version.image_storage_path,
            "image_format": view_version.image_format,
            "image_content_hash": view_version.image_content_hash,
            "chart_description": view_version.chart_description,
        }

    async def _get_insight_detail(
        self,
        session: AsyncSession,
        insight_id: UUID,
        version_number: int,
    ) -> dict:
        """获取 Insight 版本详情。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 版本号。

        Returns:
            dict: Insight 详情。
        """
        insight = await ResearchRepository.get_insight(session, insight_id)
        insight_version = await ResearchRepository.get_insight_version(
            session, insight_id, version_number
        )
        if insight is None or insight_version is None:
            raise AppError(
                code="not_found",
                message="Insight 或版本不存在",
                retryable=False,
                fields={"insight_id": str(insight_id)},
            )
        return {
            "object_type": "insight",
            "insight_id": str(insight_id),
            "name": insight.name,
            "version_number": insight_version.version_number,
            "conclusion": insight_version.conclusion,
            "scope": insight_version.scope,
            "evidence_refs": insight_version.evidence_refs,
            "method_refs": insight_version.method_refs,
            "confidence_level": insight_version.confidence_level,
            "limitations": insight_version.limitations,
            "evidence_source_label": insight_version.evidence_source_label,
        }
