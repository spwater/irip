"""实验室业务服务：create / list / get / update / set_status。

核心流程（docs/arch-department.md §4.1 / §4.2 时序图）：

create(code, display_name, description, sort_order):
  1. 检查编码唯一性 → 若已存在抛 AppError(conflict)；
  2. INSERT department（status=active, lock_version=0）；
  3. 返回 Department。

list(status, cursor, limit):
  1. 分页查询实验室列表 + member_count 聚合；
  2. 编码 next_cursor（keyset pagination）。

get(department_id):
  1. 查询实验室 → 不存在抛 AppError(not_found)。

update(department_id, display_name, description, sort_order, lock_version):
  1. 乐观锁 UPDATE（不含 code 列）→ 影响 0 行抛 AppError(conflict)。

set_status(department_id, status, lock_version):
  1. 乐观锁 UPDATE status → 影响 0 行抛 AppError(conflict)。

关键约束：
- code 创建后锁定不可修改（UpdateDepartmentRequest 不含 code，UPDATE 不写 code 列）；
- 乐观锁：WHERE id=? AND lock_version=?，影响 0 行 → 409；
- 软禁用：status='disabled'，无 DELETE；
- updated_at 显式写（服务层 UPDATE 语句 SET updated_at=now()，不加 DB 触发器）。
"""

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
from packages.departments.entities import Department, DepartmentStatus
from packages.departments.repository import DepartmentRepository


class DepartmentListResult:
    """实验室分页列表结果。

    Attributes:
        items: (Department, member_count, children_count, equipment_count) 元组列表。
        next_cursor: 下一页游标（base64url 字符串），无更多数据时为 None。
        has_more: 是否还有更多数据。
    """

    def __init__(
        self,
        items: list[tuple[Department, int, int, int]],
        next_cursor: str | None,
        has_more: bool,
    ) -> None:
        """初始化列表结果。"""
        self.items = items
        self.next_cursor = next_cursor
        self.has_more = has_more


