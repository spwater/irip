"""研究产物业务编排服务：ProductService。

ProductService 管理 DerivedDataset / ResearchView / Insight 的完整生命周期：
- 从 RunArtifact 创建 DerivedDataset（稳定身份 + v1 不可变版本）
- 从 RunArtifact 创建 ResearchView（稳定身份 + v1 不可变版本）
- 从 InsightCandidate 接受/修改创建 Insight（稳定身份 + v1 不可变版本）
- 列表 / 详情 / 版本历史
- 编辑元数据（仅 stable identity 字段，不触碰 version 内容）
- 产物列表（按类型分组）

核心不变量：
1. 版本实体创建后不可变（Repository 不提供 update/delete 方法）
2. 编辑 API 仅接受 stable identity 元数据字段
3. 非 publishable 工件不允许创建产物
4. 所有写操作产生审计记录

参照 packages/research/service.py 的 ScopedSessionMixin 模式。
"""

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.models import (
    DatasetDetail,
    DatasetVersionDetail,
    DatasetVersionRef,
    DerivedDatasetRef,
    InsightDetail,
    InsightRef,
    InsightVersionRef,
    ProductSummary,
    ViewDetail,
    ViewRef,
    ViewVersionDetail,
    ViewVersionRef,
)
from packages.research.repository import ResearchRepository
from packages.research.repository_trusted import ResearchRepositoryTrusted
from packages.research.validation import ThreeSegmentValidator

logger = logging.getLogger("research.products")


