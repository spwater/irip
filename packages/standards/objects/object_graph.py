"""工业对象图业务服务：创建对象 / 查询 / 关系管理 / 环检测 / 层次遍历。

核心流程（IRIP Task 11）：

add_object(object_type, code, display_name, ...):
  1. 检查编码唯一性（组织内 + 类型内）→ 若已存在抛 AppError(conflict)；
  2. 若指定 parent_id，校验父对象存在且同组织；
  3. INSERT industrial_object（status=active）；
  4. 返回 IndustrialObject。

add_relation(source_id, target_id, relation_type):
  1. 拒绝自关联（source == target）→ AppError(code="self_relation")；
  2. 校验两对象存在且同组织；
  3. 幂等：若完全相同的活跃关系已存在，直接返回；
  4. 层次型关系环检测：若添加 source→target 会形成 target→...→source 的路径，
     抛 AppError(code="object_cycle")；
  5. 若存在不活跃的同 (source, target, type) 关系，重新激活；
  6. 否则 INSERT 新关系。

descendants(root_id):
  使用 PostgreSQL 递归 CTE 遍历 contains 关系，返回所有后代对象 ID 元组。

关键约束：
- 层次型关系（contains / upstream_of / downstream_of）必须无环；
- 非层次型关系允许双向（connected_to / measures / simulates / equivalent_to）。
"""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.pagination import MAX_PAGE_SIZE
from packages.standards.objects import (
    HIERARCHICAL_RELATIONS,
    IndustrialObject,
    ObjectRelation,
    RelationType,
)


