"""证据集服务（IRIP Task 17）。

EvidenceService 提供证据集的创建、冻结、查询与成员管理。

核心不变量：
1. frozen_immutable: 冻结后的证据集版本不可修改，保证可复现推导。
2. exact_revisions: 冻结时记录每个成员的精确事实修订号和修订 ID。
3. quality_filter: 冻结时可按质量状态过滤，仅纳入质量通过的事实修订。

依赖注入 session_factory（事务管理）、organization_id（当前组织）、
actor_id（操作人）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.entities import Fact, FactRevision
from packages.provenance.entities import EvidenceSet, EvidenceSetVersion


@dataclass(frozen=True)
class EvidenceMember:
    """证据集成员（引用特定事实修订）。

    冻结时创建，不可变。记录精确的事实修订号和修订 ID，
    保证后续推导可复现。

    Attributes:
        fact_id: 事实 UUID。
        fact_revision: 修订号。
        fact_revision_id: 修订 UUID。
        observation_id: 特定观察值 UUID（可选）。
        decision: 纳入决定（included / excluded）。
        reason: 决定原因。
    """

    fact_id: UUID
    fact_revision: int
    fact_revision_id: UUID
    observation_id: UUID | None
    decision: Literal["included", "excluded"]
    reason: str

    def to_dict(self) -> dict:
        """序列化为 JSONB 可存储的字典。"""
        return {
            "fact_id": str(self.fact_id),
            "fact_revision": self.fact_revision,
            "fact_revision_id": str(self.fact_revision_id),
            "observation_id": str(self.observation_id) if self.observation_id else None,
            "decision": self.decision,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EvidenceMember:
        """从字典反序列化。"""
        return cls(
            fact_id=UUID(str(d["fact_id"])),
            fact_revision=int(d["fact_revision"]),
            fact_revision_id=UUID(str(d["fact_revision_id"])),
            observation_id=UUID(str(d["observation_id"])) if d.get("observation_id") else None,
            decision=d["decision"],  # type: ignore[arg-type]
            reason=d.get("reason", ""),
        )


@dataclass(frozen=True)
class EvidenceSetRef:
    """证据集引用（不可变值对象）。

    Attributes:
        set_id: 证据集 UUID。
        version: 版本号。
        version_id: 版本 UUID。
        member_count: 成员数量。
        status: 状态（frozen）。
    """

    set_id: UUID
    version: int
    version_id: UUID
    member_count: int
    status: str


class EvidenceService:
    """证据集业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID（用于 created_by）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化证据集服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选，用于 created_by）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_set(self, name: str) -> dict:
        """创建空的证据集（draft 状态）。

        Args:
            name: 证据集名称。

        Returns:
            dict: 包含 set_id 和 name 的字典。

        Raises:
            AppError: code="validation_failed"，当 name 为空时。
        """
        if not name or not name.strip():
            raise AppError(
                code="validation_failed",
                message="证据集名称不能为空",
                retryable=False,
                fields={"name": "required"},
            )

        async with session_scope(self._factory) as session:
            evidence_set = EvidenceSet(
                id=new_id(),
                organization_id=self._org_id,
                name=name.strip(),
                status="draft",
                lock_version=0,
                created_by=self._actor_id,
            )
            session.add(evidence_set)
            await session.flush()
            return {
                "set_id": evidence_set.id,
                "name": evidence_set.name,
                "status": evidence_set.status,
            }

    async def freeze(
        self,
        set_id: UUID,
        fact_filter: dict | None = None,
    ) -> EvidenceSetRef:
        """冻结证据集：查询符合条件的事实修订，创建不可变版本。

        流程：
        1. 加载证据集（必须存在且为 draft 状态）；
        2. 查询当前组织下的活跃事实的最新修订；
        3. 可选按质量过滤（fact_filter={"quality": "passed"}）；
        4. 为每个事实修订创建 EvidenceMember（decision="included"）；
        5. 创建 evidence_set_version（不可变，members JSONB）；
        6. 更新 evidence_set status 为 frozen；
        7. 返回 EvidenceSetRef。

        Args:
            set_id: 证据集 UUID。
            fact_filter: 过滤条件，如 {"quality": "passed"}。

        Returns:
            EvidenceSetRef: 证据集版本引用。

        Raises:
            AppError: code="not_found"，当证据集不存在时。
            AppError: code="evidence_not_frozen"，当证据集已冻结时。
        """
        async with session_scope(self._factory) as session:
            # 1. 加载证据集
            evidence_set = await session.scalar(
                sa.select(EvidenceSet).where(
                    EvidenceSet.id == set_id,
                    EvidenceSet.organization_id == self._org_id,
                )
            )
            if evidence_set is None:
                raise AppError(
                    code="not_found",
                    message=f"证据集不存在: {set_id}",
                    retryable=False,
                    fields={"set_id": str(set_id)},
                )

            if evidence_set.status == "frozen":
                raise AppError(
                    code="evidence_not_frozen",
                    message="证据集已冻结，无法再次冻结",
                    retryable=False,
                    fields={"set_id": str(set_id)},
                )

            # 2. 查询当前组织下活跃事实的最新修订
            stmt = (
                sa.select(FactRevision)
                .join(Fact, FactRevision.fact_id == Fact.id)
                .where(
                    Fact.organization_id == self._org_id,
                    Fact.status == "active",
                    FactRevision.revision == Fact.current_revision,
                )
                .order_by(FactRevision.created_at)
            )

            # 3. 可选按质量过滤
            if fact_filter and fact_filter.get("quality") == "passed":
                # 关联 quality_assessment 表，筛选 overall_status = "passed"
                qa_table = sa.table(
                    "quality_assessment",
                    sa.column("fact_revision_id", sa.UUID),
                    sa.column("overall_status", sa.Text),
                )
                stmt = stmt.join(
                    qa_table,
                    qa_table.c.fact_revision_id == FactRevision.id,
                ).where(qa_table.c.overall_status == "passed")

            result = await session.execute(stmt)
            revisions = result.scalars().all()

            # 4. 为每个事实修订创建 EvidenceMember
            members: list[EvidenceMember] = []
            for rev in revisions:
                member = EvidenceMember(
                    fact_id=rev.fact_id,
                    fact_revision=rev.revision,
                    fact_revision_id=rev.id,
                    observation_id=None,
                    decision="included",
                    reason="自动纳入",
                )
                members.append(member)

            # 5. 创建 evidence_set_version
            version_number = 1
            version = EvidenceSetVersion(
                id=new_id(),
                evidence_set_id=set_id,
                version=version_number,
                status="frozen",
                members=[m.to_dict() for m in members],
                member_count=len(members),
                frozen_at=datetime.now(UTC),
            )
            session.add(version)

            # 6. 更新 evidence_set status
            await session.execute(
                sa.update(EvidenceSet)
                .values(
                    status="frozen",
                    updated_at=sa.func.now(),
                    lock_version=EvidenceSet.lock_version + 1,
                )
                .where(EvidenceSet.id == set_id)
            )

            await session.flush()

            return EvidenceSetRef(
                set_id=set_id,
                version=version_number,
                version_id=version.id,
                member_count=len(members),
                status="frozen",
            )

    async def get_set(self, set_id: UUID) -> dict:
        """获取证据集详情（含最新版本信息）。

        Args:
            set_id: 证据集 UUID。

        Returns:
            dict: 证据集详情，包含 set_id, name, status, version, member_count。

        Raises:
            AppError: code="not_found"，当证据集不存在时。
        """
        async with self._factory() as session:
            evidence_set = await session.scalar(
                sa.select(EvidenceSet).where(
                    EvidenceSet.id == set_id,
                    EvidenceSet.organization_id == self._org_id,
                )
            )
            if evidence_set is None:
                raise AppError(
                    code="not_found",
                    message=f"证据集不存在: {set_id}",
                    retryable=False,
                    fields={"set_id": str(set_id)},
                )

            # 查找最新版本
            latest_version = await session.scalar(
                sa.select(EvidenceSetVersion)
                .where(EvidenceSetVersion.evidence_set_id == set_id)
                .order_by(EvidenceSetVersion.version.desc())
                .limit(1)
            )

            return {
                "set_id": set_id,
                "name": evidence_set.name,
                "status": evidence_set.status,
                "version": latest_version.version if latest_version else 0,
                "version_id": latest_version.id if latest_version else None,
                "member_count": latest_version.member_count if latest_version else 0,
            }

    async def list_members(
        self,
        set_id: UUID,
        version: int | None = None,
    ) -> tuple[EvidenceMember, ...]:
        """列出证据集版本的成员。

        Args:
            set_id: 证据集 UUID。
            version: 版本号（None 表示最新版本）。

        Returns:
            tuple[EvidenceMember, ...]: 成员元组。

        Raises:
            AppError: code="not_found"，当证据集或版本不存在时。
        """
        async with self._factory() as session:
            # 校验证据集存在
            evidence_set = await session.scalar(
                sa.select(EvidenceSet).where(
                    EvidenceSet.id == set_id,
                    EvidenceSet.organization_id == self._org_id,
                )
            )
            if evidence_set is None:
                raise AppError(
                    code="not_found",
                    message=f"证据集不存在: {set_id}",
                    retryable=False,
                    fields={"set_id": str(set_id)},
                )

            # 查找版本
            if version is not None:
                version_row = await session.scalar(
                    sa.select(EvidenceSetVersion).where(
                        EvidenceSetVersion.evidence_set_id == set_id,
                        EvidenceSetVersion.version == version,
                    )
                )
            else:
                version_row = await session.scalar(
                    sa.select(EvidenceSetVersion)
                    .where(EvidenceSetVersion.evidence_set_id == set_id)
                    .order_by(EvidenceSetVersion.version.desc())
                    .limit(1)
                )

            if version_row is None:
                raise AppError(
                    code="not_found",
                    message=f"证据集版本不存在: set_id={set_id}, version={version}",
                    retryable=False,
                    fields={"set_id": str(set_id), "version": str(version)},
                )

            members_list = version_row.members or []
            return tuple(EvidenceMember.from_dict(m) for m in members_list)
