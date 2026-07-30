"""设备仪器业务服务：create / list / get / update / set_status / delete。

核心流程：

create(department_id, code, display_name, description, sort_order):
  1. 检查编码唯一性 → 若已存在抛 AppError(conflict)；
  2. INSERT equipment（status=active, lock_version=0）；
  3. 返回 Equipment。

list(department_id, status, cursor, limit):
  1. 分页查询设备列表 + 部门名 JOIN；
  2. 编码 next_cursor（keyset pagination）。

get(equipment_id):
  1. 查询设备 → 不存在抛 AppError(not_found)。

update(equipment_id, display_name, description, department_id, sort_order, lock_version):
  1. 乐观锁 UPDATE（不含 code 列）→ 影响 0 行抛 AppError(conflict)。

set_status(equipment_id, status, lock_version):
  1. 乐观锁 UPDATE status → 影响 0 行抛 AppError(conflict)。

关键约束：
- code 创建后锁定不可修改（UpdateEquipmentBody 不含 code，UPDATE 不写 code 列）；
- 乐观锁：WHERE id=? AND lock_version=?，影响 0 行 → 409；
- 软禁用：status='disabled'，无 DELETE；
- 所有操作校验 organization_id。
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from packages.equipment.entities import Equipment, EquipmentStatus
from packages.equipment.repository import EquipmentRepository


class EquipmentListResult:
    """设备分页列表结果。

    Attributes:
        items: (Equipment, department_name) 元组列表。
        next_cursor: 下一页游标（base64url 字符串），无更多数据时为 None。
        has_more: 是否还有更多数据。
    """

    def __init__(
        self,
        items: list[tuple[Equipment, str]],
        next_cursor: str | None,
        has_more: bool,
    ) -> None:
        """初始化列表结果。"""
        self.items = items
        self.next_cursor = next_cursor
        self.has_more = has_more


class EquipmentService:
    """设备仪器业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、clock（时钟）。
    仓库方法为静态调用，无需注入实例。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _clock: 时钟实例。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        clock: Clock | None = None,
    ) -> None:
        """初始化设备仪器服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            clock: 时钟（默认 SystemClock）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._clock = clock or SystemClock()

    async def create(
        self,
        department_id: UUID,
        code: str,
        display_name: str,
        description: str | None,
        sort_order: int,
        visible_departments: list[str] | None = None,
    ) -> Equipment:
        """创建设备仪器。

        流程：
        1. 检查编码唯一性（organization_id + code）→ 已存在抛 AppError(conflict)；
        2. 生成 UUID，INSERT equipment。

        Args:
            department_id: 所属部门 UUID。
            code: 设备编码（组织内唯一，创建后锁定）。
            display_name: 中文显示名。
            description: 描述（可选）。
            sort_order: 排序权重。
            visible_departments: 可见单位 ID 列表（可选，默认空数组）。

        Returns:
            Equipment: 新创建的设备实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        async with session_scope(self._factory) as session:
            existing = await EquipmentRepository.select_by_org_and_code(session, self._org_id, code)
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message="设备编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            now = self._clock.now()
            equipment = Equipment(
                id=new_id(),
                organization_id=self._org_id,
                code=code,
                display_name=display_name,
                description=description,
                department_id=department_id,
                visible_departments=visible_departments or [],
                status=EquipmentStatus.ACTIVE.value,
                sort_order=sort_order,
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            return await EquipmentRepository.insert(session, equipment)

    async def list(
        self,
        department_id: UUID | None = None,
        visible_dept_id: UUID | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> EquipmentListResult:
        """分页查询设备列表（含部门名）。

        排序：sort_order ASC, created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (sort_order, created_at_iso, id)。

        Args:
            department_id: 部门 ID 筛选（含后代部门）。
            visible_dept_id: 可见性部门 ID，用于 OR visible_departments 过滤。
            status: 状态筛选。
            cursor: 分页游标。
            limit: 每页数量。
        """
        effective_limit = min(max(limit, 1), MAX_PAGE_SIZE)

        cursor_sort_order: int | None = None
        cursor_created_at: datetime | None = None
        cursor_id: UUID | None = None

        if cursor is not None:
            cursor_sort_order, cursor_created_at, cursor_id = _decode_cursor(cursor)

        # 多查一位判断 has_more
        fetch_limit = effective_limit + 1

        async with self._factory() as session:
            rows = await EquipmentRepository.select_list(
                session,
                organization_id=self._org_id,
                department_id=department_id,
                visible_dept_id=visible_dept_id,
                status=status,
                cursor_sort_order=cursor_sort_order,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                limit=fetch_limit,
            )

        has_more = len(rows) > effective_limit
        page_items = rows[:effective_limit]

        next_cursor: str | None = None
        if has_more and page_items:
            last_equip, _ = page_items[-1]
            next_cursor = _encode_cursor(
                last_equip.sort_order, last_equip.created_at, last_equip.id
            )

        return EquipmentListResult(
            items=page_items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get(self, equipment_id: UUID) -> Equipment:
        """查询单个设备详情。

        Args:
            equipment_id: 设备 UUID。

        Returns:
            Equipment: 设备实体。

        Raises:
            AppError: code="not_found"，当设备不存在时。
        """
        async with self._factory() as session:
            equipment = await EquipmentRepository.select_by_id(session, equipment_id, self._org_id)
            if equipment is None:
                raise AppError(
                    code="not_found",
                    message="设备不存在",
                    retryable=False,
                    fields={"equipment_id": str(equipment_id)},
                )
        return equipment

    async def update(
        self,
        equipment_id: UUID,
        display_name: str,
        description: str | None,
        department_id: UUID,
        sort_order: int,
        lock_version: int,
        visible_departments: list[str] | None = None,
    ) -> Equipment:
        """编辑设备（code 不可修改，乐观锁）。

        UPDATE 不写 code 列（编码锁定约定）。
        影响 0 行时：先查询是否存在 → 存在则 409（lock_version 不匹配），不存在则 404。

        Args:
            equipment_id: 设备 UUID。
            display_name: 新显示名。
            description: 新描述。
            department_id: 新部门 ID。
            sort_order: 新排序权重。
            lock_version: 客户端持有的乐观锁版本号。
            visible_departments: 新可见单位 ID 列表（None 表示不修改）。

        Returns:
            Equipment: 更新后的实体（含新 lock_version）。

        Raises:
            AppError: code="not_found"，当设备不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        async with session_scope(self._factory) as session:
            updated = await EquipmentRepository.update(
                session,
                equipment_id=equipment_id,
                display_name=display_name,
                description=description,
                department_id=department_id,
                sort_order=sort_order,
                lock_version=lock_version,
                organization_id=self._org_id,
                visible_departments=visible_departments,
            )
            if updated is not None:
                return updated

            # 影响 0 行：判断是不存在还是 lock_version 不匹配
            existing = await EquipmentRepository.select_by_id(session, equipment_id, self._org_id)
            if existing is None or existing.organization_id != self._org_id:
                raise AppError(
                    code="not_found",
                    message="设备不存在",
                    retryable=False,
                    fields={"equipment_id": str(equipment_id)},
                )
            raise AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": lock_version},
            )

    async def set_status(
        self,
        equipment_id: UUID,
        status: str,
        lock_version: int,
    ) -> Equipment:
        """启用/禁用设备（软禁用，乐观锁）。

        Args:
            equipment_id: 设备 UUID。
            status: 新状态（"active" / "disabled"）。
            lock_version: 客户端持有的乐观锁版本号。

        Returns:
            Equipment: 更新后的实体（含新 lock_version）。

        Raises:
            AppError: code="not_found"，当设备不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        async with session_scope(self._factory) as session:
            updated = await EquipmentRepository.update_status(
                session,
                equipment_id=equipment_id,
                status=status,
                lock_version=lock_version,
                organization_id=self._org_id,
            )
            if updated is not None:
                return updated

            existing = await EquipmentRepository.select_by_id(session, equipment_id, self._org_id)
            if existing is None or existing.organization_id != self._org_id:
                raise AppError(
                    code="not_found",
                    message="设备不存在",
                    retryable=False,
                    fields={"equipment_id": str(equipment_id)},
                )
            raise AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": lock_version},
            )

    async def delete(self, equipment_id: UUID) -> None:
        """删除设备（硬删除）。

        Args:
            equipment_id: 设备 UUID。

        Raises:
            AppError: code="not_found"，当设备不存在时。
        """
        async with session_scope(self._factory) as session:
            equipment = await EquipmentRepository.select_by_id(session, equipment_id, self._org_id)
            if equipment is None:
                raise AppError(
                    code="not_found",
                    message="设备不存在",
                    retryable=False,
                    fields={"equipment_id": str(equipment_id)},
                )
            await session.execute(
                sa.delete(Equipment).where(
                    Equipment.id == equipment_id,
                    Equipment.organization_id == self._org_id,
                )
            )


def _encode_cursor(sort_order: int, created_at: datetime, equip_id: UUID) -> str:
    """编码 keyset 分页游标。

    格式：base64url( JSON {"v": {"so": sort_order, "ct": created_at_iso}, "id": uuid} )

    Args:
        sort_order: 排序权重。
        created_at: 创建时间。
        equip_id: 设备 UUID。

    Returns:
        str: base64url 编码的游标字符串。
    """
    payload = json.dumps(
        {
            "v": {
                "so": sort_order,
                "ct": created_at.isoformat(),
            },
            "id": str(equip_id),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[int, datetime, UUID]:
    """解码 keyset 分页游标。

    Args:
        cursor: base64url 编码的游标字符串。

    Returns:
        tuple[int, datetime, UUID]: (sort_order, created_at, id)。

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

    v = payload["v"]
    if not isinstance(v, dict) or "so" not in v or "ct" not in v:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：缺少排序字段 so / ct",
            retryable=False,
            fields={"cursor": cursor},
        )

    try:
        sort_order = int(v["so"])
    except (ValueError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：so 字段不是整数",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        created_at = datetime.fromisoformat(str(v["ct"]))
    except (ValueError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：ct 字段不是合法 ISO 时间",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        cursor_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：id 字段不是合法 UUID",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    return sort_order, created_at, cursor_id
