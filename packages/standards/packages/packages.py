"""标准包 ORM 模型与业务服务（IRIP Task 12）。

定义两张表：
- standard_package: 标准包主表，code 组织内唯一，含状态机字段；
- standard_package_version: 不可变版本表，存储变量/方法/模板/质量规则引用，
  发布后冻结所有引用，不可修改。

PackageService 提供标准包 CRUD + 引用管理 + 提交验证 + 发布冻结 + 生命周期管理。

验证规则（提交时）：
- 所有变量引用必须指向已发布的变量版本 → ``reference_not_published:variable``
- 所有方法引用必须指向已发布的方法版本 → ``reference_not_published:method``
- 所有模板引用必须指向已发布的模板版本 → ``reference_not_published:template``
- 已发布的包不可修改引用（不可变）。
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.pagination import MAX_PAGE_SIZE
from packages.standards.state_machine import StandardStatus, assert_transition


class PackageStatus(StrEnum):
    """标准包状态枚举（与 state_machine.StandardStatus 值对齐）。

    Attributes:
        DRAFT: 草稿状态，可编辑引用。
        IN_REVIEW: 审核中，已提交审核。
        PUBLISHED: 已发布，引用冻结不可修改（仅可弃用）。
        REJECTED: 已拒绝，可重新编辑引用。
        DEPRECATED: 已弃用。
    """

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class PackageReference:
    """标准包引用：指向某个标准实体的特定版本。

    Attributes:
        ref_type: 引用类型（variable / method / template / quality_rule）。
        ref_id: 被引用实体的 UUID。
        version: 被引用实体的版本号。
    """

    ref_type: str
    ref_id: UUID
    version: int


@dataclass(frozen=True)
class PackageValidationReport:
    """标准包验证报告。

    Attributes:
        valid: 是否通过验证。
        codes: 错误码元组。
    """

    valid: bool
    codes: tuple[str, ...]


class StandardPackage(Base):
    """标准包实体（对应 standard_package 表）。

    code 在组织内唯一（UNIQUE 约束 (organization_id, code)）。

    Attributes:
        id: 包 UUID。
        organization_id: 所属组织 ID。
        code: 包编码（组织内唯一，创建后锁定）。
        display_name: 中文显示名。
        description: 描述（可选）。
        status: 状态（draft / in_review / published / rejected / deprecated）。
        version_count: 已创建版本数（默认 0）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "standard_package"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'draft'"))
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
        sa.UniqueConstraint("organization_id", "code", name="uq_standard_package_org_code"),
    )

    def __repr__(self) -> str:
        return (
            f"StandardPackage(id={self.id!r}, code={self.code!r}, "
            f"status={self.status!r}, version_count={self.version_count!r})"
        )


