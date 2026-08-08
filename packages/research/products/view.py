"""视图 Mixin：ResearchView 生命周期管理。

拆分自 products.py（IRIP 拆分任务）。``ViewMixin`` 承载
从 RunArtifact 创建 ResearchView、列表、详情、元数据编辑、版本历史与删除
等完整生命周期管理。
"""

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.errors import AppError
from packages.research.dtos import (
    ViewDetail,
    ViewRef,
    ViewVersionDetail,
    ViewVersionRef,
)
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.products.product_base import ProductServiceBase
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.products")


class ViewMixin(ProductServiceBase):
    """视图功能域：ResearchView CRUD。"""

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
