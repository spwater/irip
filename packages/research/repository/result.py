"""成果包子仓库。

封装三类数据库操作：
- ResearchResult（稳定身份）：插入、查询、列表、版本/ACL/元数据/状态更新、
  跨用户列表与按 Workspace 计数；
- ResearchResultVersion（不可变版本）：插入、查询、最新/列表、状态更新、搜索；
- ResearchResultAclRevision（仅追加 ACL 修订）：插入、最新/列表。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import (
    ResearchResult,
    ResearchResultAclRevision,
    ResearchResultVersion,
)


class ResultRepository:
    """成果包数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    # ---- ResearchResult ----

    @staticmethod
    async def insert_result(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        name: str,
        status: str = "published",
        current_version: int = 0,
        current_acl_type: str = "private",
        current_explicit_user_ids: list[Any] | None = None,
    ) -> ResearchResult:
        """插入成果包稳定身份行。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID。
            name: 成果包名称。
            status: 状态（默认 published）。
            current_version: 当前版本号（默认 0）。
            current_acl_type: 当前 ACL 类型（默认 private）。
            current_explicit_user_ids: 指定用户列表（可选）。

        Returns:
            ResearchResult: 成果包 ORM 实体。
        """
        result = ResearchResult(
            id=new_id(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=name,
            status=status,
            current_version=current_version,
            current_acl_type=current_acl_type,
            current_explicit_user_ids=current_explicit_user_ids
            if current_explicit_user_ids is not None
            else [],
            lock_version=0,
        )
        session.add(result)
        await session.flush()
        return result

    @staticmethod
    async def get_result(
        session: AsyncSession,
        result_id: UUID,
    ) -> ResearchResult | None:
        """获取成果包（无 owner 过滤，用于跨用户查询）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            ResearchResult | None: 成果包实体。
        """
        res = await session.execute(sa.select(ResearchResult).where(ResearchResult.id == result_id))
        return res.scalar_one_or_none()

    @staticmethod
    async def get_result_by_owner(
        session: AsyncSession,
        result_id: UUID,
        owner_user_id: UUID,
    ) -> ResearchResult | None:
        """获取成果包并校验所有者归属。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            owner_user_id: 所有者用户 ID。

        Returns:
            ResearchResult | None: 成果包实体。
        """
        res = await session.execute(
            sa.select(ResearchResult).where(
                ResearchResult.id == result_id,
                ResearchResult.owner_user_id == owner_user_id,
            )
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_results_by_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchResult]:
        """列出工作空间内的全部成果包。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchResult]: 成果包列表。
        """
        res = await session.execute(
            sa.select(ResearchResult)
            .where(ResearchResult.workspace_id == workspace_id)
            .order_by(ResearchResult.created_at.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def update_result_current_version(
        session: AsyncSession,
        result_id: UUID,
        version_number: int,
    ) -> None:
        """更新成果包当前版本号。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(
                current_version=version_number,
                updated_at=sa.func.now(),
            )
        )

    @staticmethod
    async def update_result_acl(
        session: AsyncSession,
        result_id: UUID,
        acl_type: str,
        explicit_user_ids: list[Any],
    ) -> None:
        """更新成果包当前 ACL。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            acl_type: 新 ACL 类型。
            explicit_user_ids: 指定用户列表。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(
                current_acl_type=acl_type,
                current_explicit_user_ids=explicit_user_ids,
                updated_at=sa.func.now(),
            )
        )

    @staticmethod
    async def update_result_metadata(
        session: AsyncSession,
        result_id: UUID,
        name: str,
    ) -> None:
        """更新成果包元数据（仅 name）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            name: 新名称。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(name=name, updated_at=sa.func.now())
        )

    @staticmethod
    async def update_result_status(
        session: AsyncSession,
        result_id: UUID,
        status: str,
    ) -> None:
        """更新成果包状态。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            status: 新状态。
        """
        await session.execute(
            sa.update(ResearchResult)
            .where(ResearchResult.id == result_id)
            .values(status=status, updated_at=sa.func.now())
        )

    @staticmethod
    async def list_published_results(
        session: AsyncSession,
    ) -> list[ResearchResult]:
        """列出全部已发布成果包（跨用户，ACL 过滤由 Service 层处理）。

        Args:
            session: 异步会话。

        Returns:
            list[ResearchResult]: 已发布成果包列表。
        """
        res = await session.execute(
            sa.select(ResearchResult)
            .where(ResearchResult.status == "published")
            .order_by(ResearchResult.updated_at.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def count_published_results_by_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """统计工作空间内已发布成果包数量（用于删除检查）。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            int: 已发布成果包数量。
        """
        res = await session.execute(
            sa.select(sa.func.count())
            .select_from(ResearchResult)
            .where(
                ResearchResult.workspace_id == workspace_id,
                ResearchResult.status == "published",
            )
        )
        return int(res.scalar() or 0)

    # ---- ResearchResultVersion（不可变）----

    @staticmethod
    async def insert_result_version(
        session: AsyncSession,
        *,
        result_id: UUID,
        version_number: int,
        title: str,
        summary: str | None,
        tags: list[Any],
        release_notes: str | None,
        dataset_version_refs: list[Any],
        view_version_refs: list[Any],
        insight_version_refs: list[Any],
        evidence_snapshot_ids: list[Any],
        analysis_run_ids: list[Any],
        source_run_statuses: dict[str, Any],
        publisher: UUID,
        content_hash: str,
        published_permission_envelope: dict[str, Any],
        status: str = "active",
    ) -> ResearchResultVersion:
        """插入成果包版本（不可变）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            version_number: 版本号。
            title: 标题。
            summary: 摘要。
            tags: 标签列表。
            release_notes: 发布说明。
            dataset_version_refs: DerivedDataset 版本引用列表。
            view_version_refs: ResearchView 版本引用列表。
            insight_version_refs: Insight 版本引用列表。
            evidence_snapshot_ids: Evidence Snapshot ID 列表。
            analysis_run_ids: Analysis Run ID 列表。
            source_run_statuses: Run 状态映射。
            publisher: 发布者 ID。
            content_hash: 内容哈希。
            published_permission_envelope: 权限包络快照。
            status: 版本状态（默认 active）。

        Returns:
            ResearchResultVersion: 版本 ORM 实体。
        """
        version = ResearchResultVersion(
            id=new_id(),
            result_id=result_id,
            version_number=version_number,
            title=title,
            summary=summary,
            tags=tags,
            release_notes=release_notes,
            dataset_version_refs=dataset_version_refs,
            view_version_refs=view_version_refs,
            insight_version_refs=insight_version_refs,
            evidence_snapshot_ids=evidence_snapshot_ids,
            analysis_run_ids=analysis_run_ids,
            source_run_statuses=source_run_statuses,
            publisher=publisher,
            content_hash=content_hash,
            published_permission_envelope=published_permission_envelope,
            status=status,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_result_version(
        session: AsyncSession,
        result_id: UUID,
        version_number: int,
    ) -> ResearchResultVersion | None:
        """获取成果包版本。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            version_number: 版本号。

        Returns:
            ResearchResultVersion | None: 版本实体。
        """
        res = await session.execute(
            sa.select(ResearchResultVersion).where(
                ResearchResultVersion.result_id == result_id,
                ResearchResultVersion.version_number == version_number,
            )
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def get_latest_result_version(
        session: AsyncSession,
        result_id: UUID,
    ) -> ResearchResultVersion | None:
        """获取成果包最新版本。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            ResearchResultVersion | None: 最新版本。
        """
        res = await session.execute(
            sa.select(ResearchResultVersion)
            .where(ResearchResultVersion.result_id == result_id)
            .order_by(ResearchResultVersion.version_number.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_result_versions(
        session: AsyncSession,
        result_id: UUID,
    ) -> list[ResearchResultVersion]:
        """列出成果包的全部版本（按版本号降序）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            list[ResearchResultVersion]: 版本列表。
        """
        res = await session.execute(
            sa.select(ResearchResultVersion)
            .where(ResearchResultVersion.result_id == result_id)
            .order_by(ResearchResultVersion.version_number.desc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def update_result_version_status(
        session: AsyncSession,
        version_id: UUID,
        status: str,
    ) -> None:
        """更新版本状态（仅 status 字段，其他字段不可变）。

        Args:
            session: 异步会话。
            version_id: 版本 ID。
            status: 新状态。
        """
        await session.execute(
            sa.update(ResearchResultVersion)
            .where(ResearchResultVersion.id == version_id)
            .values(status=status)
        )

    @staticmethod
    async def search_result_versions(
        session: AsyncSession,
        query: str | None,
        result_ids: list[UUID] | None = None,
    ) -> list[ResearchResultVersion]:
        """搜索成果包版本（关键词匹配 title / summary / tags）。

        Args:
            session: 异步会话。
            query: 关键词（ILIKE 模糊匹配）。
            result_ids: 成果包 ID 过滤列表（可选，用于 ACL 过滤后的二次查询）。

        Returns:
            list[ResearchResultVersion]: 搜索结果列表。
        """
        stmt = sa.select(ResearchResultVersion).where(ResearchResultVersion.status == "active")
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                sa.or_(
                    ResearchResultVersion.title.ilike(pattern),
                    ResearchResultVersion.summary.ilike(pattern),
                    ResearchResultVersion.tags.cast(sa.Text).ilike(pattern),
                )
            )
        if result_ids is not None:
            stmt = stmt.where(ResearchResultVersion.result_id.in_(result_ids))
        stmt = stmt.order_by(ResearchResultVersion.published_at.desc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    # ---- ResearchResultAclRevision（仅追加）----

    @staticmethod
    async def insert_acl_revision(
        session: AsyncSession,
        *,
        result_id: UUID,
        revision_number: int,
        acl_type: str,
        explicit_user_ids: list[Any],
        previous_acl_type: str | None = None,
        previous_explicit_user_ids: list[Any] | None = None,
        changed_by: UUID,
        change_reason: str | None = None,
        is_declassify: bool = False,
        declassify_reason: str | None = None,
    ) -> ResearchResultAclRevision:
        """插入 ACL 修订记录（仅追加，不可变）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。
            revision_number: 修订号。
            acl_type: ACL 类型。
            explicit_user_ids: 指定用户列表。
            previous_acl_type: 变更前 ACL 类型。
            previous_explicit_user_ids: 变更前指定用户列表。
            changed_by: 变更者 ID。
            change_reason: 变更原因。
            is_declassify: 是否为 declassify 操作。
            declassify_reason: declassify 理由。

        Returns:
            ResearchResultAclRevision: 修订记录 ORM 实体。
        """
        revision = ResearchResultAclRevision(
            id=new_id(),
            result_id=result_id,
            revision_number=revision_number,
            acl_type=acl_type,
            explicit_user_ids=explicit_user_ids,
            previous_acl_type=previous_acl_type,
            previous_explicit_user_ids=previous_explicit_user_ids,
            changed_by=changed_by,
            change_reason=change_reason,
            is_declassify=is_declassify,
            declassify_reason=declassify_reason,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def get_latest_acl_revision(
        session: AsyncSession,
        result_id: UUID,
    ) -> ResearchResultAclRevision | None:
        """获取成果包最新的 ACL 修订记录。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            ResearchResultAclRevision | None: 最新修订记录。
        """
        res = await session.execute(
            sa.select(ResearchResultAclRevision)
            .where(ResearchResultAclRevision.result_id == result_id)
            .order_by(ResearchResultAclRevision.revision_number.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def list_acl_revisions(
        session: AsyncSession,
        result_id: UUID,
    ) -> list[ResearchResultAclRevision]:
        """列出成果包的全部 ACL 修订记录（按修订号升序）。

        Args:
            session: 异步会话。
            result_id: 成果包 ID。

        Returns:
            list[ResearchResultAclRevision]: 修订记录列表。
        """
        res = await session.execute(
            sa.select(ResearchResultAclRevision)
            .where(ResearchResultAclRevision.result_id == result_id)
            .order_by(ResearchResultAclRevision.revision_number.asc())
        )
        return list(res.scalars().all())
