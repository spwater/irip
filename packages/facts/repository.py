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

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.entities import Fact, FactDataIndex
from packages.facts.observations import FactMeta, FactSnapshotRow

if TYPE_CHECKING:
    from packages.common.artifacts import Artifact


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
        org_id: UUID,  # 保留参数兼容调用方，可见性由 RLS 处理
    ) -> Fact:
        """获取事实（可见性由 RLS 策略保证）。

        Args:
            session: 异步会话（已设 GUC，RLS 自动过滤不可见行）。
            fact_id: 事实 ID。
            org_id: 部门 ID（保留兼容，不再用于过滤）。

        Returns:
            Fact: 事实 ORM 实体。

        Raises:
            AppError: code="not_found"，当事实不存在或 RLS 不可见时。
        """
        result = await session.execute(sa.select(Fact).where(Fact.id == fact_id))
        fact = result.scalar_one_or_none()
        if fact is None:
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
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], str | None]:
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

        items: list[dict[str, Any]] = [
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
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], str | None]:
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

        stmt = sa.select(
            Fact.id.label("fact_id"),
            Fact.fact_type.label("fact_type"),
            Fact.status.label("status"),
            Fact.object_id.label("object_id"),
            Fact.subject_id.label("subject_id"),
            Fact.created_at.label("created_at"),
            Fact.id.label("fact_uuid"),
        )
        # 可见性由 RLS 策略保证（app.current_dept_id + app.current_user_id GUC）
        # 不再在此处硬过滤 department_id == org_id，否则平台管理员看不到子部门数据
        stmt = stmt.order_by(Fact.created_at.asc(), Fact.id.asc()).limit(fetch_limit)

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

        items: list[dict[str, Any]] = [
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

    # ---- 快照富化与统计 ----

    @staticmethod
    async def fetch_snapshots(
        session: AsyncSession,
        fact_ids: list[UUID],
        *,
        include_project: bool = False,
        include_base: bool = False,
        with_task_code_fallback: bool = False,
    ) -> dict[UUID, FactSnapshotRow]:
        """统一的快照 JOIN 查询。

        JOIN 链：Fact ⟕ FlowRun ⟕ FlowDefinitionVersion ⟕ FlowDefinition
        （可选 ⟕ ExperimentProject）。

        Args:
            session: 异步会话。
            fact_ids: 事实 ID 列表。
            include_project: 是否 JOIN ExperimentProject 取 project_name。
            include_base: 是否额外取 fact_type / subject_id / status。
            with_task_code_fallback: JOIN 条件用
                sa.or_(FV.flow_definition_id==FD.id, Fact.task_code==FD.code)
                （仅 list 版原行为）；否则单路径 FV.flow_definition_id==FD.id。

        Returns:
            dict[UUID, FactSnapshotRow]: fact_id → 快照行映射。
        """
        from packages.components.flow.flow_runtime import (
            FlowDefinition as _FD,
        )
        from packages.components.flow.flow_runtime import (
            FlowDefinitionVersionORM as _FV,
        )
        from packages.components.flow.flow_runtime import (
            FlowRun as _FR,
        )

        columns: list[sa.ColumnElement[Any]] = [
            Fact.id.label("fact_id"),
            (Fact.fact_type if include_base else sa.null()).label("fact_type"),
            (Fact.subject_id if include_base else sa.null()).label("subject_id"),
            (Fact.status if include_base else sa.null()).label("status"),
            Fact.task_code.label("task_code"),
            sa.func.coalesce(_FD.display_name, Fact.task_name).label("task_name"),
            sa.null().label("project_name"),
            Fact.department_name.label("department_name"),
            Fact.operator.label("operator"),
            Fact.run_operator.label("run_operator"),
            Fact.equipment_name.label("equipment_name"),
            Fact.created_at.label("created_at"),
        ]

        if include_project:
            from packages.experiment_project.entities import (
                ExperimentProject as _EP,
            )

            # Replace project_name column with actual JOIN
            columns[6] = _EP.display_name.label("project_name")

        stmt = sa.select(*columns)

        # JOIN FlowRun
        stmt = stmt.outerjoin(_FR, Fact.flow_run_id == _FR.id)
        # JOIN FlowDefinitionVersion
        stmt = stmt.outerjoin(_FV, _FR.flow_version_id == _FV.id)
        # JOIN FlowDefinition
        if with_task_code_fallback:
            stmt = stmt.outerjoin(
                _FD,
                sa.or_(
                    _FV.flow_definition_id == _FD.id,
                    Fact.task_code == _FD.code,
                ),
            )
        else:
            stmt = stmt.outerjoin(_FD, _FV.flow_definition_id == _FD.id)
        # JOIN ExperimentProject (optional)
        if include_project:
            stmt = stmt.outerjoin(_EP, _FD.project_id == _EP.id)

        stmt = stmt.where(Fact.id.in_(fact_ids))

        result = await session.execute(stmt)
        snap_map: dict[UUID, FactSnapshotRow] = {}
        for row in result:
            snap_map[row.fact_id] = FactSnapshotRow(
                fact_id=row.fact_id,
                fact_type=row.fact_type,
                subject_id=row.subject_id,
                status=row.status,
                task_code=row.task_code,
                task_name=row.task_name,
                project_name=row.project_name,
                department_name=row.department_name,
                operator=row.operator,
                run_operator=row.run_operator,
                equipment_name=row.equipment_name,
                created_at=row.created_at,
            )
        return snap_map

    @staticmethod
    async def count_group_by_task(
        session: AsyncSession,
        fact_ids: list[UUID] | None = None,
    ) -> dict[str, int]:
        """按 task_code 分组计数。

        Args:
            session: 异步会话。
            fact_ids: None → 全局 group count（list/search 用）；
                非 None → 按 IN(...) 过滤（search-data 用）。

        Returns:
            dict[str, int]: task_code → 计数。
        """
        stmt = (
            sa.select(Fact.task_code, sa.func.count(sa.func.distinct(Fact.id)))
            .where(Fact.task_code.isnot(None))
            .group_by(Fact.task_code)
        )
        if fact_ids is not None:
            stmt = stmt.where(Fact.id.in_(fact_ids))
        result = await session.execute(stmt)
        return {str(row[0]): row[1] for row in result}

    @staticmethod
    async def search_data_index(
        session: AsyncSession,
        *,
        q: str | None = None,
        key: str | None = None,
        value: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        page_size: int = 20,
    ) -> list[UUID] | None:
        """FactDataIndex 去重 fact_id 查询。

        Args:
            session: 异步会话。
            q: 全文搜索（匹配 key 或 value_text）。
            key: 精确匹配 key。
            value: 精确匹配 value_text。
            min_value: 数值下限。
            max_value: 数值上限。
            page_size: 每页数量。

        Returns:
            list[UUID] | None: 去重后的 fact_id 列表；无匹配条件时返回 None。
        """
        conditions: list[sa.ColumnElement[bool]] = []
        if q is not None:
            like_q = f"%{q}%"
            conditions.append(
                sa.or_(
                    FactDataIndex.key.ilike(like_q),
                    FactDataIndex.value_text.ilike(like_q),
                )
            )
        if key is not None:
            conditions.append(FactDataIndex.key == key)
        if value is not None:
            conditions.append(FactDataIndex.value_text == value)
        if min_value is not None:
            conditions.append(FactDataIndex.value_number >= min_value)
        if max_value is not None:
            conditions.append(FactDataIndex.value_number <= max_value)

        if not conditions:
            return None

        stmt = (
            sa.select(FactDataIndex.fact_id).where(sa.and_(*conditions)).distinct().limit(page_size)
        )
        result = await session.execute(stmt)
        return [row[0] for row in result]

    # ---- 写操作支持 ----

    @staticmethod
    async def find_fact_in_dept(
        session: AsyncSession,
        fact_id: UUID,
        dept_id: UUID,
    ) -> Fact | None:
        """在部门范围内查找事实（archive 用）。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            dept_id: 部门 ID。

        Returns:
            Fact | None: 找到的事实，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(Fact).where(
                Fact.department_id == dept_id,
                Fact.id == fact_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_fact_status(
        session: AsyncSession,
        fact_id: UUID,
        status: str,
    ) -> None:
        """更新事实状态（archive 用）。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。
            status: 新状态。
        """
        await session.execute(sa.update(Fact).where(Fact.id == fact_id).values(status=status))

    @staticmethod
    async def get_fact_meta(
        session: AsyncSession,
        fact_id: UUID,
    ) -> FactMeta | None:
        """取事实元数据（delete 前置查询）。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。

        Returns:
            FactMeta | None: 元数据，不存在时返回 None。
        """
        result = await session.execute(
            sa.select(
                Fact.id,
                Fact.source_artifact_id,
                Fact.department_id,
                Fact.owner_user_id,
                Fact.flow_run_id,
            ).where(Fact.id == fact_id)
        )
        row = result.first()
        if row is None:
            return None
        return FactMeta(
            fact_id=row[0],
            source_artifact_id=row[1],
            department_id=row[2],
            owner_user_id=row[3],
            flow_run_id=row[4],
        )

    @staticmethod
    async def get_facts_meta_by_task(
        session: AsyncSession,
        task_code: str,
    ) -> list[FactMeta]:
        """按任务编码批量取事实元数据（delete 前置查询）。

        Args:
            session: 异步会话。
            task_code: 任务编码。

        Returns:
            list[FactMeta]: 元数据列表（department_id / owner_user_id 为 None，
                因为批量删除不做归属校验）。
        """
        result = await session.execute(
            sa.select(
                Fact.id,
                Fact.source_artifact_id,
                Fact.flow_run_id,
            ).where(Fact.task_code == task_code)
        )
        rows = result.all()
        return [
            FactMeta(
                fact_id=row[0],
                source_artifact_id=row[1],
                department_id=None,
                owner_user_id=None,
                flow_run_id=row[2],
            )
            for row in rows
        ]

    @staticmethod
    async def find_json_artifact(
        session: AsyncSession,
        fact_id: UUID,
    ) -> Artifact | None:
        """查找 JSON Artifact（source_artifact_id 优先，fallback extract_{flow_run_id}.json）。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。

        Returns:
            Artifact | None: JSON artifact 实体，不存在时返回 None。
        """
        from packages.common.artifacts import Artifact

        # 优先通过 source_artifact_id 查找 JSON artifact
        result = await session.execute(
            sa.select(Artifact)
            .where(
                Artifact.id
                == sa.select(Fact.source_artifact_id).where(Fact.id == fact_id).scalar_subquery(),
                Artifact.media_type == "application/json",
            )
            .limit(1)
        )
        art_record = result.scalar_one_or_none()

        # Fallback: source_artifact_id 指向非 JSON（原始文件），
        # 通过 flow_run_id 查找 JSON 结果 artifact
        if art_record is None:
            flow_run_row = (
                await session.execute(sa.select(Fact.flow_run_id).where(Fact.id == fact_id))
            ).scalar_one_or_none()
            if flow_run_row is not None:
                result = await session.execute(
                    sa.select(Artifact)
                    .where(
                        Artifact.media_type == "application/json",
                        Artifact.filename == f"extract_{flow_run_row}.json",
                    )
                    .order_by(Artifact.created_at.desc())
                    .limit(1)
                )
                art_record = result.scalar_one_or_none()

        return art_record

    @staticmethod
    async def find_source_file_artifact(
        session: AsyncSession,
        fact_id: UUID,
    ) -> Artifact | None:
        """查找非 JSON 原始文件 artifact（PDF 等）。

        Args:
            session: 异步会话。
            fact_id: 事实 ID。

        Returns:
            Artifact | None: 非 JSON artifact 实体，不存在时返回 None。
        """
        from packages.common.artifacts import Artifact

        result = await session.execute(
            sa.select(Artifact)
            .where(
                Artifact.id
                == sa.select(Fact.source_artifact_id).where(Fact.id == fact_id).scalar_subquery(),
                Artifact.media_type != "application/json",
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def delete_facts(
        session: AsyncSession,
        fact_ids: list[UUID],
    ) -> None:
        """删除事实（FK CASCADE 自动删 FactDataIndex）。

        Args:
            session: 异步会话。
            fact_ids: 事实 ID 列表。
        """
        await session.execute(sa.delete(Fact).where(Fact.id.in_(fact_ids)))

    @staticmethod
    async def delete_flow_runs(
        session: AsyncSession,
        flow_run_ids: list[UUID],
    ) -> None:
        """删除流程运行记录。

        Args:
            session: 异步会话。
            flow_run_ids: 流程运行 ID 列表。
        """
        from packages.components.flow.flow_runtime import FlowRun

        await session.execute(sa.delete(FlowRun).where(FlowRun.id.in_(flow_run_ids)))