class ObjectGraphService:
    """工业对象图业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化对象图服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选，预留审计扩展）。
        """
        self._factory = session_factory
        self._org_id = organization_id
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
            code: 对象编码（组织内 + 类型内唯一）。
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
            AppError: code="not_found"，当 parent_id 指定的父对象不存在或不属于当前组织时。
        """
        async with session_scope(self._factory) as session:
            # 检查编码唯一性
            existing = await session.execute(
                sa.select(IndustrialObject).where(
                    IndustrialObject.organization_id == self._org_id,
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
                if parent_obj is None or parent_obj.organization_id != self._org_id:
                    raise AppError(
                        code="not_found",
                        message="父对象不存在",
                        retryable=False,
                        fields={"parent_id": str(parent_id)},
                    )

            now = datetime.now(UTC)
            obj = IndustrialObject(
                id=new_id(),
                organization_id=self._org_id,
                object_type=object_type,
                code=code,
                display_name=display_name,
                description=description,
                parent_id=parent_id,
                equipment_id=equipment_id,
                department_id=department_id,
                visible_departments=visible_departments or [],
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
            AppError: code="not_found"，当对象不存在或不属于当前组织时。
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

            # 检查是否有活跃关系（作为 source 或 target）
            rel_count = await session.execute(
                sa.select(sa.func.count())
                .select_from(ObjectRelation)
                .where(
                    sa.or_(
                        ObjectRelation.source_id == object_id,
                        ObjectRelation.target_id == object_id,
                    ),
                    ObjectRelation.is_active == sa.true(),
                )
            )
            if int(rel_count.scalar() or 0) > 0:
                raise AppError(
                    code="conflict",
                    message="该对象存在活跃的关系，请先移除关系后再删除",
                    retryable=False,
                    fields={"object_id": str(object_id)},
                )

            # 检查是否有子对象（parent_id 指向自己）
            child_count = await session.execute(
                sa.select(sa.func.count())
                .select_from(IndustrialObject)
                .where(IndustrialObject.parent_id == object_id)
            )
            if int(child_count.scalar() or 0) > 0:
                raise AppError(
                    code="conflict",
                    message="该对象存在子对象，请先删除子对象",
                    retryable=False,
                    fields={"object_id": str(object_id)},
                )

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
                    IndustrialObject.organization_id == self._org_id,
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
            .where(IndustrialObject.organization_id == self._org_id)
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

    # ---- 关系管理 ----

    async def add_relation(
        self,
        source_id: UUID,
        target_id: UUID,
        relation_type: str,
    ) -> ObjectRelation:
        """添加对象间关系（含自关联校验 + 层次型环检测）。

        Args:
            source_id: 源对象 ID。
            target_id: 目标对象 ID。
            relation_type: 关系类型（contains / connected_to / ...）。

        Returns:
            ObjectRelation: 关系实体。

        Raises:
            AppError: code="self_relation"，当 source == target 时。
            AppError: code="not_found"，当对象不存在或不属于当前组织时。
            AppError: code="object_cycle"，当层次型关系会形成环时。
        """
        # 自关联校验
        if source_id == target_id:
            raise AppError(
                code="self_relation",
                message="不允许自关联关系",
                retryable=False,
                fields={"source_id": str(source_id)},
            )

        async with session_scope(self._factory) as session:
            # 校验两对象存在且同组织
            await self._get_and_check_org(session, source_id)
            await self._get_and_check_org(session, target_id)

            # 查询是否已存在相同关系（活跃或不活跃）
            result = await session.execute(
                sa.select(ObjectRelation).where(
                    ObjectRelation.source_id == source_id,
                    ObjectRelation.target_id == target_id,
                    ObjectRelation.relation_type == relation_type,
                )
            )
            existing = result.scalar_one_or_none()

            # 幂等：活跃关系已存在，直接返回
            if existing is not None and existing.is_active:
                return existing

            # 层次型关系环检测：仅在新创建或重新激活时
            if relation_type in HIERARCHICAL_RELATIONS:
                await self._check_cycle(session, source_id, target_id, relation_type)

            if existing is not None and not existing.is_active:
                # 重新激活
                await session.execute(
                    sa.update(ObjectRelation)
                    .values(is_active=True)
                    .where(ObjectRelation.id == existing.id)
                )
                await session.flush()
                result2 = await session.execute(
                    sa.select(ObjectRelation).where(ObjectRelation.id == existing.id)
                )
                reactivated = result2.scalar_one()
                return reactivated

            # 创建新关系
            now = datetime.now(UTC)
            relation = ObjectRelation(
                id=new_id(),
                organization_id=self._org_id,
                source_id=source_id,
                target_id=target_id,
                relation_type=relation_type,
                is_active=True,
                created_at=now,
                lock_version=0,
            )
            session.add(relation)
            await session.flush()
            return relation

    async def remove_relation(
        self,
        source_id: UUID,
        target_id: UUID,
        relation_type: str,
    ) -> None:
        """移除对象间关系（标记 is_active=false）。

        Args:
            source_id: 源对象 ID。
            target_id: 目标对象 ID。
            relation_type: 关系类型。

        Raises:
            AppError: code="not_found"，当关系不存在时。
        """
        async with session_scope(self._factory) as session:
            result = await session.execute(
                sa.select(ObjectRelation).where(
                    ObjectRelation.source_id == source_id,
                    ObjectRelation.target_id == target_id,
                    ObjectRelation.relation_type == relation_type,
                    ObjectRelation.is_active.is_(True),
                )
            )
            relation = result.scalar_one_or_none()
            if relation is None:
                raise AppError(
                    code="not_found",
                    message="关系不存在",
                    retryable=False,
                    fields={
                        "source_id": str(source_id),
                        "target_id": str(target_id),
                        "relation_type": relation_type,
                    },
                )
            await session.execute(
                sa.update(ObjectRelation)
                .values(is_active=False)
                .where(ObjectRelation.id == relation.id)
            )

    async def get_relations(
        self,
        object_id: UUID,
        relation_type: str | None = None,
    ) -> list[ObjectRelation]:
        """查询对象的所有活跃关系（作为源或目标）。

        Args:
            object_id: 对象 ID。
            relation_type: 可选关系类型过滤。

        Returns:
            list[ObjectRelation]: 活跃关系列表。
        """
        async with self._factory() as session:
            query = (
                sa.select(ObjectRelation)
                .where(
                    ObjectRelation.is_active.is_(True),
                    sa.or_(
                        ObjectRelation.source_id == object_id,
                        ObjectRelation.target_id == object_id,
                    ),
                )
                .order_by(ObjectRelation.created_at.asc())
            )
            if relation_type is not None:
                query = query.where(ObjectRelation.relation_type == relation_type)
            result = await session.execute(query)
            return list(result.scalars().all())

    # ---- 层次遍历 ----

    async def descendants(self, root_id: UUID) -> tuple[UUID, ...]:
        """递归 CTE 遍历 contains 关系，返回所有后代对象 ID。

        从 root_id 出发，沿活跃的 contains 关系递归向下遍历，
        返回所有后代对象的 ID 元组（不含 root_id 自身）。
        按 BFS 顺序（深度优先、同深度按 ID 排序）返回，确保确定性。

        Args:
            root_id: 根对象 ID。

        Returns:
            tuple[UUID, ...]: 后代对象 ID 元组，无后代时为空元组。

        Raises:
            AppError: code="not_found"，当根对象不存在或不属于当前组织时。
        """
        async with self._factory() as session:
            # 校验根对象
            await self._get_and_check_org(session, root_id)

            # 递归 CTE：BFS 遍历 contains 关系
            cte = sa.select(
                ObjectRelation.target_id.label("descendant_id"),
                sa.literal_column("1").label("depth"),
                ObjectRelation.target_id.label("sort_key"),
            ).where(
                ObjectRelation.source_id == root_id,
                ObjectRelation.relation_type == RelationType.CONTAINS.value,
                ObjectRelation.is_active.is_(True),
            )
            cte = cte.cte("descendants_cte", recursive=True)

            # 递归部分：沿 contains 关系继续向下
            child_alias = aliased(ObjectRelation)
            cte_recursive = cte.union_all(
                sa.select(
                    child_alias.target_id.label("descendant_id"),
                    (cte.c.depth + 1).label("depth"),
                    child_alias.target_id.label("sort_key"),
                ).where(
                    child_alias.source_id == cte.c.descendant_id,
                    child_alias.relation_type == RelationType.CONTAINS.value,
                    child_alias.is_active.is_(True),
                )
            )

            query = sa.select(cte_recursive.c.descendant_id).order_by(
                cte_recursive.c.depth.asc(), cte_recursive.c.sort_key.asc()
            )
            result = await session.execute(query)
            rows = result.fetchall()
            return tuple(UUID(str(row[0])) for row in rows)

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
            AppError: code="not_found"，当对象不存在或不属于当前组织时。
        """
        result = await session.execute(
            sa.select(IndustrialObject).where(IndustrialObject.id == object_id)
        )
        obj = result.scalar_one_or_none()
        if obj is None or obj.organization_id != self._org_id:
            raise AppError(
                code="not_found",
                message="工业对象不存在",
                retryable=False,
                fields={"object_id": str(object_id)},
            )
        return obj

    async def _check_cycle(
        self,
        session: AsyncSession,
        source_id: UUID,
        target_id: UUID,
        relation_type: str,
    ) -> None:
        """检测添加 source→target 是否会形成环。

        对于层次型关系（contains / upstream_of / downstream_of），
        若从 target 出发沿同类型活跃关系能到达 source，则形成环。

        Args:
            session: 异步会话。
            source_id: 新关系的源对象。
            target_id: 新关系的目标对象。
            relation_type: 关系类型。

        Raises:
            AppError: code="object_cycle"，当检测到环时。
        """
        # 递归 CTE：从 target 出发沿同类型活跃关系遍历
        cte = sa.select(
            ObjectRelation.target_id.label("node_id"),
            sa.literal_column("1").label("depth"),
        ).where(
            ObjectRelation.source_id == target_id,
            ObjectRelation.relation_type == relation_type,
            ObjectRelation.is_active.is_(True),
        )
        cte = cte.cte("cycle_check_cte", recursive=True)

        child_alias = aliased(ObjectRelation)
        cte_recursive = cte.union_all(
            sa.select(
                child_alias.target_id.label("node_id"),
                (cte.c.depth + 1).label("depth"),
            ).where(
                child_alias.source_id == cte.c.node_id,
                child_alias.relation_type == relation_type,
                child_alias.is_active.is_(True),
            )
        )

        # 检查从 target 可达的节点中是否包含 source
        query = sa.select(cte_recursive.c.node_id).where(cte_recursive.c.node_id == source_id)
        result = await session.execute(query)
        if result.fetchone() is not None:
            raise AppError(
                code="object_cycle",
                message=f"添加该 {relation_type} 关系将形成环",
                retryable=False,
                fields={
                    "source_id": str(source_id),
                    "target_id": str(target_id),
                    "relation_type": relation_type,
                },
            )


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