class ProductService(ScopedSessionMixin):
    """DerivedDataset / ResearchView / Insight 生命周期管理服务。

    依赖注入 session_factory / department_id / actor_id / RunArtifactService。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _artifact_service: RunArtifactService（工件内容读取和下载）。
        _rls_dept_id: RLS 部门 ID（平台管理员绕过隔离，可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        artifact_service: Any,
        lineage_writer: Any | None = None,
    ) -> None:
        """初始化产物服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            artifact_service: RunArtifactService 实例。
            lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._artifact_service = artifact_service
        self._lineage_writer = lineage_writer
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
    # DerivedDataset
    # ============================================================

    async def create_dataset(
        self,
        workspace_id: UUID,
        artifact_id: UUID,
        name: str,
        summary: str | None = None,
        tags: list[str] | None = None,
    ) -> DerivedDatasetRef:
        """从 RunArtifact 创建 DerivedDataset。

        流程：
        1. 获取工件（校验 is_publishable=true, artifact_type=data）
        2. 下载工件内容（MinIO）
        3. ThreeSegmentValidator.validate() 校验三段式
        4. ThreeSegmentValidator.infer_field_manifest() 推断字段清单
        5. ThreeSegmentValidator.compute_content_hash() 计算哈希
        6. 创建 ResearchDerivedDataset（stable identity）
        7. 创建 ResearchDerivedDatasetVersion v1（不可变）
        8. 更新 dataset.current_version=1
        9. 审计

        Args:
            workspace_id: 工作空间 ID。
            artifact_id: 工件 ID。
            name: 数据集名称。
            summary: 摘要（可选）。
            tags: 标签列表（可选）。

        Returns:
            DerivedDatasetRef: 数据集引用。

        Raises:
            AppError: code="validation_failed"，当工件不可发布或类型不匹配时。
            AppError: code="validation_failed"，当三段式校验失败时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 获取工件并校验
            artifact = await ResearchRepositoryTrusted.get_artifact(session, artifact_id)
            if artifact is None:
                raise AppError(
                    code="not_found",
                    message="工件不存在",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            if not artifact.is_publishable:
                raise AppError(
                    code="validation_failed",
                    message="工件不可发布，无法创建数据集",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            if artifact.artifact_type != "data":
                raise AppError(
                    code="validation_failed",
                    message=f"工件类型不匹配: 期望 data, 实际 {artifact.artifact_type}",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )

            # 2. 下载工件内容
            artifact_content = await self._artifact_service.get_artifact(artifact_id)
            if artifact_content is None:
                raise AppError(
                    code="validation_failed",
                    message="无法下载工件内容",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )

            # 3. 校验三段式数据
            result = ThreeSegmentValidator.validate(artifact_content.content)
            if not result.valid:
                raise AppError(
                    code="validation_failed",
                    message="三段式数据校验失败: " + "; ".join(result.errors),
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )

            assert result.data is not None
            metadata_content = result.data.metadata
            points_content = result.data.points
            series_content = result.data.series
            field_manifest = result.field_manifest

            # 4. 计算 content_hash
            content_hash = ThreeSegmentValidator.compute_content_hash(
                metadata_content, points_content, series_content
            )

            # 5. 获取最新快照 ID（逻辑引用）
            latest_snapshot = await ResearchRepository.get_latest_snapshot(session, workspace_id)
            source_snapshot_id = latest_snapshot.id if latest_snapshot else None

            # 6. 创建稳定身份
            dataset = await ResearchRepository.insert_dataset(
                session,
                workspace_id=workspace_id,
                owner_user_id=actor_id,
                name=name,
                summary=summary,
                tags=tags if tags is not None else [],
                status="confirmed",
                source_run_id=artifact.run_id,
                source_snapshot_id=source_snapshot_id,
            )

            # 7. 创建 v1 不可变版本
            version = await ResearchRepository.insert_dataset_version(
                session,
                dataset_id=dataset.id,
                version_number=1,
                metadata_content=metadata_content,
                points_content=points_content,
                series_content=series_content,
                field_manifest=field_manifest,
                source_run_id=artifact.run_id,
                source_step_id=artifact.step_id,
                source_artifact_id=artifact_id,
                content_hash=content_hash,
                created_by=actor_id,
            )

            # 8. 更新当前版本号
            await ResearchRepository.update_dataset_current_version(session, dataset.id, 1)

            # 9. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.derived_dataset.create",
                    actor_user_id=actor_id,
                    resource_type="research_derived_dataset",
                    resource_id=dataset.id,
                    payload={"name": name, "artifact_id": str(artifact_id)},
                ),
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.derived_dataset.version",
                    actor_user_id=actor_id,
                    resource_type="research_derived_dataset_version",
                    resource_id=version.id,
                    payload={
                        "dataset_id": str(dataset.id),
                        "version_number": 1,
                        "content_hash": content_hash[:16],
                    },
                ),
            )

            result = DerivedDatasetRef(  # type: ignore[assignment]
                dataset_id=dataset.id,
                name=name,
                status="confirmed",
                current_version=1,
                workspace_id=workspace_id,
            )
            _hook_run_id = artifact.run_id
            _hook_dataset_id = dataset.id

        # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
        if self._lineage_writer is not None:
            try:
                await self._lineage_writer.on_product_confirmed(
                    _hook_run_id,
                    "research:derived_dataset",
                    _hook_dataset_id,
                    "dataset",
                )
            except Exception as exc:
                logger.warning("on_product_confirmed hook failed: %s", exc)

        return result  # type: ignore[return-value]

    async def list_datasets(self, workspace_id: UUID) -> list[DerivedDatasetRef]:
        """列出工作空间内的 DerivedDataset。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[DerivedDatasetRef]: 数据集引用列表。
        """
        async with self._scoped_session() as session:
            datasets = await ResearchRepository.list_datasets(session, workspace_id)
            return [
                DerivedDatasetRef(
                    dataset_id=ds.id,
                    name=ds.name,
                    status=ds.status,
                    current_version=ds.current_version,
                    workspace_id=workspace_id,
                )
                for ds in datasets
            ]

    async def get_dataset(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
    ) -> DatasetDetail:
        """获取 DerivedDataset 详情（含当前版本数据预览）。

        Args:
            workspace_id: 工作空间 ID。
            dataset_id: 数据集 ID。

        Returns:
            DatasetDetail: 数据集详情。

        Raises:
            AppError: code="not_found"，当数据集不存在时。
        """
        async with self._scoped_session() as session:
            dataset = await ResearchRepository.get_dataset(session, dataset_id, workspace_id)
            if dataset is None:
                raise AppError(
                    code="not_found",
                    message="数据集不存在",
                    retryable=False,
                    fields={"dataset_id": str(dataset_id)},
                )

            current_version_data: dict[str, Any] | None = None
            if dataset.current_version > 0:
                version = await ResearchRepository.get_latest_dataset_version(session, dataset_id)
                if version is not None:
                    current_version_data = {
                        "version_id": str(version.id),
                        "version_number": version.version_number,
                        "metadata_content": version.metadata_content,
                        "points_content": version.points_content,
                        "series_content": version.series_content,
                        "field_manifest": version.field_manifest,
                        "content_hash": version.content_hash,
                        "created_at": version.created_at.isoformat()
                        if version.created_at
                        else None,
                    }

            return DatasetDetail(
                dataset_id=dataset.id,
                workspace_id=workspace_id,
                name=dataset.name,
                summary=dataset.summary,
                tags=list(dataset.tags or []),
                status=dataset.status,
                current_version=dataset.current_version,
                source_run_id=dataset.source_run_id,
                source_snapshot_id=dataset.source_snapshot_id,
                current_version_data=current_version_data,
            )

    async def update_dataset_metadata(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        name: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
    ) -> DerivedDatasetRef:
        """编辑数据集元数据（仅 stable identity 字段，不触碰 version 内容）。

        Args:
            workspace_id: 工作空间 ID。
            dataset_id: 数据集 ID。
            name: 新名称（可选，None 表示不更新）。
            summary: 新摘要（可选）。
            tags: 新标签列表（可选）。

        Returns:
            DerivedDatasetRef: 更新后的数据集引用。

        Raises:
            AppError: code="not_found"，当数据集不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            dataset = await ResearchRepository.get_dataset(session, dataset_id, workspace_id)
            if dataset is None:
                raise AppError(
                    code="not_found",
                    message="数据集不存在",
                    retryable=False,
                    fields={"dataset_id": str(dataset_id)},
                )

            await ResearchRepository.update_dataset_metadata(
                session,
                dataset_id,
                name=name,
                summary=summary,
                tags=tags,
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.derived_dataset.edit",
                    actor_user_id=actor_id,
                    resource_type="research_derived_dataset",
                    resource_id=dataset_id,
                    payload={
                        "name": name if name is not None else "(unchanged)",
                        "summary_updated": summary is not None,
                        "tags_updated": tags is not None,
                    },
                ),
            )

            return DerivedDatasetRef(
                dataset_id=dataset.id,
                name=name if name is not None else dataset.name,
                status=dataset.status,
                current_version=dataset.current_version,
                workspace_id=workspace_id,
            )

    async def list_dataset_versions(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
    ) -> list[DatasetVersionRef]:
        """列出数据集版本历史。

        Args:
            workspace_id: 工作空间 ID。
            dataset_id: 数据集 ID。

        Returns:
            list[DatasetVersionRef]: 版本引用列表。
        """
        async with self._scoped_session() as session:
            versions = await ResearchRepository.list_dataset_versions(session, dataset_id)
            return [
                DatasetVersionRef(
                    version_id=v.id,
                    dataset_id=dataset_id,
                    version_number=v.version_number,
                    content_hash=v.content_hash,
                    created_at=v.created_at,
                )
                for v in versions
            ]

    async def get_dataset_version(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        version_number: int,
    ) -> DatasetVersionDetail:
        """获取数据集版本详情（含三段式数据 + field_manifest）。

        Args:
            workspace_id: 工作空间 ID。
            dataset_id: 数据集 ID。
            version_number: 版本号。

        Returns:
            DatasetVersionDetail: 版本详情。

        Raises:
            AppError: code="not_found"，当版本不存在时。
        """
        async with self._scoped_session() as session:
            version = await ResearchRepository.get_dataset_version(
                session, dataset_id, version_number
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message="数据集版本不存在",
                    retryable=False,
                    fields={
                        "dataset_id": str(dataset_id),
                        "version_number": version_number,
                    },
                )

            return DatasetVersionDetail(
                version_id=version.id,
                dataset_id=dataset_id,
                version_number=version.version_number,
                metadata_content=version.metadata_content,
                points_content=version.points_content,
                series_content=version.series_content,
                field_manifest=version.field_manifest,
                content_hash=version.content_hash,
                source_run_id=version.source_run_id,
                source_step_id=version.source_step_id,
                source_artifact_id=version.source_artifact_id,
                created_at=version.created_at,
            )

    # ============================================================
    # ResearchView
    # ============================================================

    async def create_view(
        self,
        workspace_id: UUID,
        artifact_id: UUID,
        name: str,
        caption: str | None = None,
        display_order: int = 0,
    ) -> ViewRef:
        """从 RunArtifact 创建 ResearchView。

        流程：
        1. 获取工件（校验 is_publishable=true, artifact_type=chart）
        2. 读取工件元数据（格式、尺寸、content_hash）
        3. 查找同步骤的 code 工件作为 chart_code_artifact_id
        4. 从 Run 记录获取 image_digest
        5. 创建 ResearchView（stable identity）
        6. 创建 ResearchViewVersion v1（不可变）
        7. 更新 view.current_version=1
        8. 审计

        Args:
            workspace_id: 工作空间 ID。
            artifact_id: 工件 ID。
            name: 视图名称。
            caption: 图注（可选）。
            display_order: 展示顺序。

        Returns:
            ViewRef: 视图引用。

        Raises:
            AppError: code="validation_failed"，当工件不可发布或类型不匹配时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 获取工件并校验
            artifact = await ResearchRepositoryTrusted.get_artifact(session, artifact_id)
            if artifact is None:
                raise AppError(
                    code="not_found",
                    message="工件不存在",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            if not artifact.is_publishable:
                raise AppError(
                    code="validation_failed",
                    message="工件不可发布，无法创建视图",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            if artifact.artifact_type != "chart":
                raise AppError(
                    code="validation_failed",
                    message=f"工件类型不匹配: 期望 chart, 实际 {artifact.artifact_type}",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )

            # 2. 推断图片格式
            image_format = "png"
            if artifact.artifact_key.lower().endswith(".pdf"):
                image_format = "pdf"
            elif artifact.artifact_key.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                ext = artifact.artifact_key.rsplit(".", 1)[-1].lower()
                image_format = ext if ext in ("png", "pdf") else "png"

            image_content_hash = artifact.content_hash or ""

            # 3. 查找同步骤的 code 工件
            chart_code_artifact_id: UUID | None = None
            if artifact.step_id is not None:
                step_artifacts = await ResearchRepositoryTrusted.list_artifacts_by_step(
                    session, artifact.step_id
                )
                for a in step_artifacts:
                    if a.artifact_type == "code":
                        chart_code_artifact_id = a.id
                        break

            # 4. 从 Run 记录获取 image_digest
            run = await ResearchRepositoryTrusted.get_run(session, artifact.run_id)
            image_digest = run.image_digest if run is not None else None

            # 5. 创建稳定身份
            view = await ResearchRepository.insert_view(
                session,
                workspace_id=workspace_id,
                owner_user_id=actor_id,
                name=name,
                caption=caption,
                display_order=display_order,
                status="confirmed",
                source_run_id=artifact.run_id,
            )

            # 6. 创建 v1 不可变版本
            version = await ResearchRepository.insert_view_version(
                session,
                view_id=view.id,
                version_number=1,
                image_storage_path=artifact.storage_path,
                image_format=image_format,
                image_content_hash=image_content_hash,
                chart_code_artifact_id=chart_code_artifact_id,
                image_digest=image_digest,
                source_run_id=artifact.run_id,
                source_step_id=artifact.step_id,
                source_artifact_id=artifact_id,
                created_by=actor_id,
            )

            # 7. 更新当前版本号
            await ResearchRepository.update_view_current_version(session, view.id, 1)

            # 8. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.view.create",
                    actor_user_id=actor_id,
                    resource_type="research_view",
                    resource_id=view.id,
                    payload={"name": name, "artifact_id": str(artifact_id)},
                ),
            )
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.view.version",
                    actor_user_id=actor_id,
                    resource_type="research_view_version",
                    resource_id=version.id,
                    payload={
                        "view_id": str(view.id),
                        "version_number": 1,
                    },
                ),
            )

            result = ViewRef(
                view_id=view.id,
                name=name,
                status="confirmed",
                current_version=1,
                caption=caption,
                display_order=display_order,
            )
            _hook_run_id = artifact.run_id
            _hook_view_id = view.id

        # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
        if self._lineage_writer is not None:
            try:
                await self._lineage_writer.on_product_confirmed(
                    _hook_run_id,
                    "research:view",
                    _hook_view_id,
                    "view",
                )
            except Exception as exc:
                logger.warning("on_product_confirmed hook failed: %s", exc)

        return result

    async def list_views(self, workspace_id: UUID) -> list[ViewRef]:
        """列出工作空间内的 ResearchView。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[ViewRef]: 视图引用列表。
        """
        async with self._scoped_session() as session:
            views = await ResearchRepository.list_views(session, workspace_id)
            return [
                ViewRef(
                    view_id=v.id,
                    name=v.name,
                    status=v.status,
                    current_version=v.current_version,
                    caption=v.caption,
                    display_order=v.display_order,
                )
                for v in views
            ]

    async def get_view(
        self,
        workspace_id: UUID,
        view_id: UUID,
    ) -> ViewDetail:
        """获取 ResearchView 详情（含当前版本图片信息）。

        Args:
            workspace_id: 工作空间 ID。
            view_id: 视图 ID。

        Returns:
            ViewDetail: 视图详情。

        Raises:
            AppError: code="not_found"，当视图不存在时。
        """
        async with self._scoped_session() as session:
            view = await ResearchRepository.get_view(session, view_id, workspace_id)
            if view is None:
                raise AppError(
                    code="not_found",
                    message="视图不存在",
                    retryable=False,
                    fields={"view_id": str(view_id)},
                )

            current_version_info: dict[str, Any] | None = None
            if view.current_version > 0:
                version = await ResearchRepository.get_view_version(
                    session, view_id, view.current_version
                )
                if version is not None:
                    current_version_info = {
                        "version_id": str(version.id),
                        "version_number": version.version_number,
                        "image_storage_path": version.image_storage_path,
                        "image_format": version.image_format,
                        "image_width": version.image_width,
                        "image_height": version.image_height,
                        "image_content_hash": version.image_content_hash,
                        "chart_code_artifact_id": str(version.chart_code_artifact_id)
                        if version.chart_code_artifact_id
                        else None,
                        "image_digest": version.image_digest,
                        "chart_description": version.chart_description,
                        "bound_dataset_version_id": str(version.bound_dataset_version_id)
                        if version.bound_dataset_version_id
                        else None,
                        "created_at": version.created_at.isoformat()
                        if version.created_at
                        else None,
                    }

            return ViewDetail(
                view_id=view.id,
                workspace_id=workspace_id,
                name=view.name,
                caption=view.caption,
                display_order=view.display_order,
                status=view.status,
                current_version=view.current_version,
                source_run_id=view.source_run_id,
                current_version_info=current_version_info,
            )

    async def update_view_metadata(
        self,
        workspace_id: UUID,
        view_id: UUID,
        name: str | None = None,
        caption: str | None = None,
        display_order: int | None = None,
    ) -> ViewRef:
        """编辑视图元数据（仅 stable identity 字段）。

        Args:
            workspace_id: 工作空间 ID。
            view_id: 视图 ID。
            name: 新名称（可选）。
            caption: 新图注（可选）。
            display_order: 新展示顺序（可选）。

        Returns:
            ViewRef: 更新后的视图引用。

        Raises:
            AppError: code="not_found"，当视图不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            view = await ResearchRepository.get_view(session, view_id, workspace_id)
            if view is None:
                raise AppError(
                    code="not_found",
                    message="视图不存在",
                    retryable=False,
                    fields={"view_id": str(view_id)},
                )

            await ResearchRepository.update_view_metadata(
                session,
                view_id,
                name=name,
                caption=caption,
                display_order=display_order,
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.view.edit",
                    actor_user_id=actor_id,
                    resource_type="research_view",
                    resource_id=view_id,
                    payload={
                        "name": name if name is not None else "(unchanged)",
                        "caption_updated": caption is not None,
                        "display_order_updated": display_order is not None,
                    },
                ),
            )

            return ViewRef(
                view_id=view.id,
                name=name if name is not None else view.name,
                status=view.status,
                current_version=view.current_version,
                caption=caption if caption is not None else view.caption,
                display_order=display_order if display_order is not None else view.display_order,
            )

    async def list_view_versions(
        self,
        workspace_id: UUID,
        view_id: UUID,
    ) -> list[ViewVersionRef]:
        """列出视图版本历史。

        Args:
            workspace_id: 工作空间 ID。
            view_id: 视图 ID。

        Returns:
            list[ViewVersionRef]: 版本引用列表。
        """
        async with self._scoped_session() as session:
            versions = await ResearchRepository.list_view_versions(session, view_id)
            return [
                ViewVersionRef(
                    version_id=v.id,
                    view_id=view_id,
                    version_number=v.version_number,
                    image_storage_path=v.image_storage_path,
                    image_format=v.image_format,
                    created_at=v.created_at,
                )
                for v in versions
            ]

    async def get_view_version(
        self,
        workspace_id: UUID,
        view_id: UUID,
        version_number: int,
    ) -> ViewVersionDetail:
        """获取视图版本详情。

        Args:
            workspace_id: 工作空间 ID。
            view_id: 视图 ID。
            version_number: 版本号。

        Returns:
            ViewVersionDetail: 版本详情。

        Raises:
            AppError: code="not_found"，当版本不存在时。
        """
        async with self._scoped_session() as session:
            version = await ResearchRepository.get_view_version(session, view_id, version_number)
            if version is None:
                raise AppError(
                    code="not_found",
                    message="视图版本不存在",
                    retryable=False,
                    fields={
                        "view_id": str(view_id),
                        "version_number": version_number,
                    },
                )

            return ViewVersionDetail(
                version_id=version.id,
                view_id=view_id,
                version_number=version.version_number,
                image_storage_path=version.image_storage_path,
                image_format=version.image_format,
                image_width=version.image_width,
                image_height=version.image_height,
                image_content_hash=version.image_content_hash,
                chart_code_artifact_id=version.chart_code_artifact_id,
                image_digest=version.image_digest,
                source_run_id=version.source_run_id,
                source_step_id=version.source_step_id,
                source_artifact_id=version.source_artifact_id,
                bound_dataset_version_id=version.bound_dataset_version_id,
                chart_description=version.chart_description,
                created_at=version.created_at,
            )

    # ============================================================
    # Insight
    # ============================================================

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

                result = await session.execute(
                    sa.text(
                        "SELECT id, dag_structure FROM research_analysis_plan_version "
                        "WHERE workspace_id = :wid ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"wid": str(workspace_id)},
                )
                row = result.fetchone()
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

            result = InsightRef(  # type: ignore[assignment]
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

        return result  # type: ignore[return-value]

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

    async def delete_dataset(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
    ) -> None:
        """删除 DerivedDataset（物理删除，连同版本）。"""
        async with self._scoped_session() as session:
            await session.execute(
                sa.text("DELETE FROM research_derived_dataset_version WHERE dataset_id = :did"),
                {"did": str(dataset_id)},
            )
            await session.execute(
                sa.text(
                    "DELETE FROM research_derived_dataset WHERE id = :did AND workspace_id = :wid"
                ),
                {"did": str(dataset_id), "wid": str(workspace_id)},
            )

    async def delete_view(
        self,
        workspace_id: UUID,
        view_id: UUID,
    ) -> None:
        """删除 ResearchView（物理删除，连同版本）。"""
        async with self._scoped_session() as session:
            await session.execute(
                sa.text("DELETE FROM research_view_version WHERE view_id = :vid"),
                {"vid": str(view_id)},
            )
            await session.execute(
                sa.text("DELETE FROM research_view WHERE id = :vid AND workspace_id = :wid"),
                {"vid": str(view_id), "wid": str(workspace_id)},
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

    # ============================================================
    # 产物列表
    # ============================================================

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
