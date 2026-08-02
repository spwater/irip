"""工业对象业务服务：创建对象 / 查询 / 更新 / 删除 / 列表。

核心流程（IRIP Task 11）：

add_object(object_type, code, display_name, ...):
  1. 检查编码唯一性（部门内 + 类型内）→ 若已存在抛 AppError(conflict)；
  2. INSERT industrial_object（status=active）；
  3. 返回 IndustrialObject。
"""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.pagination import MAX_PAGE_SIZE
from packages.standards.objects import (
    IndustrialObject,
    RelationType,
)


class ObjectGraphService:
    """工业对象图业务编排服务。

    依赖注入 session_factory（事务管理）、department_id（当前部门）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化对象图服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID（可选，预留审计扩展）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id

    # ---- 对象 CRUD ----

    async def add_object(
        self,
        object_type: str,
        code: str,
        display_name: str,
        description: str | None = None,
        parent_id: UUID | None = None,
        equipment_id: UUID | None = None,
        department_id: UUID | None = None,
        visible_departments: list[str] | None = None,
    ) -> IndustrialObject:
        """创建工业对象（status=active）。

        Args:
            object_type: 对象类型（lab / production_line / ...）。
            code: 对象编码（部门内 + 类型内唯一）。
            display_name: 中文显示名。
            description: 描述（可选）。
            parent_id: 父对象 ID（可选，便捷反规范化字段）。
            equipment_id: 关联设备 ID（可选）。
            department_id: 所属部门 ID（可选，跨实验室可见性基准）。
            visible_departments: 可见单位 ID 列表（可选，默认空数组）。

        Returns:
            IndustrialObject: 新创建的对象实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
            AppError: code="not_found"，当 parent_id 指定的父对象不存在或不属于当前部门时。
        """
        async with session_scope(self._factory) as session:
            # 检查编码唯一性
            existing = await session.execute(
                sa.select(IndustrialObject).where(
                    IndustrialObject.department_id == self._dept_id,
                    IndustrialObject.object_type == object_type,
                    IndustrialObject.code == code,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise AppError(
                    code="conflict",
                    message="工业对象编码已存在",
                    retryable=False,
                    fields={"code": code, "object_type": object_type},
                )

            # 校验父对象
            if parent_id is not None:
                parent = await session.execute(
                    sa.select(IndustrialObject).where(
                        IndustrialObject.id == parent_id,
                    )
                )
                parent_obj = parent.scalar_one_or_none()
                if parent_obj is None or parent_obj.department_id != self._dept_id:
                    raise AppError(
                        code="not_found",
                        message="父对象不存在",
                        retryable=False,
                        fields={"parent_id": str(parent_id)},
                    )

            now = datetime.now(UTC)
            obj = IndustrialObject(
                id=new_id(),
                department_id=self._dept_id,
                object_type=object_type,
                code=code,
                display_name=display_name,
                description=description,
                equipment_id=equipment_id,
                visible_departments=visible_departments or [],
                visibility_scope="tree",
                owner_user_id=self._actor_id,
                status="active",
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            session.add(obj)
            await session.flush()
            return obj

    async def get_object(self, object_id: UUID) -> IndustrialObject:
        """查询单个工业对象。

        Args:
            object_id: 对象 UUID。

        Returns:
            IndustrialObject: 对象实体。

        Raises:
            AppError: code="not_found"，当对象不存在或不属于当前部门时。
        """
        async with self._factory() as session:
            obj = await self._get_and_check_org(session, object_id)
            return obj

    async def update_object(
        self,
        object_id: UUID,
        display_name: str,
        description: str | None = None,
        object_type: str | None = None,
        equipment_id: UUID | None = None,
        department_id: UUID | None = None,
        visible_departments: list[str] | None = None,
    ) -> IndustrialObject:
        """编辑工业对象（code 不可修改）。

        Args:
            object_id: 对象 UUID。
            display_name: 新显示名。
            description: 新描述。
            equipment_id: 新关联设备 ID。
            department_id: 新所属部门 ID（None 表示不修改）。
            visible_departments: 新可见单位 ID 列表（None 表示不修改）。

        Returns:
            IndustrialObject: 更新后的对象实体。

        Raises:
            AppError: code="not_found"，当对象不存在时。
        """
        async with session_scope(self._factory) as session:
            obj = await self._get_and_check_org(session, object_id)
            obj.display_name = display_name
            obj.description = description
            if object_type is not None:
                obj.object_type = object_type
            obj.equipment_id = equipment_id
            if department_id is not None:
                obj.department_id = department_id
            if visible_departments is not None:
                obj.visible_departments = visible_departments
            obj.updated_at = datetime.now(UTC)
            obj.lock_version += 1
            await session.flush()
            return obj

    async def set_object_status(
        self,
        object_id: UUID,
        status: str,
    ) -> IndustrialObject:
        """启用/禁用工业对象。

        Args:
            object_id: 对象 UUID。
            status: 新状态（"active" / "inactive"）。

        Returns:
            IndustrialObject: 更新后的对象实体。

        Raises:
            AppError: code="not_found"，当对象不存在时。
        """
        async with session_scope(self._factory) as session:
            obj = await self._get_and_check_org(session, object_id)
            obj.status = status
            obj.updated_at = datetime.now(UTC)
            obj.lock_version += 1
            await session.flush()
            return obj

    async def delete_object(self, object_id: UUID) -> None:
        """物理删除工业对象。

        前置条件：对象没有活跃的关系和子对象。

        Args:
            object_id: 对象 UUID。

        Raises:
            AppError: code="not_found"，当对象不存在时。
            AppError: code="conflict"，当对象存在活跃关系时。
        """
        async with session_scope(self._factory) as session:
            obj = await self._get_and_check_org(session, object_id)

            # 物理删除
            await session.delete(obj)
            await session.flush()

    async def get_object_by_code(
        self,
        code: str,
        object_type: str,
    ) -> IndustrialObject | None:
        """按编码 + 类型查询工业对象。

        Args:
            code: 对象编码。
            object_type: 对象类型。

        Returns:
            IndustrialObject | None: 对象实体，不存在返回 None。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(IndustrialObject).where(
                    IndustrialObject.department_id == self._dept_id,
                    IndustrialObject.object_type == object_type,
                    IndustrialObject.code == code,
                )
            )
            return result.scalar_one_or_none()

    async def list_objects(
        self,
        object_type: str | list[str] | None = None,
        cursor: str | None = None,
        page_size: int = 20,
        department_id: UUID | None = None,
        visible_dept_id: UUID | None = None,
    ) -> tuple[list[IndustrialObject], str | None]:
        """分页查询工业对象列表。

        排序：created_at ASC, id ASC。Keyset 分页。

        Args:
            object_type: 可选类型过滤，str 单类型或 list[str] 多类型（IN 查询），None 表示全部。
            cursor: 分页游标（base64url 字符串），None 表示第一页。
            page_size: 每页数量（默认 20，最大 100）。
            department_id: 部门 ID 筛选（精确匹配 industrial_object.department_id）。
            visible_dept_id: 可见性部门 ID，用于 OR visible_departments @> [dept_id] 过滤。
                当 department_id 和 visible_dept_id 同时存在时，取两者的 OR。

        Returns:
            tuple[list[IndustrialObject], str | None]: (对象列表, 下一页游标)。

        Raises:
            AppError: code="invalid_cursor"，当游标格式不合法时。
        """
        effective_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        fetch_limit = effective_size + 1

        query = (
            sa.select(IndustrialObject)
            .where(IndustrialObject.department_id == self._dept_id)
            .order_by(IndustrialObject.created_at.asc(), IndustrialObject.id.asc())
            .limit(fetch_limit)
        )

        if object_type is not None:
            if isinstance(object_type, list):
                query = query.where(IndustrialObject.object_type.in_(object_type))
            else:
                query = query.where(IndustrialObject.object_type == object_type)

        # 部门过滤 + 可见性过滤
        # 可见性规则：department_id == dept_id OR visible_departments 包含 visible_dept_id
        if department_id is not None and visible_dept_id is not None:
            query = query.where(
                sa.or_(
                    IndustrialObject.department_id == department_id,
                    IndustrialObject.visible_departments.contains([str(visible_dept_id)]),
                )
            )
        elif department_id is not None:
            query = query.where(IndustrialObject.department_id == department_id)
        elif visible_dept_id is not None:
            query = query.where(
                IndustrialObject.visible_departments.contains([str(visible_dept_id)])
            )

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_list_cursor(cursor)
            query = query.where(
                sa.or_(
                    IndustrialObject.created_at > cursor_created_at,
                    sa.and_(
                        IndustrialObject.created_at == cursor_created_at,
                        IndustrialObject.id > cursor_id,
                    ),
                )
            )

        async with self._factory() as session:
            result = await session.execute(query)
            objects = list(result.scalars().all())

        has_more = len(objects) > effective_size
        page_items = objects[:effective_size]

        next_cursor: str | None = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = _encode_list_cursor(last.created_at, last.id)

        return page_items, next_cursor

    # ---- 内部辅助 ----

    async def _get_and_check_org(
        self,
        session: AsyncSession,
        object_id: UUID,
    ) -> IndustrialObject:
        """读取对象并校验组织归属。

        Args:
            session: 异步会话。
            object_id: 对象 UUID。

        Returns:
            IndustrialObject: 对象实体。

        Raises:
            AppError: code="not_found"，当对象不存在或不属于当前部门时。
        """
        result = await session.execute(
            sa.select(IndustrialObject).where(IndustrialObject.id == object_id)
        )
        obj = result.scalar_one_or_none()
        if obj is None or obj.department_id != self._dept_id:
            raise AppError(
                code="not_found",
                message="工业对象不存在",
                retryable=False,
                fields={"object_id": str(object_id)},
            )
        return obj


# ---- 游标编解码 ----


def _encode_list_cursor(created_at: datetime, object_id: UUID) -> str:
    """编码 keyset 分页游标。

    格式：base64url( JSON {"v": created_at_iso, "id": uuid_str} )
    """
    import base64
    import json

    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(object_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_list_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Returns:
        tuple[datetime, UUID]: (created_at, object_id)。

    Raises:
        AppError: code="invalid_cursor"，当游标格式不合法时。
    """
    import base64
    import binascii
    import json

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
        object_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：id 字段不是合法 UUID",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    return created_at, object_id
