"""成果包 ACL 修改逻辑：_AclMixin。

提供 PublicationService 的 ACL 修改能力（创建 ResultAclRevision，更新
Result.current_acl_*）。ACL 可见性校验（_check_result_visible）为跨功能域
共享方法，集中在 _base._PublicationBase。
"""

from uuid import UUID

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.execution.envelope import PermissionEnvelopeCalculator
from packages.research.models import AclRevisionRef
from packages.research.publication._base import _PublicationBase
from packages.research.repository import ResearchRepository


class _AclMixin(_PublicationBase):
    """成果包 ACL 修改相关方法 mixin。"""

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
            latest_version = await ResearchRepository.get_latest_result_version(session, result_id)
            snapshot_ids: list[UUID] = []
            if latest_version is not None:
                for sid in latest_version.evidence_snapshot_ids or []:
                    try:
                        snapshot_ids.append(UUID(str(sid)))
                    except (ValueError, TypeError):
                        pass

            # 计算权限包络
            envelope = await PermissionEnvelopeCalculator.calculate_envelope(snapshot_ids, session)

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
            latest_revision = await ResearchRepository.get_latest_acl_revision(session, result_id)
            previous_acl_type = latest_revision.acl_type if latest_revision else None
            previous_explicit_ids = (
                list(latest_revision.explicit_user_ids) if latest_revision else None
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
                "research.result.declassify" if is_declassify else "research.result.acl_change"
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
