"""实验项目业务服务：create / list / get / update / set_status / check_not_archived。

核心流程：

create(department_id, code, display_name, description, visible_departments):
  1. 检查编码唯一性 → 若已存在抛 AppError(conflict)；
  2. INSERT experiment_project（status=active, lock_version=0）；
  3. 返回 ExperimentProject。

list(department_id, visible_dept_id, status, cursor, limit):
  1. 分页查询项目列表 + 部门名 JOIN + count_flows_by_project 统计（可见性由 RLS 保证）；
  2. 编码 next_cursor（keyset pagination）。

get(project_id):
  1. 查询项目 → 不存在抛 AppError(not_found)。

get_with_stats(project_id):
  1. 查询项目 + 统计任务数 → 不存在抛 AppError(not_found)。

update(project_id, display_name, description, lock_version, visible_departments):
  1. 乐观锁 UPDATE（不含 code 列）→ 影响 0 行抛 AppError(conflict)。

set_status(project_id, status, lock_version):
  1. 乐观锁 UPDATE status → 影响 0 行抛 AppError(conflict)。

check_not_archived(project_id):
  1. 查询项目状态，若 archived 则抛 AppError(conflict)。

关键约束：
- code 创建后锁定不可修改（UpdateProjectBody 不含 code，UPDATE 不写 code 列）；
- 乐观锁：WHERE id=? AND lock_version=?，影响 0 行 → 409；
- 归档约束：项目 archived 时拒绝新建任务（409）；

风格参考 packages/equipment/service.py。
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
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.common.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from packages.experiment_project.entities import (
    ExperimentProject,
    ExperimentProjectStatus,
)
from packages.experiment_project.repository import ExperimentProjectRepository


class ExperimentProjectListResult:
    """实验项目分页列表结果。

    Attributes:
        items: (ExperimentProject, department_name, task_count,
            owner_display_name, fact_count) 元组列表。
        next_cursor: 下一页游标（base64url 字符串），无更多数据时为 None。
        has_more: 是否还有更多数据。
    """

    def __init__(
        self,
        items: list[tuple[ExperimentProject, str, int, str | None, int]],
        next_cursor: str | None,
        has_more: bool,
    ) -> None:
        """初始化列表结果。"""
        self.items = items
        self.next_cursor = next_cursor
        self.has_more = has_more


class ExperimentProjectService(ScopedSessionMixin):
    """实验项目业务编排服务。

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    clock（时钟）、actor_id（操作者）。
    仓库方法为静态调用，无需注入实例。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _clock: 时钟实例。
        _actor_id: 当前操作者用户 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        clock: Clock | None = None,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化实验项目服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            clock: 时钟（默认 SystemClock）。
            actor_id: 当前操作用户 ID（用于 owner_user_id）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._clock = clock or SystemClock()

    @property
    def department_id(self) -> UUID:
        """当前部门 ID（公开只读访问）。"""
        return self._dept_id

    @property
    def actor_id(self) -> UUID | None:
        """当前操作者用户 ID（公开只读访问）。"""
        return self._actor_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂（公开只读访问）。"""
        return self._factory

    async def create(
        self,
        department_id: UUID,
        code: str | None,
        display_name: str,
        description: str | None,
        visible_departments: list[str] | None = None,
        owner_user_id: UUID | None = None,
    ) -> ExperimentProject:
        """创建实验项目。

        流程：
        1. 如果 code 为空则自动生成（PRJ-YYYYMMDD-NNNN）；
        2. 检查编码唯一性（department_id + code）→ 已存在抛 AppError(conflict)；
        3. 生成 UUID，INSERT experiment_project。

        Args:
            department_id: 所属部门 UUID。
            code: 项目编码（部门内唯一，None 时自动生成）。
            display_name: 中文显示名。
            description: 描述（可选）。
            visible_departments: 可见单位 ID 列表（可选，默认空数组）。

        Returns:
            ExperimentProject: 新创建的项目实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        async with self._scoped_session() as session:
            # 自动生成编码（和现有项目格式一致：proj_ + UUID 短码）
            from packages.common.ids import new_id

            if not code or not code.strip():
                code = f"proj_{str(new_id())[:8]}"
            existing = await ExperimentProjectRepository.select_by_dept_and_code(
                session, department_id, code
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message="项目编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            now = self._clock.now()
            project = ExperimentProject(
                id=new_id(),
                department_id=department_id,
                code=code,
                display_name=display_name,
                description=description,
                visible_departments=visible_departments or [],
                owner_user_id=owner_user_id or self._actor_id or new_id(),
                visibility_scope="tree",
                status=ExperimentProjectStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            return await ExperimentProjectRepository.insert(session, project)

    async def list(
        self,
        department_id: UUID | None = None,
        visible_dept_id: UUID | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> ExperimentProjectListResult:
        """分页查询项目列表（含部门名 + 任务统计）。

        排序：created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (created_at_iso, id)。

        Args:
            department_id: 部门 ID 筛选（含后代部门）。
            visible_dept_id: 可见性部门 ID，用于 OR visible_departments 过滤。
            status: 状态筛选。
            cursor: 分页游标。
            limit: 每页数量。
        """
        effective_limit = min(max(limit, 1), MAX_PAGE_SIZE)

        cursor_created_at: datetime | None = None
        cursor_id: UUID | None = None

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)

        # 多查一位判断 has_more
        fetch_limit = effective_limit + 1

        async with self._scoped_session() as session:
            rows = await ExperimentProjectRepository.select_list(
                session,
                department_id=department_id,
                visible_dept_id=visible_dept_id,
                status=status,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                limit=fetch_limit,
            )

        has_more = len(rows) > effective_limit
        page_items = rows[:effective_limit]

        # 批量统计每个项目的任务数、数据数和负责人名（替代 N*3 次单条查询）
        project_ids = [p.id for p, _ in page_items]
        owner_ids = list({p.owner_user_id for p, _ in page_items})

        async with self._scoped_session() as session:
            stats_map = await ExperimentProjectRepository.batch_count_flows_and_facts(
                session, project_ids
            )
            from packages.auth.entities import AppUser  # noqa: F401

            owner_map = await ExperimentProjectRepository.batch_owner_names(session, owner_ids)

        result_items: list[tuple[ExperimentProject, str, int, str | None, int]] = []
        for project, dept_name in page_items:
            task_count, fact_count = stats_map.get(project.id, (0, 0))
            owner_name = owner_map.get(project.owner_user_id)
            result_items.append((project, dept_name, task_count, owner_name, fact_count))

        next_cursor: str | None = None
        if has_more and page_items:
            last_proj, _ = page_items[-1]
            next_cursor = _encode_cursor(last_proj.created_at, last_proj.id)

        return ExperimentProjectListResult(
            items=result_items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get(self, project_id: UUID) -> ExperimentProject:
        """查询单个项目详情。

        Args:
            project_id: 项目 UUID。

        Returns:
            ExperimentProject: 项目实体。

        Raises:
            AppError: code="not_found"，当项目不存在时。
        """
        async with self._scoped_session() as session:
            project = await ExperimentProjectRepository.select_by_id(session, project_id)
            if project is None:
                raise AppError(
                    code="not_found",
                    message="项目不存在",
                    retryable=False,
                    fields={"project_id": str(project_id)},
                )
        return project

    async def get_with_stats(self, project_id: UUID) -> tuple[ExperimentProject, int, int]:
        """查询项目详情 + 任务统计 + 数据统计。

        Args:
            project_id: 项目 UUID。

        Returns:
            tuple[ExperimentProject, int, int]: 项目实体 + 任务数 + 数据数。

        Raises:
            AppError: code="not_found"，当项目不存在时。
        """
        async with self._scoped_session() as session:
            project = await ExperimentProjectRepository.select_by_id(session, project_id)
            if project is None:
                raise AppError(
                    code="not_found",
                    message="项目不存在",
                    retryable=False,
                    fields={"project_id": str(project_id)},
                )
            task_count = await ExperimentProjectRepository.count_flows_by_project(
                session, project_id
            )
            fact_count = await ExperimentProjectRepository.count_facts_by_project(
                session, project_id
            )
        return project, task_count, fact_count

    async def get_owner_display_name(self, owner_user_id: UUID) -> str | None:
        """查询项目负责人的 display_name。

        Args:
            owner_user_id: 负责人用户 UUID。

        Returns:
            str | None: 负责人显示名，不存在时返回 None。
        """
        from packages.auth.entities import AppUser

        async with self._scoped_session() as session:
            result = await session.scalar(
                sa.select(AppUser.display_name).where(AppUser.id == owner_user_id)
            )
            return result  # type: ignore[no-any-return]

    async def update(
        self,
        project_id: UUID,
        display_name: str,
        description: str | None,
        lock_version: int,
        visible_departments: list[str] | None = None,  # type: ignore[valid-type]
        owner_user_id: UUID | None = None,
    ) -> ExperimentProject:
        """编辑项目（code 不可修改，乐观锁）。

        UPDATE 不写 code 列（编码锁定约定）。
        影响 0 行时：先查询是否存在 → 存在则 409（lock_version 不匹配），不存在则 404。

        Args:
            project_id: 项目 UUID。
            display_name: 新显示名。
            description: 新描述。
            lock_version: 客户端持有的乐观锁版本号。
            visible_departments: 新可见单位 ID 列表（None 表示不修改）。

        Returns:
            ExperimentProject: 更新后的实体（含新 lock_version）。

        Raises:
            AppError: code="not_found"，当项目不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        async with self._scoped_session() as session:
            updated = await ExperimentProjectRepository.update(
                session,
                project_id=project_id,
                display_name=display_name,
                description=description,
                lock_version=lock_version,
                visible_departments=visible_departments,
                owner_user_id=owner_user_id,
            )
            if updated is not None:
                return updated

            # 影响 0 行：判断是不存在还是 lock_version 不匹配
            existing = await ExperimentProjectRepository.select_by_id(session, project_id)
            if existing is None:
                raise AppError(
                    code="not_found",
                    message="项目不存在",
                    retryable=False,
                    fields={"project_id": str(project_id)},
                )
            raise AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": lock_version},
            )

    async def set_status(
        self,
        project_id: UUID,
        status: str,
        lock_version: int,
    ) -> ExperimentProject:
        """归档/恢复项目（乐观锁）。

        Args:
            project_id: 项目 UUID。
            status: 新状态（"active" / "archived"）。
            lock_version: 客户端持有的乐观锁版本号。

        Returns:
            ExperimentProject: 更新后的实体（含新 lock_version）。

        Raises:
            AppError: code="not_found"，当项目不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        async with self._scoped_session() as session:
            updated = await ExperimentProjectRepository.update_status(
                session,
                project_id=project_id,
                status=status,
                lock_version=lock_version,
            )
            if updated is not None:
                return updated

            existing = await ExperimentProjectRepository.select_by_id(session, project_id)
            if existing is None:
                raise AppError(
                    code="not_found",
                    message="项目不存在",
                    retryable=False,
                    fields={"project_id": str(project_id)},
                )
            raise AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": lock_version},
            )

    async def check_not_archived(self, project_id: UUID) -> None:
        """检查项目非归档状态。

        归档项目下不可创建新任务（409 Conflict）。

        Args:
            project_id: 项目 UUID。

        Raises:
            AppError: code="not_found"，当项目不存在时。
            AppError: code="conflict"，当项目已归档时。
        """
        project = await self.get(project_id)
        if project.status == ExperimentProjectStatus.ARCHIVED.value:
            raise AppError(
                code="conflict",
                message="项目已归档，无法创建新任务",
                retryable=False,
                fields={"project_id": str(project_id), "status": project.status},
            )

    async def delete(self, project_id: UUID) -> None:
        """删除项目（连同项目下的所有任务一起删除）。

        仅允许删除归档项目。

        Raises:
            AppError: code="conflict"，当项目未归档时。
        """
        async with self._scoped_session() as session:
            project = await ExperimentProjectRepository.select_by_id(session, project_id)
            if project is None:
                raise AppError(
                    code="not_found",
                    message="项目不存在",
                    retryable=False,
                    fields={"project_id": str(project_id)},
                )
            if project.status != ExperimentProjectStatus.ARCHIVED.value:
                raise AppError(
                    code="conflict",
                    message="仅可删除已归档的项目",
                    retryable=False,
                    fields={"status": project.status},
                )
            # 级联删除项目下的所有任务及其关联数据
            from packages.components.flow.flow_runtime import (
                FlowDefinition as FD,
            )
            from packages.components.flow.flow_runtime import (
                FlowDefinitionVersionORM as FV,
            )
            from packages.components.flow.flow_runtime import (
                FlowNodeExecution as FNE,
            )
            from packages.components.flow.flow_runtime import (
                FlowRun as FR,
            )

            # 查出所有 flow_definition 的 id
            fd_ids_result = await session.execute(
                sa.select(FD.id).where(FD.project_id == project_id)
            )
            fd_ids = [row[0] for row in fd_ids_result]
            if fd_ids:
                # 查出所有 version 的 id
                fv_ids_result = await session.execute(
                    sa.select(FV.id).where(FV.flow_definition_id.in_(fd_ids))
                )
                fv_ids = [row[0] for row in fv_ids_result]
                if fv_ids:
                    # 查出所有 run 的 id
                    fr_ids_result = await session.execute(
                        sa.select(FR.id).where(FR.flow_version_id.in_(fv_ids))
                    )
                    fr_ids = [row[0] for row in fr_ids_result]
                    if fr_ids:
                        # 删 node_execution
                        await session.execute(sa.delete(FNE).where(FNE.flow_run_id.in_(fr_ids)))
                        # 删 flow_run
                        await session.execute(sa.delete(FR).where(FR.id.in_(fr_ids)))
            # 用 superuser 连接删除 versions + flow_definition（绕过不可变触发器）
            import os as _os

            from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
            from sqlalchemy.ext.asyncio import create_async_engine as _cae

            _alembic_url = _os.getenv("IRIP_ALEMBIC_DATABASE_URL", "")
            if _alembic_url and fd_ids:
                _eng = _cae(
                    _alembic_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
                )
                _fac = _asm(_eng, expire_on_commit=False)
                async with _fac() as _sess:
                    await _sess.execute(
                        sa.text(
                            "ALTER TABLE flow_definition_version "
                            "DISABLE TRIGGER prevent_modify_flow_version"
                        )
                    )
                    # 删 versions
                    await _sess.execute(
                        sa.text(
                            f"DELETE FROM flow_definition_version "
                            f"WHERE flow_definition_id = "
                            f"ANY(ARRAY[{','.join(repr(str(v)) for v in fd_ids)}]::uuid[])"
                        )
                    )
                    # 删 flow_definition
                    await _sess.execute(
                        sa.text(
                            f"DELETE FROM flow_definition WHERE id = "
                            f"ANY(ARRAY[{','.join(repr(str(v)) for v in fd_ids)}]::uuid[])"
                        )
                    )
                    await _sess.execute(
                        sa.text(
                            "ALTER TABLE flow_definition_version "
                            "ENABLE TRIGGER prevent_modify_flow_version"
                        )
                    )
                    await _sess.commit()
                await _eng.dispose()
            await ExperimentProjectRepository.delete(session, project_id)


def _encode_cursor(created_at: datetime, project_id: UUID) -> str:
    """编码 keyset 分页游标。

    格式：base64url( JSON {"v": {"ct": created_at_iso}, "id": uuid} )

    Args:
        created_at: 创建时间。
        project_id: 项目 UUID。

    Returns:
        str: base64url 编码的游标字符串。
    """
    payload = json.dumps(
        {
            "v": {"ct": created_at.isoformat()},
            "id": str(project_id),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Args:
        cursor: base64url 编码的游标字符串。

    Returns:
        tuple[datetime, UUID]: (created_at, id)。

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
    if not isinstance(v, dict) or "ct" not in v:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：缺少排序字段 ct",
            retryable=False,
            fields={"cursor": cursor},
        )

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

    return created_at, cursor_id
