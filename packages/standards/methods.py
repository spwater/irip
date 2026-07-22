"""方法 ORM 模型与业务服务（IRIP Task 12）。

定义两张表：
- method: 方法主表，code 组织内唯一，含状态机字段；
- method_version: 不可变版本表，每次提交审核创建一行，发布后锁定不可修改。

状态流转与变量一致（draft→in_review→published→deprecated,
in_review→rejected, rejected→draft），复用 state_machine.assert_transition。

MethodService 提供方法 CRUD + 生命周期管理（创建 / 提交 / 发布 / 拒绝 / 弃用 / 重提交）。
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.pagination import MAX_PAGE_SIZE
from packages.standards.state_machine import StandardStatus, assert_transition


class MethodStatus(StrEnum):
    """方法状态枚举（与 state_machine.StandardStatus 值对齐）。

    Attributes:
        DRAFT: 草稿状态，可编辑。
        IN_REVIEW: 审核中，已提交审核。
        PUBLISHED: 已发布，不可修改（仅可弃用）。
        REJECTED: 已拒绝，可重新提交。
        DEPRECATED: 已弃用，历史数据保留，新引用被阻止。
    """

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class Method(Base):
    """方法实体（对应 method 表）。

    code 在组织内唯一（UNIQUE 约束 (organization_id, code)）。
    version_count 记录已创建的版本数（= 最大版本号）。

    Attributes:
        id: 方法 UUID。
        organization_id: 所属组织 ID。
        code: 方法编码（组织内唯一，创建后锁定）。
        display_name: 中文显示名。
        description: 描述（可选）。
        status: 状态（draft / in_review / published / rejected / deprecated）。
        version_count: 已创建版本数（默认 0）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "method"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'")
    )
    version_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_method_org_code"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"Method(id={self.id!r}, code={self.code!r}, "
            f"status={self.status!r}, version_count={self.version_count!r})"
        )


