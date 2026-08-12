"""成果包内部对象引用、复用与收藏逻辑：_ReuseMixin。

提供 PublicationService 的成果消费能力：
- 成果包内部对象独立引用（get_result_internal_object）
- 复用操作（加入 Workspace / 基于此成果新建 Workspace）
- 收藏 / 取消收藏
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.dtos import EvidenceRefDTO, WorkspaceRef
from packages.research.publication._base import _PublicationBase
from packages.research.repository import ResearchRepository


class _ReuseMixin(_PublicationBase):
    """成果包内部对象引用、复用与收藏相关方法 mixin。"""

    # ============================================================
    # 内部对象独立引用
    # ============================================================

    async def get_result_internal_object(
        self,
        result_id: UUID,
        object_type: str,
        object_id: UUID,
    ) -> dict[str, Any]:
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
            latest_version = await ResearchRepository.get_latest_result_version(session, result_id)
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
                return await self._get_dataset_detail(session, object_id, version_number or 1)
            elif object_type == "view":
                return await self._get_view_detail(session, object_id, version_number or 1)
            else:
                return await self._get_insight_detail(session, object_id, version_number or 1)

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
        3. 通过 WorkspaceService.add_evidence() 加入
           （source_namespace="research:published_derived"）
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
            latest_version = await ResearchRepository.get_latest_result_version(session, result_id)
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

            latest_version = await ResearchRepository.get_latest_result_version(session, result_id)
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

            # Timeline refactoring: question version removed, workspace created name-only
            # No question version insertion needed

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
                already = await ResearchRepository.check_favorite(session, result_id, actor_id)
                if not already:
                    await ResearchRepository.insert_favorite(
                        session,
                        result_id=result_id,
                        user_id=actor_id,
                    )
                action = "research.result.favorite"
            else:
                await ResearchRepository.delete_favorite(session, result_id, actor_id)
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
    # 产物详情辅助方法
    # ============================================================

    async def _get_dataset_detail(
        self,
        session: AsyncSession,
        dataset_id: UUID,
        version_number: int,
    ) -> dict[str, Any]:
        """获取数据集版本详情。

        Args:
            session: 异步会话。
            dataset_id: 数据集 ID。
            version_number: 版本号。

        Returns:
            dict: 数据集详情。
        """
        dataset = await ResearchRepository.get_dataset(session, dataset_id)
        version = await ResearchRepository.get_dataset_version(session, dataset_id, version_number)
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
    ) -> dict[str, Any]:
        """获取视图版本详情。

        Args:
            session: 异步会话。
            view_id: 视图 ID。
            version_number: 版本号。

        Returns:
            dict: 视图详情。
        """
        view = await ResearchRepository.get_view(session, view_id)
        view_version = await ResearchRepository.get_view_version(session, view_id, version_number)
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
    ) -> dict[str, Any]:
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