class DepartmentService:
    """实验室业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、clock（时钟）。

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
        """初始化实验室服务。

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
        code: str,
        display_name: str,
        description: str | None,
        sort_order: int,
        parent_id: UUID | None = None,
    ) -> Department:
        """创建实验室。

        流程：
        1. 检查编码唯一性（organization_id + code）→ 已存在抛 AppError(conflict)；
        2. 生成 UUID，INSERT department。

        Args:
            code: 实验室编码（组织内唯一，创建后锁定）。
            display_name: 中文显示名。
            description: 描述（可选）。
            sort_order: 排序权重。
            parent_id: 上级部门 ID（None 表示顶级部门）。

        Returns:
            Department: 新创建的实验室实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        async with session_scope(self._factory) as session:
            existing = await DepartmentRepository.select_by_org_and_code(
                session, self._org_id, code
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message="实验室编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            now = self._clock.now()
            dept = Department(
                id=new_id(),
                organization_id=self._org_id,
                code=code,
                display_name=display_name,
                description=description,
                status=DepartmentStatus.ACTIVE.value,
                sort_order=sort_order,
                created_at=now,
                updated_at=now,
                lock_version=0,
                parent_id=parent_id,
            )
            return await DepartmentRepository.insert(session, dept)

    async def list(
        self,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> DepartmentListResult:
        """分页查询实验室列表（含成员数）。

        排序：sort_order ASC, created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (sort_order, created_at_iso, id)。

        Args:
            status: 状态筛选（None = 不过滤，"active" / "disabled"）。
            cursor: 上一页返回的 next_cursor（base64url 字符串）。
            limit: 每页数量（默认 20，最大 100）。

        Returns:
            DepartmentListResult: 分页列表结果。

        Raises:
            AppError: code="invalid_cursor"，当游标格式不合法时。
        """
        effective_limit = min(max(limit, 1), MAX_PAGE_SIZE)

        cursor_sort_order: int | None = None
        cursor_created_at: datetime | None = None
        cursor_id: UUID | None = None

        if cursor is not None:
            cursor_sort_order, cursor_created_at, cursor_id = _decode_cursor(cursor)

        # 多查一条判断 has_more
        fetch_limit = effective_limit + 1

        async with self._factory() as session:
            rows = await DepartmentRepository.select_list(
                session,
                organization_id=self._org_id,
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
            last_dept, _, _, _ = page_items[-1]
            next_cursor = _encode_cursor(last_dept.sort_order, last_dept.created_at, last_dept.id)

        return DepartmentListResult(
            items=page_items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get(self, department_id: UUID) -> Department:
        """查询单个实验室详情。

        Args:
            department_id: 实验室 UUID。

        Returns:
            Department: 实验室实体。

        Raises:
            AppError: code="not_found"，当实验室不存在时。
        """
        async with self._factory() as session:
            dept = await DepartmentRepository.select_by_id(session, department_id, self._org_id)
        if dept is None:
            raise AppError(
                code="not_found",
                message="实验室不存在",
                retryable=False,
                fields={"department_id": str(department_id)},
            )
        return dept

    async def update(
        self,
        department_id: UUID,
        display_name: str,
        description: str | None,
        sort_order: int,
        lock_version: int,
        parent_id: UUID | None = None,
    ) -> Department:
        """编辑实验室（code 不可修改，乐观锁）。

        UPDATE 不写 code 列（编码锁定约定）。
        影响 0 行时：先查询是否存在 → 存在则 409（lock_version 不匹配），不存在则 404。

        Args:
            department_id: 实验室 UUID。
            display_name: 新显示名。
            description: 新描述。
            sort_order: 新排序权重。
            lock_version: 客户端持有的乐观锁版本号。
            parent_id: 上级部门 ID（None 表示顶级部门）。

        Returns:
            Department: 更新后的实体（含新 lock_version）。

        Raises:
            AppError: code="not_found"，当实验室不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        async with session_scope(self._factory) as session:
            updated = await DepartmentRepository.update(
                session,
                department_id=department_id,
                display_name=display_name,
                description=description,
                sort_order=sort_order,
                lock_version=lock_version,
                organization_id=self._org_id,
                parent_id=parent_id,
            )
            if updated is not None:
                return updated

            # 影响 0 行：判断是不存在还是 lock_version 不匹配
            existing = await DepartmentRepository.select_by_id(session, department_id, self._org_id)
            if existing is None:
                raise AppError(
                    code="not_found",
                    message="实验室不存在",
                    retryable=False,
                    fields={"department_id": str(department_id)},
                )
            raise AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": lock_version},
            )

    async def set_status(
        self,
        department_id: UUID,
        status: str,
        lock_version: int,
    ) -> Department:
        """启用/禁用实验室（软禁用，乐观锁）。

        Args:
            department_id: 实验室 UUID。
            status: 新状态（"active" / "disabled"）。
            lock_version: 客户端持有的乐观锁版本号。

        Returns:
            Department: 更新后的实体（含新 lock_version）。

        Raises:
            AppError: code="not_found"，当实验室不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        async with session_scope(self._factory) as session:
            updated = await DepartmentRepository.update_status(
                session,
                department_id=department_id,
                status=status,
                lock_version=lock_version,
                organization_id=self._org_id,
            )
            if updated is not None:
                return updated

            existing = await DepartmentRepository.select_by_id(session, department_id, self._org_id)
            if existing is None:
                raise AppError(
                    code="not_found",
                    message="实验室不存在",
                    retryable=False,
                    fields={"department_id": str(department_id)},
                )
            raise AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": lock_version},
            )

    async def delete(self, department_id: UUID) -> None:
        """删除实验室（物理删除）。

        前置条件：
        - 子部门数为 0（无直接子部门）；
        - 仪器数为 0（无关联设备）。

        Raises:
            AppError: code="not_found"，当实验室不存在时。
            AppError: code="conflict"，当存在子部门或仪器时不允许删除。
        """
        from packages.equipment.entities import Equipment

        async with session_scope(self._factory) as session:
            # 检查是否存在
            existing = await DepartmentRepository.select_by_id(session, department_id, self._org_id)
            if existing is None:
                raise AppError(
                    code="not_found",
                    message="实验室不存在",
                    retryable=False,
                    fields={"department_id": str(department_id)},
                )

            # 检查子部门数
            children_count = await DepartmentRepository.select_children_count(
                session, department_id
            )
            if children_count > 0:
                raise AppError(
                    code="conflict",
                    message=f"存在 {children_count} 个子部门，请先删除子部门",
                    retryable=False,
                    fields={"children_count": children_count},
                )

            # 检查仪器数
            equip_result = await session.execute(
                sa.select(sa.func.count())
                .select_from(Equipment)
                .where(Equipment.department_id == department_id)
            )
            equipment_count = int(equip_result.scalar() or 0)
            if equipment_count > 0:
                raise AppError(
                    code="conflict",
                    message=f"存在 {equipment_count} 台仪器，请先迁移或删除仪器",
                    retryable=False,
                    fields={"equipment_count": equipment_count},
                )

            # 执行删除
            deleted = await DepartmentRepository.delete_by_id(session, department_id, self._org_id)
            if not deleted:
                raise AppError(
                    code="not_found",
                    message="实验室不存在",
                    retryable=False,
                    fields={"department_id": str(department_id)},
                )


def _encode_cursor(sort_order: int, created_at: datetime, dept_id: UUID) -> str:
    """编码 keyset 分页游标。

    格式：base64url( JSON {"v": {"so": sort_order, "ct": created_at_iso}, "id": uuid} )

    Args:
        sort_order: 排序权重。
        created_at: 创建时间。
        dept_id: 实验室 UUID。

    Returns:
        str: base64url 编码的游标字符串。
    """
    payload = json.dumps(
        {
            "v": {
                "so": sort_order,
                "ct": created_at.isoformat(),
            },
            "id": str(dept_id),
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
