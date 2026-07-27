"""L2 事实数据访问层（IRIP Task 15）。

FactRepository 封装所有事实相关的数据库操作：
- 事实/修订/观察值/工件的插入；
- 事实与修订的查询（含组织归属校验）；
- 观察值与工件的批量查询；
- 全文搜索（PostgreSQL tsvector + GIN 索引）；
- 幂等键查找。

所有方法均为 async，接受 AsyncSession 参数，由 FactService 通过
session_scope 事务上下文管理提交。
"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.entities import (
    Fact,
    FactArtifact,
    FactRevision,
    FactRevisionLink,
    NormalizedObservation,
    RawObservation,
)


def _encode_cursor(created_at: datetime, entity_id: UUID) -> str:
    """编码 keyset 分页游标。

    Args:
        created_at: 排序时间戳。
        entity_id: 唯一决胜键。

    Returns:
        str: base64url 编码的游标字符串。
    """
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(entity_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Args:
        cursor: base64url 编码的游标字符串。

    Returns:
        tuple[datetime, UUID]: (排序时间戳, 实体 ID)。

    Raises:
        AppError: code="invalid_cursor"，当游标格式不合法时。
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：base64url 解码失败",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：JSON 解析失败",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    if not isinstance(payload, dict) or "v" not in payload or "id" not in payload:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：缺少必要字段 v / id",
            retryable=False,
            fields={"cursor": cursor},
        )

    try:
        created_at = datetime.fromisoformat(str(payload["v"]))
    except (ValueError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：v 字段不是合法 ISO 时间",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        entity_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：id 字段不是合法 UUID",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    return created_at, entity_id


class FactRepository:
    """事实数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 FactService 通过 session_scope 管理。
    """

    @staticmethod
    async def insert_fact(
        session: AsyncSession,
        *,
        organization_id: UUID,
        template_version_id: UUID,
        fact_type: str,
        object_id: UUID,
        current_revision: int = 1,
        status: str = "active",
        idempotency_key: str | None = None,
        created_by: UUID | None = None,
    ) -> Fact:
        """插入事实行，返回 ORM 实体。

        Args:
            session: 异步会话。
            organization_id: 组织 ID。
            template_version_id: 模板版本 ID。
            fact_type: 事实类型。
            object_id: 工业对象 ID。
            current_revision: 当前修订号（默认 1）。
            status: 状态（默认 active）。
            idempotency_key: 幂等键（可选）。
            created_by: 创建人 ID（可选）。

        Returns:
            Fact: 事实 ORM 实体。
        """
        fact = Fact(
            id=new_id(),
            organization_id=organization_id,
            template_version_id=template_version_id,
            fact_type=fact_type,
            object_id=object_id,
            current_revision=current_revision,
            status=status,
            lock_version=0,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )
        session.add(fact)
        await session.flush()
        return fact

    @staticmethod
    async def insert_revision(
        session: AsyncSession,
        *,
        fact_id: UUID,
        revision: int,
        template_version_id: UUID,
        fact_type: str,
        object_id: UUID,
        subject_id: str,
        method_version_id: UUID | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        revision_reason: str | None = None,
        revision_summary: dict | None = None,
        created_by: UUID | None = None,
        task_code: str | None = None,
        task_name: str | None = None,
        department_name: str | None = None,
        operator: str | None = None,
        flow_run_id: UUID | None = None,
    ) -> FactRevision:
        """插入事实修订行，返回 ORM 实体。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            revision: 修订号。
            template_version_id: 模板版本 ID（创建时快照）。
            fact_type: 事实类型。
            object_id: 工业对象 ID。
            subject_id: 主体标识。
            method_version_id: 方法版本 ID（可选）。
            started_at: 开始时间。
            ended_at: 结束时间。
            revision_reason: 修订原因（修订 2+ 必填）。
            revision_summary: 质量评估摘要。
            created_by: 创建人 ID。

        Returns:
            FactRevision: 事实修订 ORM 实体。
        """
        rev = FactRevision(
            id=new_id(),
            fact_id=fact_id,
            revision=revision,
            template_version_id=template_version_id,
            fact_type=fact_type,
            object_id=object_id,
            subject_id=subject_id,
            method_version_id=method_version_id,
            started_at=started_at,
            ended_at=ended_at,
            revision_reason=revision_reason,
            revision_summary=revision_summary,
            created_by=created_by,
            task_code=task_code,
            task_name=task_name,
            department_name=department_name,
            operator=operator,
            flow_run_id=flow_run_id,
        )
        session.add(rev)
        await session.flush()
        return rev

    @staticmethod
    async def insert_raw_observations(
        session: AsyncSession,
        revision_id: UUID,
        raws: list[dict],
    ) -> list[RawObservation]:
        """批量插入原始观察值。

        Args:
            session: 异步会话。
            revision_id: 事实修订 ID。
            raws: 原始观察值字典列表，每项含
                {source_path, source_value, source_unit, source_name, artifact_id}。

        Returns:
            list[RawObservation]: 插入的原始观察值 ORM 实体列表。
        """
        if not raws:
            return []
        result: list[RawObservation] = []
        for raw in raws:
            obs = RawObservation(
                id=raw.get("id") or new_id(),
                fact_revision_id=revision_id,
                source_path=raw["source_path"],
                source_value=raw["source_value"],
                source_unit=raw.get("source_unit"),
                source_name=raw.get("source_name"),
                artifact_id=raw.get("artifact_id"),
            )
            session.add(obs)
            result.append(obs)
        await session.flush()
        return result

    @staticmethod
    async def insert_normalized_observations(
        session: AsyncSession,
        revision_id: UUID,
        normalized: list[dict],
    ) -> list[NormalizedObservation]:
        """批量插入标准化观察值。

        Args:
            session: 异步会话。
            revision_id: 事实修订 ID。
            normalized: 标准化观察值字典列表，每项含
                {variable_version_id, raw_observation_id, value, unit}。

        Returns:
            list[NormalizedObservation]: 插入的标准化观察值 ORM 实体列表。
        """
        if not normalized:
            return []
        result: list[NormalizedObservation] = []
        for norm in normalized:
            obs = NormalizedObservation(
                id=new_id(),
                fact_revision_id=revision_id,
                variable_version_id=norm["variable_version_id"],
                raw_observation_id=norm["raw_observation_id"],
                value=norm["value"],
                unit=norm.get("unit"),
            )
            session.add(obs)
            result.append(obs)
        await session.flush()
        return result

    @staticmethod
    async def insert_artifacts(
        session: AsyncSession,
        revision_id: UUID,
        artifacts: list[dict],
    ) -> list[FactArtifact]:
        """批量插入事实-工件链接。

        Args:
            session: 异步会话。
            revision_id: 事实修订 ID。
            artifacts: 工件字典列表，每项含 {artifact_id, role}。

        Returns:
            list[FactArtifact]: 插入的链接 ORM 实体列表。
        """
        if not artifacts:
            return []
        result: list[FactArtifact] = []
        for art in artifacts:
            link = FactArtifact(
                id=new_id(),
                fact_revision_id=revision_id,
                artifact_id=art["artifact_id"],
                role=art["role"],
            )
            session.add(link)
            result.append(link)
        await session.flush()
        return result

    @staticmethod
    async def insert_revision_link(
        session: AsyncSession,
        from_revision_id: UUID,
        to_revision_id: UUID,
        link_type: str,
    ) -> FactRevisionLink:
        """插入修订链链接。

        Args:
            session: 异步会话。
            from_revision_id: 源修订 ID（新修订）。
            to_revision_id: 目标修订 ID（旧修订）。
            link_type: 链接类型（supersedes / corrects）。

        Returns:
            FactRevisionLink: 链接 ORM 实体。
        """
        link = FactRevisionLink(
            id=new_id(),
            from_revision_id=from_revision_id,
            to_revision_id=to_revision_id,
            link_type=link_type,
        )
        session.add(link)
        await session.flush()
        return link

    @staticmethod
    async def get_fact(
        session: AsyncSession,
        fact_id: UUID,
        org_id: UUID,
    ) -> Fact:
        """获取事实并校验组织归属。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            org_id: 组织 ID（用于校验归属）。

        Returns:
            Fact: 事实 ORM 实体。

        Raises:
            AppError: code="not_found"，当事实不存在或不属于该组织时。
        """
        result = await session.execute(
            sa.select(Fact).where(Fact.id == fact_id)
        )
        fact = result.scalar_one_or_none()
        if fact is None or fact.organization_id != org_id:
            raise AppError(
                code="not_found",
                message="事实不存在",
                retryable=False,
                fields={"fact_id": str(fact_id)},
            )
        return fact

    @staticmethod
    async def get_revision(
        session: AsyncSession,
        fact_id: UUID,
        revision: int,
        org_id: UUID,
    ) -> FactRevision:
        """获取特定修订并校验组织归属。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            revision: 修订号。
            org_id: 组织 ID。

        Returns:
            FactRevision: 事实修订 ORM 实体。

        Raises:
            AppError: code="not_found"，当事实或修订不存在时。
        """
        # 先校验事实存在且属于该组织
        await FactRepository.get_fact(session, fact_id, org_id)
        result = await session.execute(
            sa.select(FactRevision).where(
                FactRevision.fact_id == fact_id,
                FactRevision.revision == revision,
            )
        )
        rev = result.scalar_one_or_none()
        if rev is None:
            raise AppError(
                code="not_found",
                message=f"事实修订不存在: revision={revision}",
                retryable=False,
                fields={"fact_id": str(fact_id), "revision": revision},
            )
        return rev

    @staticmethod
    async def get_latest_revision(
        session: AsyncSession,
        fact_id: UUID,
        org_id: UUID,
    ) -> FactRevision:
        """获取最新修订。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            org_id: 组织 ID。

        Returns:
            FactRevision: 最新事实修订 ORM 实体。

        Raises:
            AppError: code="not_found"，当事实或修订不存在时。
        """
        fact = await FactRepository.get_fact(session, fact_id, org_id)
        result = await session.execute(
            sa.select(FactRevision)
            .where(FactRevision.fact_id == fact_id)
            .order_by(FactRevision.revision.desc())
            .limit(1)
        )
        rev = result.scalar_one_or_none()
        if rev is None:
            raise AppError(
                code="not_found",
                message=f"事实无修订: fact_id={fact_id}",
                retryable=False,
                fields={"fact_id": str(fact_id)},
            )
        # 用 fact.current_revision 确认
        assert rev.revision == fact.current_revision  # noqa: S101
        return rev

    @staticmethod
    async def get_revisions(
        session: AsyncSession,
        fact_id: UUID,
        org_id: UUID,
    ) -> list[FactRevision]:
        """获取事实的所有修订（按修订号升序）。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            org_id: 组织 ID。

        Returns:
            list[FactRevision]: 修订 ORM 实体列表（升序）。

        Raises:
            AppError: code="not_found"，当事实不存在时。
        """
        await FactRepository.get_fact(session, fact_id, org_id)
        result = await session.execute(
            sa.select(FactRevision)
            .where(FactRevision.fact_id == fact_id)
            .order_by(FactRevision.revision.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_raw_observations(
        session: AsyncSession,
        revision_id: UUID,
    ) -> list[RawObservation]:
        """获取修订的原始观察值列表。

        Args:
            session: 异步会话。
            revision_id: 事实修订 ID。

        Returns:
            list[RawObservation]: 原始观察值 ORM 实体列表。
        """
        result = await session.execute(
            sa.select(RawObservation)
            .where(RawObservation.fact_revision_id == revision_id)
            .order_by(RawObservation.created_at.asc(), RawObservation.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_normalized_observations(
        session: AsyncSession,
        revision_id: UUID,
    ) -> list[NormalizedObservation]:
        """获取修订的标准化观察值列表。

        Args:
            session: 异步会话。
            revision_id: 事实修订 ID。

        Returns:
            list[NormalizedObservation]: 标准化观察值 ORM 实体列表。
        """
        result = await session.execute(
            sa.select(NormalizedObservation)
            .where(
                NormalizedObservation.fact_revision_id == revision_id
            )
            .order_by(
                NormalizedObservation.created_at.asc(),
                NormalizedObservation.id.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_artifacts(
        session: AsyncSession,
        revision_id: UUID,
    ) -> list[FactArtifact]:
        """获取修订的工件链接列表。

        Args:
            session: 异步会话。
            revision_id: 事实修订 ID。

        Returns:
            list[FactArtifact]: 工件链接 ORM 实体列表。
        """
        result = await session.execute(
            sa.select(FactArtifact)
            .where(FactArtifact.fact_revision_id == revision_id)
            .order_by(FactArtifact.created_at.asc(), FactArtifact.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def search_facts(
        session: AsyncSession,
        query: str,
        org_id: UUID,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """全文搜索事实（使用 PostgreSQL tsvector + GIN 索引）。

        基于 fact_revision.search_vector 列进行搜索，关联 fact 表
        过滤组织，并可按 fact_type / object_id / status 过滤。

        Args:
            session: 异步会话。
            query: 搜索查询字符串。
            org_id: 组织 ID。
            filters: 过滤条件字典（fact_type, object_id, status）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]: (结果列表, 下一页游标)。
            每项含 {fact_id, revision, revision_id, fact_type,
            subject_id, status}。
        """
        effective_size = min(max(page_size, 1), 100)
        fetch_limit = effective_size + 1
        filters = filters or {}

        # 构建 tsquery
        tsquery = sa.func.plainto_tsquery("simple", query)

        # 构建查询：fact_revision JOIN fact
        stmt = (
            sa.select(
                Fact.id.label("fact_id"),
                FactRevision.id.label("revision_id"),
                FactRevision.revision.label("revision"),
                FactRevision.fact_type.label("fact_type"),
                FactRevision.subject_id.label("subject_id"),
                Fact.status.label("status"),
                FactRevision.created_at.label("created_at"),
            )
            .select_from(FactRevision)
            .join(Fact, Fact.id == FactRevision.fact_id)
            .where(
                Fact.organization_id == org_id,
                FactRevision.search_vector.op("@@")(tsquery),
            )
            .order_by(
                FactRevision.created_at.desc(),
                FactRevision.id.desc(),
            )
            .limit(fetch_limit)
        )

        if "fact_type" in filters:
            stmt = stmt.where(FactRevision.fact_type == filters["fact_type"])
        if "object_id" in filters:
            stmt = stmt.where(FactRevision.object_id == filters["object_id"])
        if "status" in filters:
            stmt = stmt.where(Fact.status == filters["status"])

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                sa.or_(
                    FactRevision.created_at < cursor_created_at,
                    sa.and_(
                        FactRevision.created_at == cursor_created_at,
                        FactRevision.id < cursor_id,
                    ),
                )
            )

        result = await session.execute(stmt)
        rows = result.all()

        items: list[dict] = [
            {
                "fact_id": row.fact_id,
                "revision": row.revision,
                "revision_id": row.revision_id,
                "fact_type": row.fact_type,
                "subject_id": row.subject_id,
                "status": row.status,
            }
            for row in rows[:effective_size]
        ]

        next_cursor: str | None = None
        if len(rows) > effective_size and items:
            last = rows[:effective_size][-1]
            next_cursor = _encode_cursor(last.created_at, last.revision_id)

        return items, next_cursor

    @staticmethod
    async def list_facts(
        session: AsyncSession,
        org_id: UUID,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页列出事实（按 fact_type, object_id, status 过滤）。

        返回每个事实的最新修订信息。

        Args:
            session: 异步会话。
            org_id: 组织 ID。
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]: (结果列表, 下一页游标)。
        """
        effective_size = min(max(page_size, 1), 100)
        fetch_limit = effective_size + 1
        filters = filters or {}

        # 查询 fact 表，关联最新 revision
        stmt = (
            sa.select(
                Fact.id.label("fact_id"),
                Fact.current_revision.label("current_revision"),
                Fact.fact_type.label("fact_type"),
                Fact.status.label("status"),
                Fact.object_id.label("object_id"),
                Fact.created_at.label("created_at"),
                Fact.id.label("fact_uuid"),
            )
            .where(Fact.organization_id == org_id)
            .order_by(Fact.created_at.asc(), Fact.id.asc())
            .limit(fetch_limit)
        )

        if "fact_type" in filters:
            stmt = stmt.where(Fact.fact_type == filters["fact_type"])
        if "object_id" in filters:
            stmt = stmt.where(Fact.object_id == filters["object_id"])
        if "status" in filters:
            stmt = stmt.where(Fact.status == filters["status"])

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                sa.or_(
                    Fact.created_at > cursor_created_at,
                    sa.and_(
                        Fact.created_at == cursor_created_at,
                        Fact.id > cursor_id,
                    ),
                )
            )

        result = await session.execute(stmt)
        rows = result.all()

        items: list[dict] = []
        for row in rows[:effective_size]:
            # 查询最新修订信息
            rev_result = await session.execute(
                sa.select(
                    FactRevision.id.label("revision_id"),
                    FactRevision.revision.label("revision"),
                    FactRevision.subject_id.label("subject_id"),
                )
                .where(FactRevision.fact_id == row.fact_id)
                .order_by(FactRevision.revision.desc())
                .limit(1)
            )
            rev_row = rev_result.first()
            items.append(
                {
                    "fact_id": row.fact_id,
                    "revision": rev_row.revision if rev_row else 0,
                    "revision_id": rev_row.revision_id if rev_row else None,
                    "fact_type": row.fact_type,
                    "subject_id": rev_row.subject_id if rev_row else "",
                    "status": row.status,
                }
            )

        next_cursor: str | None = None
        if len(rows) > effective_size and items:
            last = rows[:effective_size][-1]
            next_cursor = _encode_cursor(last.created_at, last.fact_uuid)

        return items, next_cursor

    @staticmethod
    async def find_by_idempotency_key(
        session: AsyncSession,
        org_id: UUID,
        key: str,
    ) -> Fact | None:
        """按幂等键查找已有事实。

        Args:
            session: 异步会话。
            org_id: 组织 ID。
            key: 幂等键。

        Returns:
            Fact | None: 已有事实，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(Fact).where(
                Fact.organization_id == org_id,
                Fact.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_revision_link(
        session: AsyncSession,
        from_revision_id: UUID,
    ) -> FactRevisionLink | None:
        """获取修订链链接（从指定修订出发）。

        Args:
            session: 异步会话。
            from_revision_id: 源修订 ID。

        Returns:
            FactRevisionLink | None: 链接实体，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(FactRevisionLink).where(
                FactRevisionLink.from_revision_id == from_revision_id
            )
        )
        return result.scalar_one_or_none()
