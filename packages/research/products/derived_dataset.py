"""派生数据集 Mixin：DerivedDataset 生命周期管理。

拆分自 products.py（IRIP 拆分任务）。``DerivedDatasetMixin`` 承载
从 RunArtifact 创建 DerivedDataset、列表、详情、元数据编辑、版本历史与删除
等完整生命周期管理。

核心不变量：
1. 版本实体创建后不可变（Repository 不提供 update/delete 方法）；
2. 编辑 API 仅接受 stable identity 元数据字段；
3. 非 publishable 工件不允许创建产物；
4. 所有写操作产生审计记录。
"""

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.dtos import (
    DatasetDetail,
    DatasetVersionDetail,
    DatasetVersionRef,
    DerivedDatasetRef,
)
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.execution.validation import ThreeSegmentValidator
from packages.research.products.product_base import ProductServiceBase
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.products")


class DerivedDatasetMixin(ProductServiceBase):
    """派生数据集功能域：DerivedDataset CRUD。"""

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
