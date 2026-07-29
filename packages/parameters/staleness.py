"""参数过期状态检查器（IRIP Task 18）。

StalenessChecker 跟踪参数版本对事实修订的依赖关系。当事实产生新修订时，
依赖该事实的参数版本变为 review_required。

核心逻辑：
1. 参数版本发布时，为证据集中每个事实修订创建 staleness 跟踪条目；
2. 检查时对比每个跟踪的事实修订号与事实的 current_revision；
3. 若 current_revision > 跟踪的修订号 → 该事实有新修订 → review_required；
4. 更新 staleness 条目的 review_state。

依赖注入 session_factory（事务管理）、organization_id（当前组织）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.ids import new_id
from packages.facts.entities import Fact, FactRevision
from packages.parameters.entities import ParameterStaleness


class StalenessChecker:
    """参数过期状态检查器。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
    ) -> None:
        """初始化过期状态检查器。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
        """
        self._factory = session_factory
        self._org_id = organization_id

    async def check_parameter(self, parameter_version_id: UUID) -> str:
        """检查参数版本的过期状态。

        流程：
        1. 加载该参数版本的所有 staleness 跟踪条目；
        2. 对每个条目，检查对应事实是否有比跟踪修订更新的修订；
        3. 若任一事实有新修订 → review_required；
        4. 若全部事实仍为当前修订 → current；
        5. 更新 staleness 条目的 review_state 和 last_checked_at。

        Args:
            parameter_version_id: 参数版本 UUID。

        Returns:
            str: 过期状态（"current" 或 "review_required"）。
        """
        async with session_scope(self._factory) as session:
            # 1. 加载 staleness 跟踪条目
            result = await session.execute(
                sa.select(ParameterStaleness).where(
                    ParameterStaleness.parameter_version_id == parameter_version_id
                )
            )
            entries = result.scalars().all()

            if not entries:
                # 无跟踪条目 → 视为 current
                return "current"

            overall_state: str = "current"
            now: datetime = datetime.now(UTC)

            for entry in entries:
                # 2. 加载跟踪的事实修订
                fact_rev = await session.scalar(
                    sa.select(FactRevision).where(FactRevision.id == entry.fact_revision_id)
                )
                if fact_rev is None:
                    # 事实修订被删除 → 标记为 review_required
                    entry.review_state = "review_required"
                    entry.last_checked_at = now
                    overall_state = "review_required"
                    continue

                # 3. 加载事实，检查 current_revision
                fact = await session.scalar(sa.select(Fact).where(Fact.id == fact_rev.fact_id))
                if fact is None:
                    entry.review_state = "review_required"
                    entry.last_checked_at = now
                    overall_state = "review_required"
                    continue

                if fact.current_revision > fact_rev.revision:
                    # 事实有更新的修订 → 过期
                    entry.review_state = "review_required"
                    entry.last_checked_at = now
                    overall_state = "review_required"
                else:
                    # 事实仍为当前修订 → 当前
                    entry.review_state = "current"
                    entry.last_checked_at = now

            return overall_state

    async def mark_stale(self, parameter_version_id: UUID, fact_revision_id: UUID) -> None:
        """标记参数版本因特定事实修订而过期。

        查找对应的 staleness 跟踪条目并更新为 review_required。
        若条目不存在则创建。

        Args:
            parameter_version_id: 参数版本 UUID。
            fact_revision_id: 事实修订 UUID。
        """
        async with session_scope(self._factory) as session:
            entry = await session.scalar(
                sa.select(ParameterStaleness).where(
                    ParameterStaleness.parameter_version_id == parameter_version_id,
                    ParameterStaleness.fact_revision_id == fact_revision_id,
                )
            )
            now: datetime = datetime.now(UTC)
            if entry is not None:
                entry.review_state = "review_required"
                entry.last_checked_at = now
            else:
                new_entry = ParameterStaleness(
                    id=new_id(),
                    parameter_version_id=parameter_version_id,
                    fact_revision_id=fact_revision_id,
                    review_state="review_required",
                    last_checked_at=now,
                )
                session.add(new_entry)
            await session.flush()
