"""标准变量业务服务：创建 / 提交审核 / 发布 / 拒绝 / 弃用 / 重提交 / 别名。

核心流程（IRIP Task 10）：

create_variable(code, display_name, data_type, ...):
  1. 检查编码唯一性 → 若已存在抛 AppError(conflict)；
  2. INSERT variable（status=draft, version_count=0）；
  3. 返回 Variable。

submit_for_review(variable_id):
  1. 读取变量 → 不存在抛 AppError(not_found)；
  2. assert_transition(draft, in_review)；
  3. UPDATE variable status=in_review, version_count+1（乐观锁）；
  4. INSERT variable_version（version=新version_count, status=in_review）；
  5. 返回 VariableVersion。

publish_variable(variable_id, reason):
  1. assert_transition(in_review, published)；
  2. UPDATE version status=published, published_at=now(), published_by=actor；
  3. UPDATE variable status=published；
  4. 版本此后不可变（published_version_immutable）。

reject_variable(variable_id, reason):
  1. assert_transition(in_review, rejected)；
  2. UPDATE version status=rejected, rejection_reason=reason；
  3. UPDATE variable status=rejected。

deprecate_variable(variable_id, reason):
  1. assert_transition(published, deprecated)；
  2. UPDATE version status=deprecated, deprecated_at=now(), deprecated_by=actor；
  3. UPDATE variable status=deprecated。

resubmit(variable_id):
  1. assert_transition(rejected, draft) + assert_transition(draft, in_review)；
  2. UPDATE variable status=in_review, version_count+1；
  3. INSERT 新 VariableVersion（version=新version_count, status=in_review）。

关键约束：
- code 创建后锁定不可修改；
- 乐观锁：WHERE id=? AND lock_version=?，影响 0 行 → 409 conflict；
- 已发布版本核心属性不可修改（published_version_immutable）。
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.pagination import MAX_PAGE_SIZE
from packages.standards.repository import StandardsRepository
from packages.standards.state_machine import StandardStatus, assert_transition
from packages.standards.variables import (
    Variable,
    VariableAlias,
    VariableVersion,
)


def _valid_range_to_json(
    valid_range: tuple[Decimal, Decimal] | None,
) -> list[str] | None:
    """将 (min, max) Decimal 元组转为 JSONB 字符串数组。"""
    if valid_range is None:
        return None
    return [str(valid_range[0]), str(valid_range[1])]


def _valid_range_from_json(
    raw: list[str] | None,
) -> tuple[Decimal, Decimal] | None:
    """将 JSONB 字符串数组转回 (min, max) Decimal 元组。"""
    if raw is None or len(raw) != 2:
        return None
    return Decimal(raw[0]), Decimal(raw[1])


class StandardService:
    """标准变量业务编排服务。

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
        """初始化标准变量服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选，用于发布/弃用时记录操作人）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    # ---- 创建 ----

    async def create_variable(
        self,
        code: str,
        display_name: str,
        data_type: str,
        canonical_unit: str | None = None,
        quantity_kind: str | None = None,
        valid_range: tuple[Decimal, Decimal] | None = None,
    ) -> Variable:
        """创建标准变量（DRAFT 状态, version_count=0）。

        Args:
            code: 变量编码（组织内唯一）。
            display_name: 中文显示名。
            data_type: 数据类型（number / text / boolean / datetime）。
            canonical_unit: 标准单位（可选）。
            quantity_kind: 量纲种类（可选）。
            valid_range: 有效范围 (min, max) Decimal 元组（可选）。

        Returns:
            Variable: 新创建的变量实体。

        Raises:
            AppError: code="conflict"，当编码已存在时。
        """
        valid_range_json = _valid_range_to_json(valid_range)

        async with session_scope(self._factory) as session:
            existing = await StandardsRepository.get_variable_by_code(
                session, code, self._org_id
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message="标准变量编码已存在",
                    retryable=False,
                    fields={"code": code},
                )

            return await StandardsRepository.create_variable(
                session,
                organization_id=self._org_id,
                code=code,
                display_name=display_name,
                data_type=data_type,
                canonical_unit=canonical_unit,
                quantity_kind=quantity_kind,
                valid_range=valid_range_json,
            )

    # ---- 状态转换 ----

    async def submit_for_review(self, variable_id: UUID) -> VariableVersion:
        """提交审核（DRAFT → IN_REVIEW，创建版本快照）。

        Args:
            variable_id: 变量 UUID。

        Returns:
            VariableVersion: 新创建的版本（status=in_review）。

        Raises:
            AppError: code="not_found"，当变量不存在时。
            AppError: code="invalid_transition"，当状态非 draft 时。
            AppError: code="conflict"，当乐观锁冲突时。
        """
        async with session_scope(self._factory) as session:
            variable = await self._get_and_check_org(session, variable_id)
            assert_transition(variable.status, StandardStatus.IN_REVIEW)

            new_version_number = variable.version_count + 1

            updated = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable_id,
                new_status=StandardStatus.IN_REVIEW,
                lock_version=variable.lock_version,
                increment_version_count=True,
            )
            if updated is None:
                raise AppError(
                    code="conflict",
                    message="数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": variable.lock_version},
                )

            version = await StandardsRepository.create_version(
                session,
                variable_id=variable_id,
                version=new_version_number,
                code=variable.code,
                display_name=variable.display_name,
                data_type=variable.data_type,
                canonical_unit=variable.canonical_unit,
                quantity_kind=variable.quantity_kind,
                valid_range=variable.valid_range,
                status=StandardStatus.IN_REVIEW,
            )
            return version

    async def publish_variable(
        self,
        variable_id: UUID,
        reason: str = "",
    ) -> VariableVersion:
        """发布变量（IN_REVIEW → PUBLISHED，版本此后不可变）。

        Args:
            variable_id: 变量 UUID。
            reason: 发布说明（可选，目前未持久化，预留审计扩展）。

        Returns:
            VariableVersion: 已发布的版本（status=published, published_at 已设置）。

        Raises:
            AppError: code="not_found"，当变量不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
            AppError: code="conflict"，当乐观锁冲突时。
        """
        async with session_scope(self._factory) as session:
            variable = await self._get_and_check_org(session, variable_id)
            assert_transition(variable.status, StandardStatus.PUBLISHED)

            latest = await StandardsRepository.get_latest_version(
                session, variable_id
            )
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"variable_id": str(variable_id)},
                )

            updated_version = await StandardsRepository.update_version_status(
                session,
                version_id=latest.id,
                new_status=StandardStatus.PUBLISHED,
                actor_id=self._actor_id,
                lock_version=latest.lock_version,
            )
            if updated_version is None:
                raise AppError(
                    code="conflict",
                    message="版本数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": latest.lock_version},
                )

            updated_var = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable_id,
                new_status=StandardStatus.PUBLISHED,
                lock_version=variable.lock_version,
            )
            if updated_var is None:
                raise AppError(
                    code="conflict",
                    message="数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": variable.lock_version},
                )

            return updated_version

    async def reject_variable(
        self,
        variable_id: UUID,
        reason: str,
    ) -> VariableVersion:
        """拒绝变量（IN_REVIEW → REJECTED，设置拒绝原因）。

        Args:
            variable_id: 变量 UUID。
            reason: 拒绝原因（必填）。

        Returns:
            VariableVersion: 已拒绝的版本（status=rejected, rejection_reason 已设置）。

        Raises:
            AppError: code="not_found"，当变量不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
            AppError: code="conflict"，当乐观锁冲突时。
        """
        async with session_scope(self._factory) as session:
            variable = await self._get_and_check_org(session, variable_id)
            assert_transition(variable.status, StandardStatus.REJECTED)

            latest = await StandardsRepository.get_latest_version(
                session, variable_id
            )
            if latest is None:
                raise AppError(
                    code="not_found",
                    message="没有待审核的版本",
                    retryable=False,
                    fields={"variable_id": str(variable_id)},
                )

            updated_version = await StandardsRepository.update_version_status(
                session,
                version_id=latest.id,
                new_status=StandardStatus.REJECTED,
                reason=reason,
                lock_version=latest.lock_version,
            )
            if updated_version is None:
                raise AppError(
                    code="conflict",
                    message="版本数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": latest.lock_version},
                )

            updated_var = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable_id,
                new_status=StandardStatus.REJECTED,
                lock_version=variable.lock_version,
            )
            if updated_var is None:
                raise AppError(
                    code="conflict",
                    message="数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": variable.lock_version},
                )

            return updated_version

    async def deprecate_variable(
        self,
        variable_id: UUID,
        reason: str = "",
    ) -> VariableVersion:
        """弃用变量（PUBLISHED → DEPRECATED，版本保留可读但阻止新引用）。

        Args:
            variable_id: 变量 UUID。
            reason: 弃用原因（可选）。

        Returns:
            VariableVersion: 已弃用的版本（status=deprecated, deprecated_at 已设置）。

        Raises:
            AppError: code="not_found"，当变量不存在时。
            AppError: code="invalid_transition"，当状态非 published 时。
            AppError: code="conflict"，当乐观锁冲突时。
        """
        async with session_scope(self._factory) as session:
            variable = await self._get_and_check_org(session, variable_id)
            assert_transition(variable.status, StandardStatus.DEPRECATED)

            published = await StandardsRepository.get_published_version(
                session, variable_id
            )
            if published is None:
                raise AppError(
                    code="not_found",
                    message="没有已发布的版本",
                    retryable=False,
                    fields={"variable_id": str(variable_id)},
                )

            updated_version = await StandardsRepository.update_version_status(
                session,
                version_id=published.id,
                new_status=StandardStatus.DEPRECATED,
                actor_id=self._actor_id,
                lock_version=published.lock_version,
            )
            if updated_version is None:
                raise AppError(
                    code="conflict",
                    message="版本数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": published.lock_version},
                )

            updated_var = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable_id,
                new_status=StandardStatus.DEPRECATED,
                lock_version=variable.lock_version,
            )
            if updated_var is None:
                raise AppError(
                    code="conflict",
                    message="数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": variable.lock_version},
                )

            return updated_version

    async def resubmit(self, variable_id: UUID) -> VariableVersion:
        """重新提交（REJECTED → DRAFT → IN_REVIEW，创建新版本）。

        流程：
        1. 断言 rejected → draft 合法；
        2. 断言 draft → in_review 合法；
        3. UPDATE variable status=in_review, version_count+1；
        4. INSERT 新 VariableVersion（version=新version_count, status=in_review）。

        Args:
            variable_id: 变量 UUID。

        Returns:
            VariableVersion: 新创建的版本（status=in_review）。

        Raises:
            AppError: code="not_found"，当变量不存在时。
            AppError: code="invalid_transition"，当状态非 rejected 时。
            AppError: code="conflict"，当乐观锁冲突时。
        """
        async with session_scope(self._factory) as session:
            variable = await self._get_and_check_org(session, variable_id)
            assert_transition(variable.status, StandardStatus.DRAFT)
            assert_transition(StandardStatus.DRAFT, StandardStatus.IN_REVIEW)

            new_version_number = variable.version_count + 1

            updated = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable_id,
                new_status=StandardStatus.IN_REVIEW,
                lock_version=variable.lock_version,
                increment_version_count=True,
            )
            if updated is None:
                raise AppError(
                    code="conflict",
                    message="数据已被修改，请刷新后重试",
                    retryable=False,
                    fields={"lock_version": variable.lock_version},
                )

            version = await StandardsRepository.create_version(
                session,
                variable_id=variable_id,
                version=new_version_number,
                code=variable.code,
                display_name=variable.display_name,
                data_type=variable.data_type,
                canonical_unit=variable.canonical_unit,
                quantity_kind=variable.quantity_kind,
                valid_range=variable.valid_range,
                status=StandardStatus.IN_REVIEW,
            )
            return version

    # ---- 别名 ----

    async def add_alias(
        self,
        variable_id: UUID,
        alias: str,
        language: str = "zh",
    ) -> VariableAlias:
        """为变量添加别名。

        Args:
            variable_id: 变量 UUID。
            alias: 别名文本。
            language: 语言代码（默认 "zh"）。

        Returns:
            VariableAlias: 新创建的别名实体。

        Raises:
            AppError: code="not_found"，当变量不存在时。
            AppError: code="conflict"，当别名已存在时。
        """
        async with session_scope(self._factory) as session:
            await self._get_and_check_org(session, variable_id)
            return await StandardsRepository.add_alias(
                session, variable_id, alias, language
            )

    # ---- 查询 ----

    async def get_variable_by_code(self, code: str) -> dict:
        """按编码查询变量详情（含最新版本 + 全部别名）。

        Args:
            code: 变量编码。

        Returns:
            dict: 变量详情，包含 variable / latest_version / aliases。

        Raises:
            AppError: code="not_found"，当变量不存在时。
        """
        async with self._factory() as session:
            variable = await StandardsRepository.get_variable_by_code(
                session, code, self._org_id
            )
            if variable is None:
                raise AppError(
                    code="not_found",
                    message="标准变量不存在",
                    retryable=False,
                    fields={"code": code},
                )
            latest = await StandardsRepository.get_latest_version(
                session, variable.id
            )
            aliases = await StandardsRepository.list_aliases(
                session, variable.id
            )

        return {
            "id": str(variable.id),
            "organization_id": str(variable.organization_id),
            "code": variable.code,
            "display_name": variable.display_name,
            "data_type": variable.data_type,
            "canonical_unit": variable.canonical_unit,
            "quantity_kind": variable.quantity_kind,
            "valid_range": _valid_range_from_json(variable.valid_range),
            "status": variable.status,
            "version_count": variable.version_count,
            "created_at": variable.created_at,
            "updated_at": variable.updated_at,
            "lock_version": variable.lock_version,
            "latest_version": _version_to_dict(latest) if latest else None,
            "aliases": [
                {"alias": a.alias, "language": a.language} for a in aliases
            ],
        }

    async def get_variable(self, variable_id: UUID) -> dict:
        """查询单个变量详情（含最新版本 + 全部别名）。

        Args:
            variable_id: 变量 UUID。

        Returns:
            dict: 变量详情，包含 variable / latest_version / aliases。

        Raises:
            AppError: code="not_found"，当变量不存在时。
        """
        async with self._factory() as session:
            variable = await self._get_and_check_org(session, variable_id)
            latest = await StandardsRepository.get_latest_version(
                session, variable_id
            )
            aliases = await StandardsRepository.list_aliases(
                session, variable_id
            )

        return {
            "id": str(variable.id),
            "organization_id": str(variable.organization_id),
            "code": variable.code,
            "display_name": variable.display_name,
            "data_type": variable.data_type,
            "canonical_unit": variable.canonical_unit,
            "quantity_kind": variable.quantity_kind,
            "valid_range": _valid_range_from_json(variable.valid_range),
            "status": variable.status,
            "version_count": variable.version_count,
            "created_at": variable.created_at,
            "updated_at": variable.updated_at,
            "lock_version": variable.lock_version,
            "latest_version": _version_to_dict(latest) if latest else None,
            "aliases": [
                {"alias": a.alias, "language": a.language} for a in aliases
            ],
        }

    async def list_variables(
        self,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页查询变量列表（含最新版本摘要）。

        Args:
            cursor: 分页游标（base64url 字符串），None 表示第一页。
            page_size: 每页数量（默认 20，最大 100）。

        Returns:
            tuple[list[dict], str | None]: (变量列表, 下一页游标)。
        """
        effective_size = min(max(page_size, 1), MAX_PAGE_SIZE)

        async with self._factory() as session:
            variables, next_cursor = await StandardsRepository.list_variables(
                session,
                organization_id=self._org_id,
                cursor=cursor,
                page_size=effective_size,
            )

            items: list[dict] = []
            for var in variables:
                latest = await StandardsRepository.get_latest_version(
                    session, var.id
                )
                items.append(
                    {
                        "id": str(var.id),
                        "code": var.code,
                        "display_name": var.display_name,
                        "data_type": var.data_type,
                        "canonical_unit": var.canonical_unit,
                        "quantity_kind": var.quantity_kind,
                        "valid_range": _valid_range_from_json(var.valid_range),
                        "status": var.status,
                        "version_count": var.version_count,
                        "created_at": var.created_at,
                        "updated_at": var.updated_at,
                        "lock_version": var.lock_version,
                        "latest_version": _version_to_dict(latest)
                        if latest
                        else None,
                    }
                )

        return items, next_cursor

    # ---- 内部辅助 ----

    async def _get_and_check_org(
        self,
        session: AsyncSession,
        variable_id: UUID,
    ) -> Variable:
        """读取变量并校验组织归属。

        Args:
            session: 异步会话。
            variable_id: 变量 UUID。

        Returns:
            Variable: 变量实体。

        Raises:
            AppError: code="not_found"，当变量不存在或不属于当前组织时。
        """
        variable = await StandardsRepository.get_variable(session, variable_id)
        if variable is None or variable.organization_id != self._org_id:
            raise AppError(
                code="not_found",
                message="标准变量不存在",
                retryable=False,
                fields={"variable_id": str(variable_id)},
            )
        return variable


def _version_to_dict(version: VariableVersion) -> dict:
    """将 VariableVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),
        "variable_id": str(version.variable_id),
        "version": version.version,
        "code": version.code,
        "display_name": version.display_name,
        "data_type": version.data_type,
        "canonical_unit": version.canonical_unit,
        "quantity_kind": version.quantity_kind,
        "valid_range": _valid_range_from_json(version.valid_range),
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
