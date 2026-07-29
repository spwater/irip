"""标准变量数据仓库：Variable / VariableVersion / VariableAlias 的数据库操作。

所有方法接受 AsyncSession 参数，由调用方（StandardService）管理事务边界。
查询使用乐观锁（lock_version）和条件 UPDATE 保证并发安全。

关键操作：
- create_variable: INSERT variable（status=draft, version_count=0）；
- get_variable / get_variable_by_code: 按 ID / (org_id, code) 查询；
- list_variables: keyset 分页列表；
- update_variable_status: UPDATE variable status + lock_version（乐观锁）；
- create_version: INSERT variable_version（快照当前 variable 属性）；
- get_latest_version / get_published_version: 按版本号 / 状态查询；
- update_version_status: UPDATE version status（含不可变性校验）；
- add_alias / find_by_alias / list_aliases: 别名 CRUD。
"""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.standards.variables import (
    Variable,
    VariableAlias,
    VariableVersion,
)


class StandardsRepository:
    """标准变量持久化仓库。

    所有方法为纯数据访问，不含业务逻辑——业务编排由 StandardService 负责。
    """

    # ---- Variable CRUD ----

    @staticmethod
    async def create_variable(
        session: AsyncSession,
        *,
        organization_id: UUID,
        code: str,
        display_name: str,
        data_type: str,
        canonical_unit: str | None = None,
        quantity_kind: str | None = None,
        valid_range: list[str] | None = None,
    ) -> Variable:
        """INSERT 标准变量记录（status=draft, version_count=0）。

        Args:
            session: 异步会话（事务由调用方管理）。
            organization_id: 组织 ID。
            code: 变量编码。
            display_name: 显示名。
            data_type: 数据类型。
            canonical_unit: 标准单位（可选）。
            quantity_kind: 量纲种类（可选）。
            valid_range: 有效范围 JSONB 字符串数组（可选）。

        Returns:
            Variable: 插入后的实体。
        """
        now = datetime.now(UTC)
        variable = Variable(
            id=new_id(),
            organization_id=organization_id,
            code=code,
            display_name=display_name,
            data_type=data_type,
            canonical_unit=canonical_unit,
            quantity_kind=quantity_kind,
            valid_range=valid_range,
            status="draft",
            version_count=0,
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(variable)
        await session.flush()
        return variable

    @staticmethod
    async def get_variable(
        session: AsyncSession,
        variable_id: UUID,
    ) -> Variable | None:
        """按 ID 查询标准变量。

        Args:
            session: 异步会话。
            variable_id: 变量 UUID。

        Returns:
            Variable | None: 变量实体，不存在返回 None。
        """
        result = await session.execute(sa.select(Variable).where(Variable.id == variable_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_variable_by_code(
        session: AsyncSession,
        code: str,
        organization_id: UUID,
    ) -> Variable | None:
        """按组织 ID 和编码查询标准变量（编码唯一性校验）。

        Args:
            session: 异步会话。
            code: 变量编码。
            organization_id: 组织 ID。

        Returns:
            Variable | None: 变量实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(Variable).where(
                Variable.organization_id == organization_id,
                Variable.code == code,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_variables(
        session: AsyncSession,
        organization_id: UUID,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[Variable], str | None]:
        """分页查询标准变量列表。

        排序：created_at ASC, id ASC。
        Keyset 分页：cursor 编码 (created_at_iso, id)。

        Args:
            session: 异步会话。
            organization_id: 组织 ID（过滤条件）。
            cursor: 上一页返回的 next_cursor（base64url 字符串），None 表示第一页。
            page_size: 每页数量。

        Returns:
            tuple[list[Variable], str | None]: (变量列表, 下一页游标)。

        Raises:
            AppError: code="invalid_cursor"，当游标格式不合法时。
        """
        fetch_limit = page_size + 1

        query = (
            sa.select(Variable)
            .where(Variable.organization_id == organization_id)
            .order_by(Variable.created_at.asc(), Variable.id.asc())
            .limit(fetch_limit)
        )

        if cursor is not None:
            cursor_created_at, cursor_id = _decode_list_cursor(cursor)
            query = query.where(
                sa.or_(
                    Variable.created_at > cursor_created_at,
                    sa.and_(
                        Variable.created_at == cursor_created_at,
                        Variable.id > cursor_id,
                    ),
                )
            )

        result = await session.execute(query)
        variables = list(result.scalars().all())

        has_more = len(variables) > page_size
        page_items = variables[:page_size]

        next_cursor: str | None = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = _encode_list_cursor(last.created_at, last.id)

        return page_items, next_cursor

    @staticmethod
    async def update_variable_status(
        session: AsyncSession,
        *,
        variable_id: UUID,
        new_status: str,
        lock_version: int,
        increment_version_count: bool = False,
    ) -> Variable | None:
        """UPDATE 变量状态（乐观锁）。

        UPDATE variable SET status=?, updated_at=now(), lock_version=lock_version+1
        [, version_count=version_count+1]
        WHERE id=? AND lock_version=?

        Args:
            session: 异步会话。
            variable_id: 变量 UUID。
            new_status: 新状态。
            lock_version: 客户端持有的乐观锁版本号。
            increment_version_count: 是否同时递增 version_count（提交审核 / 重提交时为 True）。

        Returns:
            Variable | None: 更新后的实体；None 表示 lock_version 不匹配或不存在。
        """
        values: dict[str, object] = {
            "status": new_status,
            "updated_at": sa.func.now(),
            "lock_version": Variable.lock_version + 1,
        }
        if increment_version_count:
            values["version_count"] = Variable.version_count + 1

        result = await session.execute(
            sa.update(Variable)
            .values(**values)
            .where(
                Variable.id == variable_id,
                Variable.lock_version == lock_version,
            )
            .returning(Variable)
        )
        return result.scalar_one_or_none()

    # ---- VariableVersion CRUD ----

    @staticmethod
    async def create_version(
        session: AsyncSession,
        *,
        variable_id: UUID,
        version: int,
        code: str,
        display_name: str,
        data_type: str,
        canonical_unit: str | None = None,
        quantity_kind: str | None = None,
        valid_range: list[str] | None = None,
        status: str = "in_review",
    ) -> VariableVersion:
        """INSERT 版本记录（从当前 variable 属性快照）。

        Args:
            session: 异步会话。
            variable_id: 所属变量 ID。
            version: 版本号。
            code: 变量编码快照。
            display_name: 显示名快照。
            data_type: 数据类型快照。
            canonical_unit: 标准单位快照（可选）。
            quantity_kind: 量纲快照（可选）。
            valid_range: 有效范围快照（可选）。
            status: 版本状态（默认 "in_review"）。

        Returns:
            VariableVersion: 插入后的实体。
        """
        version_row = VariableVersion(
            id=new_id(),
            variable_id=variable_id,
            version=version,
            code=code,
            display_name=display_name,
            data_type=data_type,
            canonical_unit=canonical_unit,
            quantity_kind=quantity_kind,
            valid_range=valid_range,
            status=status,
            lock_version=0,
        )
        session.add(version_row)
        await session.flush()
        return version_row

    @staticmethod
    async def get_version(
        session: AsyncSession,
        version_id: UUID,
    ) -> VariableVersion | None:
        """按 ID 查询版本。

        Args:
            session: 异步会话。
            version_id: 版本 UUID。

        Returns:
            VariableVersion | None: 版本实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(VariableVersion).where(VariableVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_latest_version(
        session: AsyncSession,
        variable_id: UUID,
    ) -> VariableVersion | None:
        """查询变量的最新版本（按版本号降序取第一条）。

        Args:
            session: 异步会话。
            variable_id: 变量 ID。

        Returns:
            VariableVersion | None: 最新版本实体，无版本返回 None。
        """
        result = await session.execute(
            sa.select(VariableVersion)
            .where(VariableVersion.variable_id == variable_id)
            .order_by(VariableVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_published_version(
        session: AsyncSession,
        variable_id: UUID,
    ) -> VariableVersion | None:
        """查询变量的已发布版本（status=published）。

        Args:
            session: 异步会话。
            variable_id: 变量 ID。

        Returns:
            VariableVersion | None: 已发布版本实体，无则返回 None。
        """
        result = await session.execute(
            sa.select(VariableVersion)
            .where(
                VariableVersion.variable_id == variable_id,
                VariableVersion.status == "published",
            )
            .order_by(VariableVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_version_status(
        session: AsyncSession,
        *,
        version_id: UUID,
        new_status: str,
        actor_id: UUID | None = None,
        reason: str | None = None,
        lock_version: int = 0,
    ) -> VariableVersion | None:
        """UPDATE 版本状态（含不可变性校验 + 乐观锁）。

        不可变性规则：
        - 已发布（published）版本仅可转为 deprecated，其他变更抛 ``published_version_immutable``；
        - 已弃用（deprecated）版本完全不可修改；
        - 已拒绝（rejected）版本完全不可修改（重提交创建新版本）。

        发布时设置 published_at + published_by；
        拒绝时设置 rejection_reason；
        弃用时设置 deprecated_at + deprecated_by。

        Args:
            session: 异步会话。
            version_id: 版本 UUID。
            new_status: 新状态。
            actor_id: 操作人 UUID（发布/弃用时设置）。
            reason: 拒绝原因（拒绝时设置）。
            lock_version: 乐观锁版本号。

        Returns:
            VariableVersion | None: 更新后的实体；None 表示 lock_version 不匹配。

        Raises:
            AppError: code="published_version_immutable"，当修改已发布/已弃用版本时。
            AppError: code="not_found"，当版本不存在时。
        """
        version = await StandardsRepository.get_version(session, version_id)
        if version is None:
            raise AppError(
                code="not_found",
                message="版本不存在",
                retryable=False,
                fields={"version_id": str(version_id)},
            )

        # 不可变性校验
        if version.status == "published" and new_status != "deprecated":
            raise AppError(
                code="published_version_immutable",
                message="已发布的版本不可修改",
                retryable=False,
                fields={"version_id": str(version_id), "status": version.status},
            )
        if version.status == "deprecated":
            raise AppError(
                code="published_version_immutable",
                message="已弃用的版本不可修改",
                retryable=False,
                fields={"version_id": str(version_id), "status": version.status},
            )
        if version.status == "rejected":
            raise AppError(
                code="published_version_immutable",
                message="已拒绝的版本不可修改，请重新提交创建新版本",
                retryable=False,
                fields={"version_id": str(version_id), "status": version.status},
            )

        values: dict[str, object] = {
            "status": new_status,
            "lock_version": VariableVersion.lock_version + 1,
        }
        if new_status == "published":
            values["published_at"] = sa.func.now()
            values["published_by"] = actor_id
        elif new_status == "rejected":
            values["rejection_reason"] = reason
        elif new_status == "deprecated":
            values["deprecated_at"] = sa.func.now()
            values["deprecated_by"] = actor_id

        result = await session.execute(
            sa.update(VariableVersion)
            .values(**values)
            .where(
                VariableVersion.id == version_id,
                VariableVersion.lock_version == lock_version,
            )
            .returning(VariableVersion)
        )
        return result.scalar_one_or_none()

    # ---- VariableAlias CRUD ----

    @staticmethod
    async def add_alias(
        session: AsyncSession,
        variable_id: UUID,
        alias: str,
        language: str = "zh",
    ) -> VariableAlias:
        """添加别名（检查 (variable_id, alias) 唯一性）。

        Args:
            session: 异步会话。
            variable_id: 变量 ID。
            alias: 别名文本。
            language: 语言代码（默认 "zh"）。

        Returns:
            VariableAlias: 新创建的别名实体。

        Raises:
            AppError: code="conflict"，当别名已存在时。
        """
        existing = await session.execute(
            sa.select(VariableAlias).where(
                VariableAlias.variable_id == variable_id,
                VariableAlias.alias == alias,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise AppError(
                code="conflict",
                message="别名已存在",
                retryable=False,
                fields={"alias": alias},
            )

        alias_row = VariableAlias(
            id=new_id(),
            variable_id=variable_id,
            alias=alias,
            language=language,
        )
        session.add(alias_row)
        await session.flush()
        return alias_row

    @staticmethod
    async def find_by_alias(
        session: AsyncSession,
        alias: str,
        organization_id: UUID,
    ) -> Variable | None:
        """通过别名查找标准变量（JOIN variable_alias + variable）。

        Args:
            session: 异步会话。
            alias: 别名文本。
            organization_id: 组织 ID（过滤条件）。

        Returns:
            Variable | None: 变量实体，不存在返回 None。
        """
        result = await session.execute(
            sa.select(Variable)
            .join(VariableAlias, VariableAlias.variable_id == Variable.id)
            .where(
                VariableAlias.alias == alias,
                Variable.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_aliases(
        session: AsyncSession,
        variable_id: UUID,
    ) -> list[VariableAlias]:
        """查询变量的全部别名。

        Args:
            session: 异步会话。
            variable_id: 变量 ID。

        Returns:
            list[VariableAlias]: 别名列表（按创建时间升序）。
        """
        result = await session.execute(
            sa.select(VariableAlias)
            .where(VariableAlias.variable_id == variable_id)
            .order_by(VariableAlias.created_at.asc())
        )
        return list(result.scalars().all())


# ---- 游标编解码 ----


def _encode_list_cursor(created_at: datetime, variable_id: UUID) -> str:
    """编码 keyset 分页游标。

    格式：base64url( JSON {"v": created_at_iso, "id": uuid_str} )
    """
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(variable_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_list_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Returns:
        tuple[datetime, UUID]: (created_at, variable_id)。

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
        variable_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：id 字段不是合法 UUID",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    return created_at, variable_id
