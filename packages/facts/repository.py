"""L2 事实数据访问层。

FactRepository 封装所有事实相关的数据库操作：
- 事实的插入（含合并字段）；
- 事实查询（含组织归属校验）；
- 全文搜索（PostgreSQL tsvector + GIN 索引，直接查 fact 表）；
- 事实列表（直接查 fact 表）；
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

from packages.common.dept_visibility import compute_visible_dept_ids
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.entities import Fact


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
        department_id: UUID,
        fact_type: str,
        object_id: UUID,
        owner_user_id: UUID,
        visibility_scope: str = "tree",
        status: str = "active",
        idempotency_key: str | None = None,
        created_by: UUID | None = None,
        subject_id: str = "",
        flow_run_id: UUID | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        task_code: str | None = None,
        task_name: str | None = None,
        department_name: str | None = None,
        operator: str | None = None,
        run_operator: str | None = None,
        equipment_name: str | None = None,
        source_artifact_id: UUID | None = None,
    ) -> Fact:
        """插入事实行，返回 ORM 实体。

        Args:
            session: 异步会话。
            department_id: 部门 ID。
            fact_type: 事实类型。
            object_id: 工业对象 ID。
            owner_user_id: 所有者用户 ID（NOT NULL）。
            visibility_scope: 可见范围（默认 tree）。
            status: 状态（默认 active）。
            idempotency_key: 幂等键（可选）。
            created_by: 创建人 ID（可选）。
            subject_id: 主体标识。
            flow_run_id: 流程运行 ID（可选）。
            started_at: 开始时间。
            ended_at: 结束时间。
            task_code: 任务编码快照。
            task_name: 任务名称快照。
            department_name: 部门名称快照。
            operator: 操作人快照。
            run_operator: 运行操作人快照。
            equipment_name: 设备名快照。
            source_artifact_id: 源工件 ID（可选）。

        Returns:
            Fact: 事实 ORM 实体。
        """
        fact = Fact(
            id=new_id(),
            department_id=department_id,
            owner_user_id=owner_user_id,
            visibility_scope=visibility_scope,
            fact_type=fact_type,
            object_id=object_id,
            status=status,
            lock_version=0,
            idempotency_key=idempotency_key,
            created_by=created_by,
            subject_id=subject_id,
            flow_run_id=flow_run_id,
            started_at=started_at,
            ended_at=ended_at,
            task_code=task_code,
            task_name=task_name,
            department_name=department_name,
            operator=operator,
            run_operator=run_operator,
            equipment_name=equipment_name,
            source_artifact_id=source_artifact_id,
        )
        session.add(fact)
        await session.flush()
        return fact

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
            org_id: 部门 ID（用于校验归属）。

        Returns:
            Fact: 事实 ORM 实体。

        Raises:
            AppError: code="not_found"，当事实不存在或不属于该组织时。
        """
        result = await session.execute(sa.select(Fact).where(Fact.id == fact_id))
        fact = result.scalar_one_or_none()
        if fact is None or fact.department_id != org_id:
            raise AppError(
                code="not_found",
                message="事实不存在",
                retryable=False,
                fields={"fact_id": str(fact_id)},
            )
        return fact

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

        直接查 fact 表的 search_vector 列进行搜索。

        Args:
            session: 异步会话。
            query: 搜索查询字符串。
            org_id: 部门 ID。
            filters: 过滤条件字典（fact_type, object_id, status）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]: (结果列表, 下一页游标)。
            每项含 {fact_id, fact_type, subject_id, status}。
        """
        effective_size = min(max(page_size, 1), 100)
        fetch_limit = effective_size + 1
        filters = filters or {}

        # 构建 tsquery
        tsquery = sa.func.plainto_tsquery("simple", query)

        # 构建查询：直接查 fact 表
        stmt = (
            sa.select(
                Fact.id.label("fact_id"),
                Fact.fact_type.label("fact_type"),
                Fact.subject_id.label("subject_id"),
                Fact.status.label("status"),
                Fact.created_at.label("created_at"),
            )
            .where(
                Fact.department_id.in_(await compute_visible_dept_ids(session, org_id)),
                Fact.search_vector.op("@@")(tsquery),
            )
            .order_by(
                Fact.created_at.desc(),
                Fact.id.desc(),
            )
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
                    Fact.created_at < cursor_created_at,
                    sa.and_(
                        Fact.created_at == cursor_created_at,
                        Fact.id < cursor_id,
                    ),
                )
            )

        result = await session.execute(stmt)
        rows = result.all()

        items: list[dict] = [
            {
                "fact_id": row.fact_id,
                "fact_type": row.fact_type,
                "subject_id": row.subject_id,
                "status": row.status,
            }
            for row in rows[:effective_size]
        ]

        next_cursor: str | None = None
        if len(rows) > effective_size and items:
            last = rows[:effective_size][-1]
            next_cursor = _encode_cursor(last.created_at, last.fact_id)

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

        直接查 fact 表，返回每个事实的字段。

        Args:
            session: 异步会话。
            org_id: 部门 ID。
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]: (结果列表, 下一页游标)。
        """
        effective_size = min(max(page_size, 1), 100)
        fetch_limit = effective_size + 1
        filters = filters or {}

        stmt = (
            sa.select(
                Fact.id.label("fact_id"),
                Fact.fact_type.label("fact_type"),
                Fact.status.label("status"),
                Fact.object_id.label("object_id"),
                Fact.subject_id.label("subject_id"),
                Fact.created_at.label("created_at"),
                Fact.id.label("fact_uuid"),
            )
            .where(Fact.department_id.in_(await compute_visible_dept_ids(session, org_id)))
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

        items: list[dict] = [
            {
                "fact_id": row.fact_id,
                "fact_type": row.fact_type,
                "subject_id": row.subject_id,
                "status": row.status,
            }
            for row in rows[:effective_size]
        ]

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
            org_id: 部门 ID。
            key: 幂等键。

        Returns:
            Fact | None: 已有事实，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(Fact).where(
                Fact.department_id == org_id,
                Fact.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()
