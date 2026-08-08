"""研究洞察子仓库。

封装三类数据库操作：
- ResearchInsight（稳定身份）：插入、查询、列表、元数据/版本更新；
- ResearchInsightVersion（不可变版本）：插入、查询、列表、最新版本；
- ResearchInsightCandidate（候选）：插入、获取、列表、状态更新。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import (
    ResearchInsight,
    ResearchInsightCandidate,
    ResearchInsightVersion,
)


class InsightRepository:
    """研究洞察数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    # ---- ResearchInsight ----

    @staticmethod
    async def insert_insight(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        name: str,
        status: str = "confirmed",
        source_run_id: UUID | None = None,
    ) -> ResearchInsight:
        """插入 Insight 稳定身份行。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            owner_user_id: 所有者用户 ID。
            name: 名称。
            status: 状态。
            source_run_id: 来源 Run ID（可选）。

        Returns:
            ResearchInsight: Insight ORM 实体。
        """
        insight = ResearchInsight(
            id=new_id(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=name,
            status=status,
            current_version=0,
            source_run_id=source_run_id,
            lock_version=0,
        )
        session.add(insight)
        await session.flush()
        return insight

    @staticmethod
    async def get_insight(
        session: AsyncSession,
        insight_id: UUID,
        workspace_id: UUID | None = None,
    ) -> ResearchInsight | None:
        """获取 Insight（可选校验 workspace 归属）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            workspace_id: 工作空间 ID（可选过滤）。

        Returns:
            ResearchInsight | None: Insight 实体。
        """
        stmt = sa.select(ResearchInsight).where(ResearchInsight.id == insight_id)
        if workspace_id is not None:
            stmt = stmt.where(ResearchInsight.workspace_id == workspace_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_insights(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[ResearchInsight]:
        """列出工作空间内的 Insight。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。

        Returns:
            list[ResearchInsight]: Insight 列表。
        """
        result = await session.execute(
            sa.select(ResearchInsight)
            .where(ResearchInsight.workspace_id == workspace_id)
            .order_by(ResearchInsight.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_insight_metadata(
        session: AsyncSession,
        insight_id: UUID,
        *,
        name: str | None = None,
    ) -> None:
        """更新 Insight 元数据（仅 name）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            name: 新名称（可选）。
        """
        values: dict[str, Any] = {"updated_at": sa.func.now()}
        if name is not None:
            values["name"] = name
        await session.execute(
            sa.update(ResearchInsight).where(ResearchInsight.id == insight_id).values(**values)
        )

    @staticmethod
    async def update_insight_current_version(
        session: AsyncSession,
        insight_id: UUID,
        version_number: int,
    ) -> None:
        """更新 Insight 当前版本号。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 新版本号。
        """
        await session.execute(
            sa.update(ResearchInsight)
            .where(ResearchInsight.id == insight_id)
            .values(current_version=version_number, updated_at=sa.func.now())
        )

    # ---- ResearchInsightVersion（不可变）----

    @staticmethod
    async def insert_insight_version(
        session: AsyncSession,
        *,
        insight_id: UUID,
        version_number: int,
        conclusion: str,
        scope: str,
        evidence_refs: list[Any],
        method_refs: list[Any],
        confidence_level: str,
        limitations: str,
        evidence_source_label: str,
        ai_original_text: str | None = None,
        is_modified: bool = False,
        modification_note: str | None = None,
        source_candidate_id: UUID | None = None,
        source_run_id: UUID | None = None,
        created_by: UUID,
    ) -> ResearchInsightVersion:
        """插入 Insight 版本（不可变）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 版本号。
            conclusion: 结论。
            scope: 适用范围。
            evidence_refs: 证据引用列表。
            method_refs: 方法引用列表。
            confidence_level: 置信说明。
            limitations: 限制条件。
            evidence_source_label: 证据来源标签。
            ai_original_text: AI 原稿（可选）。
            is_modified: 是否被修改。
            modification_note: 修改原因（可选）。
            source_candidate_id: 来源候选 ID（可选）。
            source_run_id: 来源 Run ID（可选）。
            created_by: 创建人 ID。

        Returns:
            ResearchInsightVersion: 版本 ORM 实体。
        """
        version = ResearchInsightVersion(
            id=new_id(),
            insight_id=insight_id,
            version_number=version_number,
            conclusion=conclusion,
            scope=scope,
            evidence_refs=evidence_refs,
            method_refs=method_refs,
            confidence_level=confidence_level,
            limitations=limitations,
            evidence_source_label=evidence_source_label,
            ai_original_text=ai_original_text,
            is_modified=is_modified,
            modification_note=modification_note,
            source_candidate_id=source_candidate_id,
            source_run_id=source_run_id,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    async def get_insight_version(
        session: AsyncSession,
        insight_id: UUID,
        version_number: int,
    ) -> ResearchInsightVersion | None:
        """获取 Insight 版本。

        Args:
            session: 异步会话。
            insight_id: Insight ID。
            version_number: 版本号。

        Returns:
            ResearchInsightVersion | None: 版本实体。
        """
        result = await session.execute(
            sa.select(ResearchInsightVersion).where(
                ResearchInsightVersion.insight_id == insight_id,
                ResearchInsightVersion.version_number == version_number,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_insight_versions(
        session: AsyncSession,
        insight_id: UUID,
    ) -> list[ResearchInsightVersion]:
        """列出 Insight 的全部版本（按版本号降序）。

        Args:
            session: 异步会话。
            insight_id: Insight ID。

        Returns:
            list[ResearchInsightVersion]: 版本列表。
        """
        result = await session.execute(
            sa.select(ResearchInsightVersion)
            .where(ResearchInsightVersion.insight_id == insight_id)
            .order_by(ResearchInsightVersion.version_number.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_insight_version(
        session: AsyncSession,
        insight_id: UUID,
    ) -> ResearchInsightVersion | None:
        """获取 Insight 的最新版本。

        Args:
            session: 异步会话。
            insight_id: Insight ID。

        Returns:
            ResearchInsightVersion | None: 最新版本。
        """
        result = await session.execute(
            sa.select(ResearchInsightVersion)
            .where(ResearchInsightVersion.insight_id == insight_id)
            .order_by(ResearchInsightVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ---- ResearchInsightCandidate ----

    @staticmethod
    async def insert_insight_candidate(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        run_id: UUID,
        step_id: UUID | None = None,
        conclusion: str,
        scope: str,
        evidence_refs: list[Any],
        method_refs: list[Any],
        confidence_level: str,
        limitations: str,
        evidence_source_label: str,
        ai_raw_text: str,
        status: str = "pending",
    ) -> ResearchInsightCandidate:
        """插入 Insight 候选。

        Args:
            session: 异步会话。
            workspace_id: 工作空间 ID。
            run_id: Run ID。
            step_id: 步骤 ID（可选）。
            conclusion: 结论。
            scope: 适用范围。
            evidence_refs: 证据引用列表。
            method_refs: 方法引用列表。
            confidence_level: 置信说明。
            limitations: 限制条件。
            evidence_source_label: 证据来源标签。
            ai_raw_text: AI 原始回答文本。
            status: 状态（默认 pending）。

        Returns:
            ResearchInsightCandidate: 候选 ORM 实体。
        """
        candidate = ResearchInsightCandidate(
            id=new_id(),
            workspace_id=workspace_id,
            run_id=run_id,
            step_id=step_id,
            conclusion=conclusion,
            scope=scope,
            evidence_refs=evidence_refs,
            method_refs=method_refs,
            confidence_level=confidence_level,
            limitations=limitations,
            evidence_source_label=evidence_source_label,
            ai_raw_text=ai_raw_text,
            status=status,
        )
        session.add(candidate)
        await session.flush()
        return candidate

    @staticmethod
    async def get_insight_candidate(
        session: AsyncSession,
        candidate_id: UUID,
    ) -> ResearchInsightCandidate | None:
        """获取 Insight 候选。

        Args:
            session: 异步会话。
            candidate_id: 候选 ID。

        Returns:
            ResearchInsightCandidate | None: 候选实体。
        """
        result = await session.execute(
            sa.select(ResearchInsightCandidate).where(ResearchInsightCandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_insight_candidates(
        session: AsyncSession,
        run_id: UUID,
        status: str | None = None,
    ) -> list[ResearchInsightCandidate]:
        """列出 Run 的 Insight 候选。

        Args:
            session: 异步会话。
            run_id: Run ID。
            status: 状态过滤（可选）。

        Returns:
            list[ResearchInsightCandidate]: 候选列表。
        """
        stmt = sa.select(ResearchInsightCandidate).where(ResearchInsightCandidate.run_id == run_id)
        if status is not None:
            stmt = stmt.where(ResearchInsightCandidate.status == status)
        stmt = stmt.order_by(ResearchInsightCandidate.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_insight_candidate_status(
        session: AsyncSession,
        candidate_id: UUID,
        status: str,
        *,
        accepted_insight_id: UUID | None = None,
        rejection_reason: str | None = None,
        reviewed_by: UUID | None = None,
    ) -> None:
        """更新 Insight 候选状态。

        Args:
            session: 异步会话。
            candidate_id: 候选 ID。
            status: 新状态。
            accepted_insight_id: 接受后创建的 Insight ID（可选）。
            rejection_reason: 拒绝原因（可选）。
            reviewed_by: 审核人 ID（可选）。
        """
        values: dict[str, Any] = {
            "status": status,
            "reviewed_at": sa.func.now(),
        }
        if accepted_insight_id is not None:
            values["accepted_insight_id"] = accepted_insight_id
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        if reviewed_by is not None:
            values["reviewed_by"] = reviewed_by
        await session.execute(
            sa.update(ResearchInsightCandidate)
            .where(ResearchInsightCandidate.id == candidate_id)
            .values(**values)
        )