class StandardPackageVersion(Base):
    """标准包不可变版本实体（对应 standard_package_version 表）。

    提交审核时从当前包草稿创建一行。发布后引用列表冻结不可修改。

    Attributes:
        id: 版本 UUID。
        package_id: 所属包 ID（FK→standard_package.id）。
        version: 版本号（从 1 开始递增）。
        code: 包编码快照。
        display_name: 显示名快照。
        description: 描述快照（可选）。
        variable_refs: 变量引用列表（JSONB，每项含 ref_id/version）。
        method_refs: 方法引用列表（JSONB）。
        template_refs: 模板引用列表（JSONB）。
        quality_rule_refs: 质量规则引用列表（JSONB）。
        status: 版本状态（draft / in_review / published / rejected / deprecated）。
        published_at: 发布时间。
        published_by: 发布人 UUID。
        deprecated_at: 弃用时间。
        deprecated_by: 弃用人 UUID。
        rejection_reason: 拒绝原因。
        created_at: 版本创建时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "standard_package_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    package_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("standard_package.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    variable_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    method_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    template_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    quality_rule_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
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
            f"StandardPackageVersion(id={self.id!r}, "
            f"package_id={self.package_id!r}, "
            f"version={self.version!r}, status={self.status!r})"
        )


class PackageService:
    """标准包业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化标准包服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_package(
        self,
        code: str,
        display_name: str,
        description: str | None = None,
    ) -> StandardPackage:
        """创建标准包（DRAFT 状态, version_count=0）。

        Args:
            code: 包编码（组织内唯一）。
            display_name: 中文显示名。
            description: 描述（可选）。

        Returns:
            StandardPackage: 新创建的包实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        async with session_scope(self._factory) as session:
            existing = await session.execute(
                sa.select(StandardPackage).where(
                    StandardPackage.organization_id == self._org_id,
                    StandardPackage.code == code,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise AppError(
                    code="conflict",
                    message="标准包编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            now = datetime.now(UTC)
            pkg = StandardPackage(
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
            session.add(pkg)
            await session.flush()
            return pkg

    async def add_variable_ref(
        self,
        package_id: UUID,
        variable_id: UUID,
        version: int,
    ) -> None:
        """添加变量引用到包草稿版本。

        Args:
            package_id: 包 UUID。
            variable_id: 变量 UUID。
            version: 变量版本号。

        Raises:
            AppError: code="not_found"，当包不存在时。
            AppError: code="invalid_transition"，当包状态不允许修改时。
        """
        await self._add_ref(package_id, "variable_refs", variable_id, version)

    async def add_method_ref(
        self,
        package_id: UUID,
        method_id: UUID,
        version: int,
    ) -> None:
        """添加方法引用到包草稿版本。

        Args:
            package_id: 包 UUID。
            method_id: 方法 UUID。
            version: 方法版本号。
        """
        await self._add_ref(package_id, "method_refs", method_id, version)

    async def add_template_ref(
        self,
        package_id: UUID,
        template_id: UUID,
        version: int,
    ) -> None:
        """添加模板引用到包草稿版本。

        Args:
            package_id: 包 UUID。
            template_id: 模板 UUID。
            version: 模板版本号。
        """
        await self._add_ref(package_id, "template_refs", template_id, version)

    async def submit_package(self, package_id: UUID) -> PackageValidationReport:
        """提交审核（DRAFT → IN_REVIEW），验证所有引用是否已发布。

        验证规则：
        - 所有变量引用必须指向已发布的变量版本；
        - 所有方法引用必须指向已发布的方法版本；
        - 所有模板引用必须指向已发布的模板版本。

        验证不通过时抛出 AppError(validation_failed)，包含错误码列表。
        验证通过时将包和草稿版本转为 in_review 状态。

        Args:
            package_id: 包 UUID。

        Returns:
            PackageValidationReport: 验证报告。

        Raises:
            AppError: code="not_found"，当包不存在时。
            AppError: code="invalid_transition"，当状态非 draft 时。
            AppError: code="validation_failed"，当验证不通过时。
        """
        async with session_scope(self._factory) as session:
            pkg = await self._get_and_check_org(session, package_id)
            assert_transition(pkg.status, StandardStatus.IN_REVIEW)

            draft = await self._get_draft_version(session, package_id)
            if draft is None:
                raise AppError(
                    code="validation_failed",
                    message="包没有引用，无法提交",
                    retryable=False,
                    fields={"package_id": str(package_id)},
                )

            report = await self._validate_refs(session, draft)
            if not report.valid:
                raise AppError(
                    code="validation_failed",
                    message="包引用验证失败: " + "; ".join(report.codes),
                    retryable=False,
                    fields={"codes": list(report.codes)},
                )

            await session.execute(
                sa.update(StandardPackageVersion)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    lock_version=StandardPackageVersion.lock_version + 1,
                )
                .where(
                    StandardPackageVersion.id == draft.id,
                    StandardPackageVersion.lock_version == draft.lock_version,
                )
            )

            await session.execute(
                sa.update(StandardPackage)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    updated_at=sa.func.now(),
                    lock_version=StandardPackage.lock_version + 1,
                    version_count=StandardPackage.version_count + 1,
                )
                .where(
                    StandardPackage.id == package_id,
                    StandardPackage.lock_version == pkg.lock_version,
                )
            )

            return report

    async def publish_package(self, package_id: UUID) -> StandardPackageVersion:
        """发布包（IN_REVIEW → PUBLISHED），冻结所有引用。

        发布后包版本不可变，不能添加新引用。

        Args:
            package_id: 包 UUID。

        Returns:
            StandardPackageVersion: 已发布的版本。

        Raises:
            AppError: code="not_found"，当包不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
        """
        async with session_scope(self._factory) as session:
            pkg = await self._get_and_check_org(session, package_id)
            assert_transition(pkg.status, StandardStatus.PUBLISHED)

            latest = await self._get_latest_version(session, package_id)
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"package_id": str(package_id)},
                )

            await session.execute(
                sa.update(StandardPackageVersion)
                .values(
                    status=StandardStatus.PUBLISHED,
                    published_at=sa.func.now(),
                    published_by=self._actor_id,
                    lock_version=StandardPackageVersion.lock_version + 1,
                )
                .where(
                    StandardPackageVersion.id == latest.id,
                    StandardPackageVersion.lock_version == latest.lock_version,
                )
            )

            await session.execute(
                sa.update(StandardPackage)
                .values(
                    status=StandardStatus.PUBLISHED,
                    updated_at=sa.func.now(),
                    lock_version=StandardPackage.lock_version + 1,
                )
                .where(
                    StandardPackage.id == package_id,
                    StandardPackage.lock_version == pkg.lock_version,
                )
            )

            result = await session.execute(
                sa.select(StandardPackageVersion).where(StandardPackageVersion.id == latest.id)
            )
            return result.scalar_one()

    async def reject_package(self, package_id: UUID, reason: str) -> StandardPackageVersion:
        """拒绝包（IN_REVIEW → REJECTED，设置拒绝原因）。

        Args:
            package_id: 包 UUID。
            reason: 拒绝原因（必填）。

        Returns:
            StandardPackageVersion: 已拒绝的版本。
        """
        async with session_scope(self._factory) as session:
            pkg = await self._get_and_check_org(session, package_id)
            assert_transition(pkg.status, StandardStatus.REJECTED)

            latest = await self._get_latest_version(session, package_id)
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"package_id": str(package_id)},
                )

            await session.execute(
                sa.update(StandardPackageVersion)
                .values(
                    status=StandardStatus.REJECTED,
                    rejection_reason=reason,
                    lock_version=StandardPackageVersion.lock_version + 1,
                )
                .where(
                    StandardPackageVersion.id == latest.id,
                    StandardPackageVersion.lock_version == latest.lock_version,
                )
            )

            await session.execute(
                sa.update(StandardPackage)
                .values(
                    status=StandardStatus.REJECTED,
                    updated_at=sa.func.now(),
                    lock_version=StandardPackage.lock_version + 1,
                )
                .where(
                    StandardPackage.id == package_id,
                    StandardPackage.lock_version == pkg.lock_version,
                )
            )

            result = await session.execute(
                sa.select(StandardPackageVersion).where(StandardPackageVersion.id == latest.id)
            )
            return result.scalar_one()

    async def deprecate_package(self, package_id: UUID) -> StandardPackageVersion:
        """弃用包（PUBLISHED → DEPRECATED）。

        Args:
            package_id: 包 UUID。

        Returns:
            StandardPackageVersion: 已弃用的版本。
        """
        async with session_scope(self._factory) as session:
            pkg = await self._get_and_check_org(session, package_id)
            assert_transition(pkg.status, StandardStatus.DEPRECATED)

            published = await self._get_published_version(session, package_id)
            if published is None:
                raise AppError(
                    code="not_found",
                    message="没有已发布的版本",
                    retryable=False,
                    fields={"package_id": str(package_id)},
                )

            await session.execute(
                sa.update(StandardPackageVersion)
                .values(
                    status=StandardStatus.DEPRECATED,
                    deprecated_at=sa.func.now(),
                    deprecated_by=self._actor_id,
                    lock_version=StandardPackageVersion.lock_version + 1,
                )
                .where(
                    StandardPackageVersion.id == published.id,
                    StandardPackageVersion.lock_version == published.lock_version,
                )
            )

            await session.execute(
                sa.update(StandardPackage)
                .values(
                    status=StandardStatus.DEPRECATED,
                    updated_at=sa.func.now(),
                    lock_version=StandardPackage.lock_version + 1,
                )
                .where(
                    StandardPackage.id == package_id,
                    StandardPackage.lock_version == pkg.lock_version,
                )
            )

            result = await session.execute(
                sa.select(StandardPackageVersion).where(StandardPackageVersion.id == published.id)
            )
            return result.scalar_one()

    async def get_package_by_code(self, code: str) -> dict:
        """按编码查询包详情（含最新版本）。

        Args:
            code: 包编码。

        Returns:
            dict: 包详情。

        Raises:
            AppError: code="not_found"，当包不存在时。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(StandardPackage).where(
                    StandardPackage.organization_id == self._org_id,
                    StandardPackage.code == code,
                )
            )
            pkg = result.scalar_one_or_none()
            if pkg is None:
                raise AppError(
                    code="not_found",
                    message="标准包不存在",
                    retryable=False,
                    fields={"code": code},
                )
            latest = await self._get_latest_version(session, pkg.id)

        return {
            "id": str(pkg.id),
            "organization_id": str(pkg.organization_id),
            "code": pkg.code,
            "display_name": pkg.display_name,
            "description": pkg.description,
            "status": pkg.status,
            "version_count": pkg.version_count,
            "created_at": pkg.created_at,
            "updated_at": pkg.updated_at,
            "lock_version": pkg.lock_version,
            "latest_version": _package_version_to_dict(latest) if latest else None,
        }

    async def get_package(self, package_id: UUID) -> dict:
        """查询单个包详情（含最新版本）。

        Args:
            package_id: 包 UUID。

        Returns:
            dict: 包详情。

        Raises:
            AppError: code="not_found"，当包不存在时。
        """
        async with self._factory() as session:
            pkg = await self._get_and_check_org(session, package_id)
            latest = await self._get_latest_version(session, package_id)

        return {
            "id": str(pkg.id),
            "organization_id": str(pkg.organization_id),
            "code": pkg.code,
            "display_name": pkg.display_name,
            "description": pkg.description,
            "status": pkg.status,
            "version_count": pkg.version_count,
            "created_at": pkg.created_at,
            "updated_at": pkg.updated_at,
            "lock_version": pkg.lock_version,
            "latest_version": _package_version_to_dict(latest) if latest else None,
        }

    async def list_packages(
        self,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页查询包列表（含最新版本摘要）。

        Args:
            cursor: 分页游标，None 表示第一页。
            page_size: 每页数量（默认 20，最大 100）。

        Returns:
            tuple[list[dict], str | None]: (包列表, 下一页游标)。
        """
        effective_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        fetch_limit = effective_size + 1

        query = (
            sa.select(StandardPackage)
            .where(StandardPackage.organization_id == self._org_id)
            .order_by(
                StandardPackage.created_at.asc(),
                StandardPackage.id.asc(),
            )
            .limit(fetch_limit)
        )

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            query = query.where(
                sa.or_(
                    StandardPackage.created_at > cursor_created_at,
                    sa.and_(
                        StandardPackage.created_at == cursor_created_at,
                        StandardPackage.id > cursor_id,
                    ),
                )
            )

        async with self._factory() as session:
            result = await session.execute(query)
            packages = list(result.scalars().all())

            items: list[dict] = []
            for p in packages:
                latest = await self._get_latest_version(session, p.id)
                items.append(
                    {
                        "id": str(p.id),
                        "code": p.code,
                        "display_name": p.display_name,
                        "description": p.description,
                        "status": p.status,
                        "version_count": p.version_count,
                        "created_at": p.created_at,
                        "updated_at": p.updated_at,
                        "lock_version": p.lock_version,
                        "latest_version": _package_version_to_dict(latest) if latest else None,
                    }
                )

        has_more = len(items) > effective_size
        page_items = items[:effective_size]

        next_cursor: str | None = None
        if has_more and page_items:
            last = packages[:effective_size][-1]
            next_cursor = _encode_cursor(last.created_at, last.id)

        return page_items, next_cursor

    async def _add_ref(
        self,
        package_id: UUID,
        ref_column: str,
        ref_id: UUID,
        version: int,
    ) -> None:
        """添加引用到包草稿版本的指定 JSONB 列。"""
        async with session_scope(self._factory) as session:
            pkg = await self._get_and_check_org(session, package_id)

            if pkg.status == "rejected":
                assert_transition("rejected", "draft")
                await session.execute(
                    sa.update(StandardPackage)
                    .values(
                        status="draft",
                        updated_at=sa.func.now(),
                        lock_version=StandardPackage.lock_version + 1,
                    )
                    .where(
                        StandardPackage.id == package_id,
                        StandardPackage.lock_version == pkg.lock_version,
                    )
                )
                pkg.status = "draft"

            if pkg.status != "draft":
                raise AppError(
                    code="invalid_transition",
                    message="只能在草稿状态下添加引用",
                    retryable=False,
                    fields={"status": pkg.status},
                )

            draft = await self._get_or_create_draft_version(session, pkg)

            current_refs = getattr(draft, ref_column) or []
            current_refs.append({"ref_id": str(ref_id), "version": version})

            await session.execute(
                sa.update(StandardPackageVersion)
                .values(**{ref_column: current_refs})
                .where(StandardPackageVersion.id == draft.id)
            )

    async def _validate_refs(
        self,
        session: AsyncSession,
        draft: StandardPackageVersion,
    ) -> PackageValidationReport:
        """验证包草稿版本的所有引用是否指向已发布版本。"""
        from packages.standards.methods import MethodVersion
        from packages.standards.templates import FactTemplateVersion
        from packages.standards.variables import VariableVersion

        codes: list[str] = []

        # 验证变量引用
        for ref in draft.variable_refs or []:
            var_id = UUID(str(ref["ref_id"]))
            ver = int(ref["version"])
            result = await session.execute(
                sa.select(VariableVersion).where(
                    VariableVersion.variable_id == var_id,
                    VariableVersion.version == ver,
                    VariableVersion.status == "published",
                )
            )
            if result.scalar_one_or_none() is None:
                codes.append("reference_not_published:variable")

        # 验证方法引用
        for ref in draft.method_refs or []:
            method_id = UUID(str(ref["ref_id"]))
            ver = int(ref["version"])
            result = await session.execute(
                sa.select(MethodVersion).where(
                    MethodVersion.method_id == method_id,
                    MethodVersion.version == ver,
                    MethodVersion.status == "published",
                )
            )
            if result.scalar_one_or_none() is None:
                codes.append("reference_not_published:method")

        # 验证模板引用
        for ref in draft.template_refs or []:
            template_id = UUID(str(ref["ref_id"]))
            ver = int(ref["version"])
            result = await session.execute(
                sa.select(FactTemplateVersion).where(
                    FactTemplateVersion.template_id == template_id,
                    FactTemplateVersion.version == ver,
                    FactTemplateVersion.status == "published",
                )
            )
            if result.scalar_one_or_none() is None:
                codes.append("reference_not_published:template")

        return PackageValidationReport(
            valid=len(codes) == 0,
            codes=tuple(codes),
        )

    async def _get_and_check_org(
        self,
        session: AsyncSession,
        package_id: UUID,
    ) -> StandardPackage:
        """读取包并校验组织归属。"""
        result = await session.execute(
            sa.select(StandardPackage).where(StandardPackage.id == package_id)
        )
        pkg = result.scalar_one_or_none()
        if pkg is None or pkg.organization_id != self._org_id:
            raise AppError(
                code="not_found",
                message="标准包不存在",
                retryable=False,
                fields={"package_id": str(package_id)},
            )
        return pkg

    async def _get_or_create_draft_version(
        self,
        session: AsyncSession,
        pkg: StandardPackage,
    ) -> StandardPackageVersion:
        """获取或创建草稿版本。"""
        draft = await self._get_draft_version(session, pkg.id)
        if draft is not None:
            return draft

        new_version_number = pkg.version_count + 1
        draft = StandardPackageVersion(
            id=new_id(),
            package_id=pkg.id,
            version=new_version_number,
            code=pkg.code,
            display_name=pkg.display_name,
            description=pkg.description,
            variable_refs=[],
            method_refs=[],
            template_refs=[],
            quality_rule_refs=[],
            status="draft",
            lock_version=0,
        )
        session.add(draft)
        await session.flush()
        return draft

    async def _get_draft_version(
        self,
        session: AsyncSession,
        package_id: UUID,
    ) -> StandardPackageVersion | None:
        """查询包的草稿版本（status=draft）。"""
        result = await session.execute(
            sa.select(StandardPackageVersion)
            .where(
                StandardPackageVersion.package_id == package_id,
                StandardPackageVersion.status == "draft",
            )
            .order_by(StandardPackageVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_latest_version(
        self,
        session: AsyncSession,
        package_id: UUID,
    ) -> StandardPackageVersion | None:
        """查询包的最新版本。"""
        result = await session.execute(
            sa.select(StandardPackageVersion)
            .where(StandardPackageVersion.package_id == package_id)
            .order_by(StandardPackageVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_published_version(
        self,
        session: AsyncSession,
        package_id: UUID,
    ) -> StandardPackageVersion | None:
        """查询包的已发布版本。"""
        result = await session.execute(
            sa.select(StandardPackageVersion)
            .where(
                StandardPackageVersion.package_id == package_id,
                StandardPackageVersion.status == "published",
            )
            .order_by(StandardPackageVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


def _package_version_to_dict(version: StandardPackageVersion) -> dict:
    """将 StandardPackageVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),
        "package_id": str(version.package_id),
        "version": version.version,
        "code": version.code,
        "display_name": version.display_name,
        "description": version.description,
        "variable_refs": version.variable_refs or [],
        "method_refs": version.method_refs or [],
        "template_refs": version.template_refs or [],
        "quality_rule_refs": version.quality_rule_refs or [],
        "status": version.status,
        "published_at": version.published_at,
        "published_by": str(version.published_by) if version.published_by else None,
        "deprecated_at": version.deprecated_at,
        "deprecated_by": str(version.deprecated_by) if version.deprecated_by else None,
        "rejection_reason": version.rejection_reason,
        "created_at": version.created_at,
        "lock_version": version.lock_version,
    }


def _encode_cursor(created_at: datetime, entity_id: UUID) -> str:
    """编码 keyset 分页游标。"""
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(entity_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。"""
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
