"""治理服务：用户管理、角色分配、数据移交、root 数据统计。

从 ``apps/api/routers/governance.py`` 提取的 ORM 查询与业务逻辑。
职责：
- 用户列表（含分页、状态筛选、lab_director 可见部门过滤）；
- 创建用户（邮箱唯一性检查、部门解析、INSERT user + user_department）；
- 更新用户（字段更新、部门关联更新、角色更新）；
- 分配/移除角色（合并/移除 + lock_version 乐观锁）；
- 切换用户状态（active / disabled）；
- 删除用户（删 refresh_session + 物理删除）；
- 数据移交（批量 UPDATE department_id，含 dry_run）；
- root 部门数据量统计。

依赖注入：
- 继承 ScopedSessionMixin，通过 ``_scoped_session()`` 获取带 GUC 的会话；
- 需要实例属性 ``_factory``, ``_dept_id``, ``_actor_id``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.redaction import redact
from packages.audit.repository import AuditRecorder
from packages.auth.entities import AppUser
from packages.auth.passwords import hash_password
from packages.common.database import ScopedSessionMixin, scoped_session
from packages.common.errors import AppError
from packages.departments.entities import AppUserDepartment, Department

#: 允许移交的表白名单（均含 department_id 列）。
_TRANSFERABLE_TABLES: dict[str, str] = {
    "fact": "实验事实",
    "parameter": "参数",
    "model": "模型",
    "flow_definition": "流程定义",
    "flow_run": "流程运行",
    "equipment": "设备仪器",
}

#: 需统计 root 归属的表列表（表名 → 中文显示名）。
_ROOT_STATS_TABLES: dict[str, str] = {
    "fact": "实验事实",
    "parameter": "参数",
    "model": "模型",
    "flow_definition": "流程定义",
    "flow_run": "流程运行",
    "equipment": "设备仪器",
}


class GovernanceService(ScopedSessionMixin):
    """治理服务（用户管理 + 数据移交 + root 统计）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID（RLS 部门隔离锚点）。
        _actor_id: 当前操作者用户 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化治理服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID（用于 RLS GUC）。
            actor_id: 当前操作者用户 ID（用于 RLS GUC + 审计）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id

    # ------------------------------------------------------------------
    # 审计辅助
    # ------------------------------------------------------------------

    @staticmethod
    async def _record_audit(
        session: AsyncSession,
        actor_department_id: UUID | None,
        actor_user_id: UUID,
        action: str,
        resource_type: str | None,
        resource_id: UUID | None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """在当前事务中记录审计事件（脱敏后 INSERT）。

        Args:
            session: 数据库异步会话（由调用方管理事务）。
            actor_department_id: 操作者部门 ID。
            actor_user_id: 操作者用户 ID。
            action: 审计动作字符串。
            resource_type: 资源类型。
            resource_id: 资源 ID。
            payload: 事件载荷（将被脱敏）。
        """
        redacted = redact(payload) if payload is not None else None
        dept_id_for_audit: UUID = (
            actor_department_id if actor_department_id is not None else actor_user_id
        )
        event = AuditEventData(
            department_id=dept_id_for_audit,
            action=action,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=redacted,
        )
        await AuditRecorder.record(session, event)

    # ------------------------------------------------------------------
    # 用户列表
    # ------------------------------------------------------------------

    async def list_users(
        self,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
        visible_dept_ids: list[UUID] | None = None,
        filter_platform_users: bool = False,
    ) -> tuple[list[AppUser], bool, str | None]:
        """列出用户（分页）。

        Args:
            status: 状态筛选（active / disabled），None 表示不过滤。
            cursor: 分页游标（上一页最后一条记录的 created_at ISO 字符串）。
            limit: 每页数量。
            visible_dept_ids: 可见部门 ID 列表（lab_director 场景）。
                None 或空列表表示不做部门过滤。
            filter_platform_users: 是否过滤掉平台管理员/监督员用户
                （lab_director 场景）。

        Returns:
            tuple[list[AppUser], bool, str | None]:
                (page_items, has_more, next_cursor)

        Raises:
            AppError: code="invalid_cursor"，当游标格式错误时。
        """
        is_lab_director_only: bool = visible_dept_ids is not None
        is_not_lab_director: bool = visible_dept_ids is None

        async with self._factory() as session:
            stmt = sa.select(AppUser).order_by(AppUser.created_at.desc())

            if is_lab_director_only and visible_dept_ids:
                stmt = stmt.where(AppUser.department_id.in_(visible_dept_ids))

            if status is not None:
                stmt = stmt.where(AppUser.status == status)

            if cursor is not None:
                try:
                    cursor_dt = datetime.fromisoformat(cursor)
                except ValueError as exc:
                    raise AppError(
                        code="invalid_cursor",
                        message="无效的分页游标",
                        retryable=False,
                        fields={"cursor": cursor},
                    ) from exc
                stmt = stmt.where(AppUser.created_at < cursor_dt)

            # lab_director 需要额外过滤掉平台级角色用户，所以多取一些行再过滤
            fetch_limit = limit + 1 if is_not_lab_director else limit * 10 + 1
            stmt = stmt.limit(fetch_limit)
            result = await session.execute(stmt)
            rows: list[AppUser] = list(result.scalars().all())

        # lab_director 不应看到平台管理员/监督员用户
        if filter_platform_users:
            rows = [
                u
                for u in rows
                if not any(
                    r in ("platform_administrator", "platform_auditor")
                    for r in (u.roles if u.roles else [])
                )
            ]

        has_more: bool = len(rows) > limit
        page_items: list[AppUser] = rows[:limit]
        next_cursor: str | None = None
        if has_more and page_items:
            next_cursor = page_items[-1].created_at.isoformat()

        return page_items, has_more, next_cursor

    # ------------------------------------------------------------------
    # 创建用户
    # ------------------------------------------------------------------

    async def create_user(
        self,
        email: str,
        display_name: str,
        password: str,
        roles: list[str],
        department_uuid: UUID | None,
        admin_dept_id: UUID,
    ) -> AppUser:
        """创建新用户（含邮箱唯一性检查 + 部门解析 + 关联表写入）。

        Args:
            email: 用户邮箱。
            display_name: 显示名。
            password: 明文密码（将被 hash）。
            roles: 角色代码列表。
            department_uuid: 指定的实验室 UUID（可选）。
            admin_dept_id: 当前管理员的部门 ID（未指定实验室时使用）。

        Returns:
            AppUser: 新创建的用户实体。

        Raises:
            AppError: code="conflict"，当邮箱已存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            # 检查邮箱唯一性
            existing = await session.execute(sa.select(AppUser).where(AppUser.email == email))
            if existing.scalar_one_or_none() is not None:
                raise AppError(
                    code="conflict",
                    message=f"邮箱已存在: {email}",
                    retryable=False,
                    fields={"email": email},
                )

            # 确定 department_id：优先从所选实验室获取，未选实验室则用管理员部门
            dept_id: UUID = admin_dept_id
            if department_uuid is not None:
                dept = await session.execute(
                    sa.text("SELECT id FROM department WHERE id = :dept_id"),
                    {"dept_id": str(department_uuid)},
                )
                dept_row = dept.fetchone()
                if dept_row is not None and dept_row[0] is not None:
                    dept_id = UUID(str(dept_row[0]))

            # 创建用户
            user = AppUser(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                status="active",
                roles=list(roles),
                department_id=dept_id,
            )
            session.add(user)
            await session.flush()

            # 同步写入 app_user_department 关联表（is_primary=True）
            if department_uuid is not None:
                session.add(
                    AppUserDepartment(
                        user_id=user.id,
                        department_id=department_uuid,
                        is_primary=True,
                    )
                )
                await session.flush()

            # 记录审计
            await self._record_audit(
                session,
                actor_department_id=self._dept_id,
                actor_user_id=self._actor_id or user.id,
                action="user.create",
                resource_type="user",
                resource_id=user.id,
                payload={"email": email, "display_name": display_name, "roles": roles},
            )

            return user

    # ------------------------------------------------------------------
    # 更新用户
    # ------------------------------------------------------------------

    async def update_user(
        self,
        user_id: UUID,
        display_name: str | None = None,
        password: str | None = None,
        roles: list[str] | None = None,
        department_id: str | None = None,
    ) -> AppUser:
        """更新用户信息（邮箱不可修改）。

        Args:
            user_id: 目标用户 UUID。
            display_name: 新显示名（None 不修改）。
            password: 新密码明文（None 不修改）。
            roles: 新角色列表（None 不修改）。
            department_id: 新部门 ID 字符串（None 不修改）。

        Returns:
            AppUser: 更新后的用户实体。

        Raises:
            AppError: code="not_found"，当用户不存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            user = await session.get(AppUser, user_id)
            if user is None:
                raise AppError(
                    code="not_found",
                    message="用户不存在",
                    retryable=False,
                    fields={"user_id": str(user_id)},
                )

            if display_name is not None:
                user.display_name = display_name
            if password is not None:
                user.password_hash = hash_password(password)
            if roles is not None:
                user.roles = list(roles)
            if department_id is not None:
                user.department_id = UUID(department_id)

            await session.flush()

            # 记录审计
            await self._record_audit(
                session,
                actor_department_id=self._dept_id,
                actor_user_id=self._actor_id or user_id,
                action="user.update",
                resource_type="user",
                resource_id=user.id,
                payload={
                    "display_name": display_name,
                    "roles": roles,
                    "department_id": department_id,
                    "password_changed": password is not None,
                },
            )

            return user

    # ------------------------------------------------------------------
    # 分配角色
    # ------------------------------------------------------------------

    async def assign_roles(
        self,
        user_id: UUID,
        roles_to_add: list[str],
    ) -> AppUser:
        """分配角色给用户（合并到已有角色列表）。

        Args:
            user_id: 目标用户 UUID。
            roles_to_add: 要分配的角色代码列表。

        Returns:
            AppUser: 更新后的用户实体。

        Raises:
            AppError: code="not_found"，当用户不存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            user: AppUser | None = await session.scalar(
                sa.select(AppUser).where(AppUser.id == user_id)
            )
            if user is None:
                raise AppError(
                    code="not_found",
                    message=f"用户不存在: {user_id}",
                    retryable=False,
                    fields={"user_id": str(user_id)},
                )

            existing_roles: set[str] = set(user.roles) if user.roles else set()
            new_roles_set: set[str] = existing_roles | set(roles_to_add)
            merged_roles: list[str] = sorted(new_roles_set)

            await session.execute(
                sa.update(AppUser)
                .values(
                    roles=merged_roles,
                    updated_at=sa.func.now(),
                    lock_version=AppUser.lock_version + 1,
                )
                .where(AppUser.id == user_id)
            )

            await self._record_audit(
                session,
                actor_department_id=self._dept_id,
                actor_user_id=self._actor_id or user_id,
                action="governance.user.assign_roles",
                resource_type="app_user",
                resource_id=user_id,
                payload={"roles_added": roles_to_add, "roles_after": merged_roles},
            )

            await session.refresh(user)
            return user

    # ------------------------------------------------------------------
    # 移除角色
    # ------------------------------------------------------------------

    async def remove_role(
        self,
        user_id: UUID,
        role: str,
    ) -> AppUser:
        """移除用户的指定角色。

        Args:
            user_id: 目标用户 UUID。
            role: 要移除的角色代码。

        Returns:
            AppUser: 更新后的用户实体。

        Raises:
            AppError: code="not_found"，当用户不存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            user: AppUser | None = await session.scalar(
                sa.select(AppUser).where(AppUser.id == user_id)
            )
            if user is None:
                raise AppError(
                    code="not_found",
                    message=f"用户不存在: {user_id}",
                    retryable=False,
                    fields={"user_id": str(user_id)},
                )

            existing_roles: list[str] = list(user.roles) if user.roles else []
            updated_roles: list[str] = [r for r in existing_roles if r != role]

            await session.execute(
                sa.update(AppUser)
                .values(
                    roles=updated_roles,
                    updated_at=sa.func.now(),
                    lock_version=AppUser.lock_version + 1,
                )
                .where(AppUser.id == user_id)
            )

            await self._record_audit(
                session,
                actor_department_id=self._dept_id,
                actor_user_id=self._actor_id or user_id,
                action="governance.user.remove_role",
                resource_type="app_user",
                resource_id=user_id,
                payload={"role_removed": role, "roles_after": updated_roles},
            )

            await session.refresh(user)
            return user

    # ------------------------------------------------------------------
    # 切换用户状态
    # ------------------------------------------------------------------

    async def update_user_status(
        self,
        user_id: UUID,
        status: str,
    ) -> AppUser:
        """启用/禁用用户。

        Args:
            user_id: 目标用户 UUID。
            status: 目标状态（active / disabled）。

        Returns:
            AppUser: 更新后的用户实体。

        Raises:
            AppError: code="not_found"，当用户不存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            user: AppUser | None = await session.scalar(
                sa.select(AppUser).where(AppUser.id == user_id)
            )
            if user is None:
                raise AppError(
                    code="not_found",
                    message=f"用户不存在: {user_id}",
                    retryable=False,
                    fields={"user_id": str(user_id)},
                )

            await session.execute(
                sa.update(AppUser)
                .values(
                    status=status,
                    updated_at=sa.func.now(),
                    lock_version=AppUser.lock_version + 1,
                )
                .where(AppUser.id == user_id)
            )

            await self._record_audit(
                session,
                actor_department_id=self._dept_id,
                actor_user_id=self._actor_id or user_id,
                action="governance.user.update_status",
                resource_type="app_user",
                resource_id=user_id,
                payload={"status": status},
            )

            await session.refresh(user)
            return user

    # ------------------------------------------------------------------
    # 删除用户
    # ------------------------------------------------------------------

    async def delete_user(
        self,
        user_id: UUID,
        actor_email_for_audit: str | None = None,
        actor_display_name_for_audit: str | None = None,
    ) -> None:
        """删除用户（物理删除，含 refresh_session 清理）。

        Args:
            user_id: 目标用户 UUID。
            actor_email_for_audit: 被删除用户邮箱（审计日志用，从路由层传入）。
            actor_display_name_for_audit: 被删除用户显示名（审计日志用）。

        Raises:
            AppError: code="not_found"，当用户不存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            user = await session.get(AppUser, user_id)
            if user is None:
                raise AppError(
                    code="not_found",
                    message="用户不存在",
                    retryable=False,
                    fields={"user_id": str(user_id)},
                )

            # 记录审计（删除前记录）
            await self._record_audit(
                session,
                actor_department_id=self._dept_id,
                actor_user_id=self._actor_id or user_id,
                action="user.delete",
                resource_type="app_user",
                resource_id=user_id,
                payload={
                    "email": actor_email_for_audit or user.email,
                    "display_name": actor_display_name_for_audit or user.display_name,
                },
            )

            # 先删除关联的 refresh_session（避免外键约束报错）
            await session.execute(
                sa.text("DELETE FROM refresh_session WHERE user_id = :uid"),
                {"uid": str(user_id)},
            )

            await session.delete(user)

    # ------------------------------------------------------------------
    # 数据移交
    # ------------------------------------------------------------------

    async def transfer_data(
        self,
        table: str,
        from_dept_id: UUID,
        to_dept_id: UUID,
        dry_run: bool = False,
    ) -> int:
        """批量移交数据归属部门。

        将指定表中 department_id = from_dept_id 的所有行更新为 to_dept_id。
        dry_run=True 时只返回影响行数，不执行 UPDATE。

        Args:
            table: 目标表名（必须在白名单中）。
            from_dept_id: 源部门 UUID。
            to_dept_id: 目标部门 UUID。
            dry_run: True 时只返回影响行数，不执行。

        Returns:
            int: 影响行数。

        Raises:
            AppError: code="validation_failed"，当表名不在白名单时。
            AppError: code="validation_failed"，当源和目标部门相同时。
        """
        # 验证表名
        if table not in _TRANSFERABLE_TABLES:
            raise AppError(
                code="validation_failed",
                message=(
                    f"不支持的数据表: {table}（允许: {', '.join(_TRANSFERABLE_TABLES.keys())}）"
                ),
                retryable=False,
                fields={"table": table},
            )

        # 不允许源和目标相同
        if from_dept_id == to_dept_id:
            raise AppError(
                code="validation_failed",
                message="源部门和目标部门不能相同",
                retryable=False,
                fields={},
            )

        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            # 统计影响行数
            count_stmt = sa.text(
                f"SELECT COUNT(*) FROM {table} WHERE department_id = :from_dept_id"
            )
            count_result = await session.execute(count_stmt, {"from_dept_id": str(from_dept_id)})
            affected_rows: int = count_result.scalar() or 0

            if not dry_run and affected_rows > 0:
                # 执行 UPDATE
                update_stmt = sa.text(
                    f"UPDATE {table} SET department_id = :to_dept_id "
                    f"WHERE department_id = :from_dept_id"
                )
                await session.execute(
                    update_stmt,
                    {"to_dept_id": str(to_dept_id), "from_dept_id": str(from_dept_id)},
                )

                # 记录审计日志
                await self._record_audit(
                    session,
                    actor_department_id=self._dept_id,
                    actor_user_id=self._actor_id or from_dept_id,
                    action="governance.data_transfer",
                    resource_type=table,
                    resource_id=None,
                    payload={
                        "table": table,
                        "from_dept_id": str(from_dept_id),
                        "to_dept_id": str(to_dept_id),
                        "affected_rows": affected_rows,
                    },
                )

        return affected_rows

    # ------------------------------------------------------------------
    # root 部门数据量统计
    # ------------------------------------------------------------------

    async def get_root_data_stats(self) -> tuple[str, str, list[dict[str, Any]]]:
        """统计 root 部门归属的各表数据量。

        Returns:
            tuple[str, str, list[dict[str, Any]]]:
                (root_department_id, root_department_name, stats)
                stats 中每项含 table / display_name / count。

        Raises:
            AppError: code="not_found"，当 root 部门不存在时。
        """
        async with scoped_session(self._factory, self._dept_id, self._actor_id) as session:
            # 查找 root 部门
            dept_result = await session.execute(
                sa.select(Department).where(Department.code == "root")
            )
            root_dept = dept_result.scalar_one_or_none()
            if root_dept is None:
                raise AppError(
                    code="not_found",
                    message="root 部门不存在",
                    retryable=False,
                    fields={},
                )

            root_id = str(root_dept.id)
            root_name = root_dept.display_name

            # 获取 root 及其所有子孙部门 ID（管理员视角应统计全组织数据）
            dept_ids_result = await session.execute(
                sa.text(
                    "WITH RECURSIVE dept_tree AS ("
                    "  SELECT id FROM department WHERE id = :root_id"
                    "  UNION ALL"
                    "  SELECT d.id FROM department d"
                    "  JOIN dept_tree t ON d.parent_id = t.id"
                    ") SELECT id FROM dept_tree"
                ),
                {"root_id": root_id},
            )
            visible_dept_ids = [str(row[0]) for row in dept_ids_result]

            # 统计各表行数（root 及所有子孙部门）
            stats: list[dict[str, Any]] = []
            for table_name, display_name in _ROOT_STATS_TABLES.items():
                count_stmt = sa.text(
                    f"SELECT COUNT(*) FROM {table_name} WHERE department_id = ANY(:dept_ids)"
                )
                count_result = await session.execute(
                    count_stmt,
                    {"dept_ids": visible_dept_ids},
                )
                count = count_result.scalar() or 0
                stats.append(
                    {
                        "table": table_name,
                        "display_name": display_name,
                        "count": count,
                    }
                )

        return root_id, root_name, stats
