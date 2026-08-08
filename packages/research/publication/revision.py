"""成果包版本管理与详情查询逻辑：_RevisionMixin。

提供 PublicationService 的版本管理能力：
- 撤回成果包版本（withdraw）
- 编辑元数据（update_result_metadata）
- 成果包详情 / 版本详情 / 版本历史 / ACL 变更记录查询
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.dtos import (
    AclRevisionRef,
    ResultDetail,
    ResultRef,
    ResultVersionDetail,
    ResultVersionRef,
)
from packages.research.entities import ResearchResultVersion
from packages.research.publication._base import _PublicationBase
from packages.research.repository import ResearchRepository


class _RevisionMixin(_PublicationBase):
    """成果包版本管理与详情查询相关方法 mixin。"""

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
                versions = await ResearchRepository.list_result_versions(session, result_id)
                for v in versions:
                    if v.status == "active":
                        await ResearchRepository.update_result_version_status(
                            session, v.id, "withdrawn"
                        )
                await ResearchRepository.update_result_status(session, result_id, "withdrawn")
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

            await ResearchRepository.update_result_metadata(session, result_id, name)
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
            latest_version = await ResearchRepository.get_latest_result_version(session, result_id)
            current_version_detail: ResultVersionDetail | None = None
            if latest_version is not None:
                current_version_detail = await self._version_to_detail(session, latest_version)

            # 版本历史
            versions = await ResearchRepository.list_result_versions(session, result_id)
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
            revisions = await ResearchRepository.list_acl_revisions(session, result_id)
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
            is_favorited = await ResearchRepository.check_favorite(session, result_id, actor_id)

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

            versions = await ResearchRepository.list_result_versions(session, result_id)
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

            revisions = await ResearchRepository.list_acl_revisions(session, result_id)
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

    async def _version_to_detail(
        self,
        session: AsyncSession,
        version: ResearchResultVersion,
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
            logging.getLogger(__name__).warning("unexpected error", exc_info=True)

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
            publisher=publisher_display,  # type: ignore[arg-type]
            published_at=version.published_at,
            content_hash=version.content_hash,
            published_permission_envelope=dict(version.published_permission_envelope or {}),
            status=version.status,
        )
