"""AI 工具仓库：``ai_tool`` 表的 async CRUD。

提供 ``AITool`` ORM 模型与 ``AIToolRow`` 领域对象，以及 ``ToolRepository``
静态方法集（``list_all`` / ``get_by_name`` / ``create`` / ``update`` /
``set_enabled``），供 ``ToolRegistry.reload_from_db`` 与 ``ai_tools`` 路由使用。

设计约定（架构设计文档 §3.2 / §7.3）：
- 乐观锁：``update`` / ``set_enabled`` 校验 ``lock_version``，冲突返回 409；
- 不支持删除（D-5）：仓库无 delete 方法；
- 全局表（D-6）：无 ``organization_id`` 列；
- 返回 ``AIToolRow``（frozen dataclass），解耦 ORM 与调用方。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id


class AITool(Base):
    """``ai_tool`` 表 ORM 模型（全局表，无 organization_id）。

    Attributes:
        id: 工具 UUID（PK）。
        name: 工具唯一键（创建后不可改）。
        display_name: 中文显示名。
        description: 工具描述（供 AI 理解工具用途）。
        required_permission: 执行此工具所需的权限字符串。
        parameters_schema: 工具参数的 JSON Schema。
        enabled: 是否启用（禁用后 AI 不可见、不可调用）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
        updated_at: 更新时间。
        updated_by: 最后修改人 UUID（可空）。
    """

    __tablename__ = "ai_tool"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    required_permission: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parameters_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
        server_default=sa.text("true"),
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, server_default=sa.func.now()
    )
    updated_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    category: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="ai_tool",
        server_default="ai_tool",
    )


@dataclass(frozen=True)
class AIToolRow:
    """``ai_tool`` 行的领域对象（仓库返回值，解耦 ORM）。

    与 ``ToolSpec`` 字段对齐，供 ``ToolRegistry.reload_from_db`` 映射为
    ``ToolSpec``；同时供路由层转换为 ``AIToolDTO``（Pydantic）。

    Attributes:
        id: 工具 UUID。
        name: 工具唯一键。
        display_name: 中文显示名。
        description: 工具描述。
        required_permission: 所需权限字符串。
        parameters_schema: 参数 JSON Schema。
        enabled: 是否启用。
        lock_version: 乐观锁版本号。
        created_at: 创建时间。
        updated_at: 更新时间。
        updated_by: 最后修改人 UUID（可空）。
    """

    id: UUID
    name: str
    display_name: str
    description: str
    required_permission: str
    parameters_schema: dict[str, Any]
    enabled: bool
    lock_version: int
    created_at: datetime
    updated_at: datetime
    updated_by: UUID | None = None
    category: str = "ai_tool"


def _to_row(entity: AITool) -> AIToolRow:
    """ORM 实体 → AIToolRow 领域对象。

    Args:
        entity: AITool ORM 实体。

    Returns:
        AIToolRow: 不可变领域对象。
    """
    raw_schema = entity.parameters_schema
    schema_dict: dict[str, Any] = dict(raw_schema) if raw_schema else {}
    return AIToolRow(
        id=entity.id,
        name=entity.name,
        display_name=entity.display_name,
        description=entity.description,
        required_permission=entity.required_permission,
        parameters_schema=schema_dict,
        enabled=entity.enabled,
        lock_version=entity.lock_version,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        updated_by=entity.updated_by,
        category=entity.category,
    )


class ToolRepository:
    """``ai_tool`` 表的 async CRUD 仓库。

    所有方法为静态方法，事务由调用方管理（``session_scope`` 或显式
    begin/commit）。遵循乐观锁约定（§7.3）：``update`` / ``set_enabled``
    校验 ``lock_version``，不匹配时抛 ``AppError(code="conflict")``。
    """

    @staticmethod
    async def list_all(session: AsyncSession) -> list[AIToolRow]:
        """列出全部工具（按 name 排序）。

        Args:
            session: 异步会话。

        Returns:
            list[AIToolRow]: 全部工具行。
        """
        result = await session.execute(sa.select(AITool).order_by(AITool.name))
        entities = result.scalars().all()
        return [_to_row(e) for e in entities]

    @staticmethod
    async def get_by_name(
        session: AsyncSession,
        name: str,
    ) -> AIToolRow | None:
        """按名称查询单个工具。

        Args:
            session: 异步会话。
            name: 工具名称。

        Returns:
            AIToolRow | None: 找到返回领域对象，不存在返回 None。
        """
        result = await session.execute(sa.select(AITool).where(AITool.name == name))
        entity = result.scalar_one_or_none()
        if entity is None:
            return None
        return _to_row(entity)

    @staticmethod
    async def create(
        session: AsyncSession,
        data: dict[str, Any],
        updated_by: UUID,
    ) -> AIToolRow:
        """新建工具。

        Args:
            session: 异步会话。
            data: 工具字段（name / display_name / description /
            updated_by: 创建人 UUID。

        Returns:
            AIToolRow: 新建的工具行。

        Raises:
            AppError: code="conflict"，当工具名已存在时。
        """
        existing = await ToolRepository.get_by_name(session, data["name"])
        if existing is not None:
            raise AppError(
                code="conflict",
                message=f"工具名 '{data['name']}' 已存在",
                retryable=False,
                fields={"name": data["name"]},
            )
        entity = AITool(
            id=new_id(),
            name=data["name"],
            display_name=data["display_name"],
            description=data["description"],
            required_permission=data["required_permission"],
            parameters_schema=data.get("parameters_schema", {}),
            enabled=True,
            lock_version=0,
            updated_by=updated_by,
        )
        session.add(entity)
        await session.flush()
        return _to_row(entity)

    @staticmethod
    async def update(
        session: AsyncSession,
        name: str,
        data: dict[str, Any],
        lock_version: int,
        updated_by: UUID,
    ) -> AIToolRow:
        """更新工具声明字段（不含 name、enabled）。

        乐观锁：校验 ``lock_version``，不匹配时抛 ``conflict``。

        Args:
            session: 异步会话。
            name: 工具名称（不可改）。
            data: 更新字段（display_name / description /
            lock_version: 调用方持有的乐观锁版本号。
            updated_by: 修改人 UUID。

        Returns:
            AIToolRow: 更新后的工具行。

        Raises:
            AppError: code="not_found"，当工具不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        result = await session.execute(sa.select(AITool).where(AITool.name == name))
        entity = result.scalar_one_or_none()
        if entity is None:
            raise AppError(
                code="not_found",
                message=f"工具 '{name}' 不存在",
                retryable=False,
                fields={"name": name},
            )
        if entity.lock_version != lock_version:
            raise AppError(
                code="conflict",
                message="工具已被他人修改，请刷新后重试",
                retryable=False,
                fields={"name": name, "lock_version": lock_version},
            )
        entity.display_name = data["display_name"]
        entity.description = data["description"]
        entity.required_permission = data["required_permission"]
        entity.parameters_schema = data["parameters_schema"]
        entity.lock_version = entity.lock_version + 1
        entity.updated_by = updated_by
        entity.updated_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(entity)
        return _to_row(entity)

    @staticmethod
    async def set_enabled(
        session: AsyncSession,
        name: str,
        enabled: bool,
        lock_version: int,
        updated_by: UUID,
    ) -> AIToolRow:
        """启用/禁用工具。

        乐观锁：校验 ``lock_version``，不匹配时抛 ``conflict``。

        Args:
            session: 异步会话。
            name: 工具名称。
            enabled: 目标启用状态。
            lock_version: 调用方持有的乐观锁版本号。
            updated_by: 修改人 UUID。

        Returns:
            AIToolRow: 更新后的工具行。

        Raises:
            AppError: code="not_found"，当工具不存在时。
            AppError: code="conflict"，当 lock_version 不匹配时。
        """
        result = await session.execute(sa.select(AITool).where(AITool.name == name))
        entity = result.scalar_one_or_none()
        if entity is None:
            raise AppError(
                code="not_found",
                message=f"工具 '{name}' 不存在",
                retryable=False,
                fields={"name": name},
            )
        if entity.lock_version != lock_version:
            raise AppError(
                code="conflict",
                message="工具已被他人修改，请刷新后重试",
                retryable=False,
                fields={"name": name, "lock_version": lock_version},
            )
        entity.enabled = enabled
        entity.lock_version = entity.lock_version + 1
        entity.updated_by = updated_by
        entity.updated_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(entity)
        return _to_row(entity)