class MethodVersion(Base):
    """方法不可变版本实体（对应 method_version 表）。

    每次提交审核时从当前 method 快照创建一行。发布后（status=published），
    核心属性不可修改；仅 status 可从 published 转为 deprecated。

    Attributes:
        id: 版本 UUID。
        method_id: 所属方法 ID（FK→method.id）。
        version: 版本号（从 1 开始递增）。
        code: 方法编码快照。
        display_name: 显示名快照。
        description: 描述快照（可选）。
        status: 版本状态（in_review / published / rejected / deprecated）。
        published_at: 发布时间（发布后设置）。
        published_by: 发布人 UUID（发布后设置）。
        deprecated_at: 弃用时间（弃用后设置）。
        deprecated_by: 弃用人 UUID（弃用后设置）。
        rejection_reason: 拒绝原因（拒绝后设置）。
        created_at: 版本创建时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "method_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    method_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("method.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    published_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    deprecated_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )

    def __repr__(self) -> str:
        return (
            f"MethodVersion(id={self.id!r}, method_id={self.method_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


class MethodService:
    """方法业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID（用于 published_by / deprecated_by）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化方法服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选，用于发布/弃用时记录操作人）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_method(
        self,
        code: str,
        display_name: str,
        description: str | None = None,
    ) -> Method:
        """创建方法（DRAFT 状态, version_count=0）。

        Args:
            code: 方法编码（组织内唯一）。
            display_name: 中文显示名。
            description: 描述（可选）。

        Returns:
            Method: 新创建的方法实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        async with session_scope(self._factory) as session:
            existing = await session.execute(
                sa.select(Method).where(
                    Method.organization_id == self._org_id,
                    Method.code == code,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise AppError(
                    code="conflict",
                    message="方法编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            now = datetime.now(UTC)
            method = Method(
                id=new_id(),
                organization_id=self._org_id,
                code=code,
                display_name=display_name,
                description=description,
                status="draft",
                version_count=0,
                created_at=now,
                updated_at=now,
                lock_version=0,
            )
            session.add(method)
            await session.flush()
            return method

    async def submit_method(self, method_id: UUID) -> MethodVersion:
        """提交审核（DRAFT → IN_REVIEW，创建版本快照）。

        Args:
            method_id: 方法 UUID。

        Returns:
            MethodVersion: 新创建的版本（status=in_review）。

        Raises:
            AppError: code="not_found"，当方法不存在时。
            AppError: code="invalid_transition"，当状态非 draft 时。
        """
        async with session_scope(self._factory) as session:
            method = await self._get_and_check_org(session, method_id)
            assert_transition(method.status, StandardStatus.IN_REVIEW)

            new_version_number = method.version_count + 1

            await session.execute(
                sa.update(Method)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    updated_at=sa.func.now(),
                    lock_version=Method.lock_version + 1,
                    version_count=Method.version_count + 1,
                )
                .where(
                    Method.id == method_id,
                    Method.lock_version == method.lock_version,
                )
            )

            version = MethodVersion(
                id=new_id(),
                method_id=method_id,
                version=new_version_number,
                code=method.code,
                display_name=method.display_name,
                description=method.description,
                status=StandardStatus.IN_REVIEW,
                lock_version=0,
            )
            session.add(version)
            await session.flush()
            return version

    async def publish_method(self, method_id: UUID) -> MethodVersion:
        """发布方法（IN_REVIEW → PUBLISHED，版本此后不可变）。

        Args:
            method_id: 方法 UUID。

        Returns:
            MethodVersion: 已发布的版本。

        Raises:
            AppError: code="not_found"，当方法不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
        """
        async with session_scope(self._factory) as session:
            method = await self._get_and_check_org(session, method_id)
            assert_transition(method.status, StandardStatus.PUBLISHED)

            latest = await self._get_latest_version(session, method_id)
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"method_id": str(method_id)},
                )

            await session.execute(
                sa.update(MethodVersion)
                .values(
                    status=StandardStatus.PUBLISHED,
                    published_at=sa.func.now(),
                    published_by=self._actor_id,
                    lock_version=MethodVersion.lock_version + 1,
                )
                .where(
                    MethodVersion.id == latest.id,
                    MethodVersion.lock_version == latest.lock_version,
                )
            )

            await session.execute(
                sa.update(Method)
                .values(
                    status=StandardStatus.PUBLISHED,
                    updated_at=sa.func.now(),
                    lock_version=Method.lock_version + 1,
                )
                .where(
                    Method.id == method_id,
                    Method.lock_version == method.lock_version,
                )
            )

            result = await session.execute(
                sa.select(MethodVersion).where(MethodVersion.id == latest.id)
            )
            return result.scalar_one()

    async def reject_method(
        self, method_id: UUID, reason: str
    ) -> MethodVersion:
        """拒绝方法（IN_REVIEW → REJECTED，设置拒绝原因）。

        Args:
            method_id: 方法 UUID。
            reason: 拒绝原因（必填）。

        Returns:
            MethodVersion: 已拒绝的版本。

        Raises:
            AppError: code="not_found"，当方法不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
        """
        async with session_scope(self._factory) as session:
            method = await self._get_and_check_org(session, method_id)
            assert_transition(method.status, StandardStatus.REJECTED)

            latest = await self._get_latest_version(session, method_id)
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"method_id": str(method_id)},
                )

            await session.execute(
                sa.update(MethodVersion)
                .values(
                    status=StandardStatus.REJECTED,
                    rejection_reason=reason,
                    lock_version=MethodVersion.lock_version + 1,
                )
                .where(
                    MethodVersion.id == latest.id,
                    MethodVersion.lock_version == latest.lock_version,
                )
            )

            await session.execute(
                sa.update(Method)
                .values(
                    status=StandardStatus.REJECTED,
                    updated_at=sa.func.now(),
                    lock_version=Method.lock_version + 1,
                )
                .where(
                    Method.id == method_id,
                    Method.lock_version == method.lock_version,
                )
            )

            result = await session.execute(
                sa.select(MethodVersion).where(MethodVersion.id == latest.id)
            )
            return result.scalar_one()

    async def deprecate_method(self, method_id: UUID) -> MethodVersion:
        """弃用方法（PUBLISHED → DEPRECATED）。

        Args:
            method_id: 方法 UUID。

        Returns:
            MethodVersion: 已弃用的版本。

        Raises:
            AppError: code="not_found"，当方法不存在时。
            AppError: code="invalid_transition"，当状态非 published 时。
        """
        async with session_scope(self._factory) as session:
            method = await self._get_and_check_org(session, method_id)
            assert_transition(method.status, StandardStatus.DEPRECATED)

            published = await self._get_published_version(session, method_id)
            if published is None:
                raise AppError(
                    code="not_found",
                    message="没有已发布的版本",
                    retryable=False,
                    fields={"method_id": str(method_id)},
                )

            await session.execute(
                sa.update(MethodVersion)
                .values(
                    status=StandardStatus.DEPRECATED,
                    deprecated_at=sa.func.now(),
                    deprecated_by=self._actor_id,
                    lock_version=MethodVersion.lock_version + 1,
                )
                .where(
                    MethodVersion.id == published.id,
                    MethodVersion.lock_version == published.lock_version,
                )
            )

            await session.execute(
                sa.update(Method)
                .values(
                    status=StandardStatus.DEPRECATED,
                    updated_at=sa.func.now(),
                    lock_version=Method.lock_version + 1,
                )
                .where(
                    Method.id == method_id,
                    Method.lock_version == method.lock_version,
                )
            )

            result = await session.execute(
                sa.select(MethodVersion).where(
                    MethodVersion.id == published.id
                )
            )
            return result.scalar_one()

    async def resubmit_method(self, method_id: UUID) -> MethodVersion:
        """重新提交（REJECTED → DRAFT → IN_REVIEW，创建新版本）。

        Args:
            method_id: 方法 UUID。

        Returns:
            MethodVersion: 新创建的版本（status=in_review）。

        Raises:
            AppError: code="not_found"，当方法不存在时。
            AppError: code="invalid_transition"，当状态非 rejected 时。
        """
        async with session_scope(self._factory) as session:
            method = await self._get_and_check_org(session, method_id)
            assert_transition(method.status, StandardStatus.DRAFT)
            assert_transition(StandardStatus.DRAFT, StandardStatus.IN_REVIEW)

            new_version_number = method.version_count + 1

            await session.execute(
                sa.update(Method)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    updated_at=sa.func.now(),
                    lock_version=Method.lock_version + 1,
                    version_count=Method.version_count + 1,
                )
                .where(
                    Method.id == method_id,
                    Method.lock_version == method.lock_version,
                )
            )

            version = MethodVersion(
                id=new_id(),
                method_id=method_id,
                version=new_version_number,
                code=method.code,
                display_name=method.display_name,
                description=method.description,
                status=StandardStatus.IN_REVIEW,
                lock_version=0,
            )
            session.add(version)
            await session.flush()
            return version

    async def get_method_by_code(self, code: str) -> dict:
        """按编码查询方法详情（含最新版本）。

        Args:
            code: 方法编码。

        Returns:
            dict: 方法详情。

        Raises:
            AppError: code="not_found"，当方法不存在时。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(Method).where(
                    Method.organization_id == self._org_id,
                    Method.code == code,
                )
            )
            method = result.scalar_one_or_none()
            if method is None:
                raise AppError(
                    code="not_found",
                    message="方法不存在",
                    retryable=False,
                    fields={"code": code},
                )
            latest = await self._get_latest_version(session, method.id)

        return {
            "id": str(method.id),
            "organization_id": str(method.organization_id),
            "code": method.code,
            "display_name": method.display_name,
            "description": method.description,
            "status": method.status,
            "version_count": method.version_count,
            "created_at": method.created_at,
            "updated_at": method.updated_at,
            "lock_version": method.lock_version,
            "latest_version": _method_version_to_dict(latest)
            if latest
            else None,
        }

    async def get_method(self, method_id: UUID) -> dict:
        """查询单个方法详情（含最新版本）。

        Args:
            method_id: 方法 UUID。

        Returns:
            dict: 方法详情，包含 method / latest_version。

        Raises:
            AppError: code="not_found"，当方法不存在时。
        """
        async with self._factory() as session:
            method = await self._get_and_check_org(session, method_id)
            latest = await self._get_latest_version(session, method_id)

        return {
            "id": str(method.id),
            "organization_id": str(method.organization_id),
            "code": method.code,
            "display_name": method.display_name,
            "description": method.description,
            "status": method.status,
            "version_count": method.version_count,
            "created_at": method.created_at,
            "updated_at": method.updated_at,
            "lock_version": method.lock_version,
            "latest_version": _method_version_to_dict(latest)
            if latest
            else None,
        }

    async def list_methods(
        self,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页查询方法列表（含最新版本摘要）。

        Args:
            cursor: 分页游标（base64url 字符串），None 表示第一页。
            page_size: 每页数量（默认 20，最大 100）。

        Returns:
            tuple[list[dict], str | None]: (方法列表, 下一页游标)。
        """
        from packages.standards.repository import (
            _decode_list_cursor,
            _encode_list_cursor,
        )

        effective_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        fetch_limit = effective_size + 1

        query = (
            sa.select(Method)
            .where(Method.organization_id == self._org_id)
            .order_by(Method.created_at.asc(), Method.id.asc())
            .limit(fetch_limit)
        )

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_list_cursor(cursor)
            query = query.where(
                sa.or_(
                    Method.created_at > cursor_created_at,
                    sa.and_(
                        Method.created_at == cursor_created_at,
                        Method.id > cursor_id,
                    ),
                )
            )

        async with self._factory() as session:
            result = await session.execute(query)
            methods = list(result.scalars().all())

            items: list[dict] = []
            for m in methods:
                latest = await self._get_latest_version(session, m.id)
                items.append(
                    {
                        "id": str(m.id),
                        "code": m.code,
                        "display_name": m.display_name,
                        "description": m.description,
                        "status": m.status,
                        "version_count": m.version_count,
                        "created_at": m.created_at,
                        "updated_at": m.updated_at,
                        "lock_version": m.lock_version,
                        "latest_version": _method_version_to_dict(latest)
                        if latest
                        else None,
                    }
                )

        has_more = len(items) > effective_size
        page_items = items[:effective_size]

        next_cursor: str | None = None
        if has_more and page_items:
            last = methods[:effective_size][-1]
            next_cursor = _encode_list_cursor(last.created_at, last.id)

        return page_items, next_cursor

    async def _get_and_check_org(
        self,
        session: AsyncSession,
        method_id: UUID,
    ) -> Method:
        """读取方法并校验组织归属。

        Args:
            session: 异步会话。
            method_id: 方法 UUID。

        Returns:
            Method: 方法实体。

        Raises:
            AppError: code="not_found"，当方法不存在或不属于当前组织时。
        """
        result = await session.execute(
            sa.select(Method).where(Method.id == method_id)
        )
        method = result.scalar_one_or_none()
        if method is None or method.organization_id != self._org_id:
            raise AppError(
                code="not_found",
                message="方法不存在",
                retryable=False,
                fields={"method_id": str(method_id)},
            )
        return method

    async def _get_latest_version(
        self,
        session: AsyncSession,
        method_id: UUID,
    ) -> MethodVersion | None:
        """查询方法的最新版本（按版本号降序取第一条）。"""
        result = await session.execute(
            sa.select(MethodVersion)
            .where(MethodVersion.method_id == method_id)
            .order_by(MethodVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_published_version(
        self,
        session: AsyncSession,
        method_id: UUID,
    ) -> MethodVersion | None:
        """查询方法的已发布版本（status=published）。"""
        result = await session.execute(
            sa.select(MethodVersion)
            .where(
                MethodVersion.method_id == method_id,
                MethodVersion.status == "published",
            )
            .order_by(MethodVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


def _method_version_to_dict(version: MethodVersion) -> dict:
    """将 MethodVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),
        "method_id": str(version.method_id),
        "version": version.version,
        "code": version.code,
        "display_name": version.display_name,
        "description": version.description,
        "status": version.status,
        "published_at": version.published_at,
        "published_by": str(version.published_by) if version.published_by else None,
        "deprecated_at": version.deprecated_at,
        "deprecated_by": str(version.deprecated_by)
        if version.deprecated_by
        else None,
        "rejection_reason": version.rejection_reason,
        "created_at": version.created_at,
        "lock_version": version.lock_version,
    }
