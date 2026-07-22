"""IRIP 组件注册表：ORM 实体 + 发布/查询/废弃服务。

提供：
- Component: 组件主表 ORM（组织内按 name 唯一）；
- ComponentVersion: 组件版本表 ORM
  （按 component_id + version 唯一，已发布不可变）；
- ComponentRegistryService: 发布、查询、列表、废弃的领域服务。

设计要点（IRIP V2-T01）：
- 组件版本一旦发布即不可变（无更新端点，重复发布抛 conflict）；
- manifest_yaml 与 manifest_sha256 持久化，支持内容寻址校验；
- port_schemas 存储输入/输出端口规格的 JSONB 序列化；
- 乐观锁 lock_version 防止并发修改组件主记录；
- 服务依赖注入 session_factory、organization_id、clock。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.clock import Clock, SystemClock
from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.components.manifest import ComponentManifest


class Component(Base):
    """组件主表 ORM 模型（对应 component 表）。

    组织内按 name 唯一，一个组件可包含多个版本。

    Attributes:
        id: 组件 UUID。
        organization_id: 所属组织 ID。
        name: 组件名称（组织内唯一）。
        kind: 组件类别
            （ingestion/transform/quality/statistics/output/model）。
        status: 生命周期状态（draft/published/deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    __tablename__ = "component"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="draft",
        server_default=sa.text("'draft'"),
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"Component(name={self.name!r}, kind={self.kind!r}, "
            f"status={self.status!r})"
        )


class ComponentVersion(Base):
    """组件版本表 ORM 模型（对应 component_version 表）。

    每次发布创建一行，已发布版本不可变。

    Attributes:
        id: 版本 UUID。
        component_id: 所属组件 ID（FK→component.id）。
        version: 语义化版本字符串。
        manifest_yaml: 原始清单 YAML 文本。
        manifest_sha256: 清单 SHA-256 摘要（内容寻址）。
        runtime: 运行时类型（python/cli）。
        port_schemas: 输入/输出端口规格（JSONB）。
        status: 版本状态（默认 published）。
        published_at: 发布时间（UTC）。
        created_at: 创建时间（UTC）。
    """

    __tablename__ = "component_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    component_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "component.id", name="fk_component_version_component_id"
        ),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    manifest_yaml: Mapped[str] = mapped_column(sa.Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    runtime: Mapped[str] = mapped_column(sa.Text, nullable=False)
    port_schemas: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="published",
        server_default=sa.text("'published'"),
    )
    published_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"ComponentVersion(version={self.version!r}, "
            f"runtime={self.runtime!r}, status={self.status!r})"
        )


def _serialize_port_schemas(
    manifest: ComponentManifest,
) -> dict[str, Any]:
    """将 manifest 的端口规格序列化为 JSONB 兼容字典。

    Args:
        manifest: 组件清单。

    Returns:
        dict[str, Any]: ``{"inputs": [...], "outputs": [...]}`` 格式。
    """
    inputs: list[dict[str, Any]] = [
        {
            "name": p.name,
            "data_type": p.data_type,
            "required": p.required,
            "schema": p.schema,
        }
        for p in manifest.inputs
    ]
    outputs: list[dict[str, Any]] = [
        {
            "name": p.name,
            "data_type": p.data_type,
            "required": p.required,
            "schema": p.schema,
        }
        for p in manifest.outputs
    ]
    return {"inputs": inputs, "outputs": outputs}


class ComponentRegistryService:
    """组件注册表领域服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    clock（时间源）。

    核心操作：
    - publish: 发布组件版本（创建/复用组件主记录 + 插入版本记录）；
    - get: 按 name + version 查询版本；
    - get_version_by_id: 按版本 UUID 查询（供 API 详情端点使用）；
    - list: 按 kind / status 过滤列表；
    - deprecate: 废弃组件（主记录 status → deprecated）。

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
        """初始化组件注册表服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            clock: 时钟（可选，默认使用 SystemClock）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def publish(
        self, manifest: ComponentManifest
    ) -> ComponentVersion:
        """发布组件版本。

        流程：
        1. 查找/创建组件主记录（按 organization_id + name）；
        2. kind 一致性校验（同名组件 kind 不可变）；
        3. 版本唯一性检查（同组件同版本不可重复发布）；
        4. 插入 ComponentVersion（status=published, published_at=now）；
        5. 更新组件主记录 status=published, updated_at=now。

        Args:
            manifest: 已校验的组件清单。

        Returns:
            ComponentVersion: 新创建的版本记录。

        Raises:
            AppError: code="conflict"，当版本已存在或 kind 不一致。
        """
        port_schemas = _serialize_port_schemas(manifest)
        now: datetime = self._clock.now()

        async with session_scope(self._factory) as session:
            # 1. 查找/创建组件主记录
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.organization_id == self._org_id,
                    Component.name == manifest.name,
                )
            )
            if component is None:
                component = Component(
                    organization_id=self._org_id,
                    name=manifest.name,
                    kind=manifest.kind,
                    status="draft",
                )
                session.add(component)
                await session.flush()
            else:
                # 2. kind 一致性校验
                if component.kind != manifest.kind:
                    raise AppError(
                        code="conflict",
                        message=(
                            f"组件 kind 不一致: "
                            f"已有 {component.kind}, "
                            f"新版本 {manifest.kind}"
                        ),
                        retryable=False,
                        fields={"name": manifest.name},
                    )

            # 3. 版本唯一性检查
            existing: ComponentVersion | None = await session.scalar(
                sa.select(ComponentVersion).where(
                    ComponentVersion.component_id == component.id,
                    ComponentVersion.version == manifest.version,
                )
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message=(
                        f"组件版本已存在: "
                        f"{manifest.name}@{manifest.version}"
                    ),
                    retryable=False,
                    fields={
                        "name": manifest.name,
                        "version": manifest.version,
                    },
                )

            # 4. 插入版本
            version = ComponentVersion(
                component_id=component.id,
                version=manifest.version,
                manifest_yaml=manifest.raw_yaml,
                manifest_sha256=manifest.sha256,
                runtime=manifest.runtime,
                port_schemas=port_schemas,
                status="published",
                published_at=now,
            )
            session.add(version)
            await session.flush()

            # 5. 更新组件主记录
            component.status = "published"
            component.updated_at = now
            component.lock_version += 1
            await session.flush()

            return version

    async def get(
        self, name: str, version: str
    ) -> ComponentVersion:
        """按 name + version 查询组件版本。

        Args:
            name: 组件名称。
            version: 语义化版本。

        Returns:
            ComponentVersion: 版本记录。

        Raises:
            AppError: code="not_found"，当组件或版本不存在。
        """
        async with session_scope(self._factory) as session:
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.organization_id == self._org_id,
                    Component.name == name,
                )
            )
            if component is None:
                raise AppError(
                    code="not_found",
                    message=f"组件不存在: {name}",
                    retryable=False,
                    fields={"name": name},
                )

            version_row: ComponentVersion | None = await session.scalar(
                sa.select(ComponentVersion).where(
                    ComponentVersion.component_id == component.id,
                    ComponentVersion.version == version,
                )
            )
            if version_row is None:
                raise AppError(
                    code="not_found",
                    message=f"组件版本不存在: {name}@{version}",
                    retryable=False,
                    fields={"name": name, "version": version},
                )

            return version_row

    async def get_version_by_id(
        self, version_id: UUID
    ) -> tuple[Component, ComponentVersion]:
        """按版本 UUID 查询组件 + 版本（供 API 详情端点使用）。

        Args:
            version_id: 版本 UUID。

        Returns:
            tuple[Component, ComponentVersion]: 组件主记录 + 版本记录。

        Raises:
            AppError: code="not_found"，当版本不存在。
        """
        async with session_scope(self._factory) as session:
            row = (
                await session.execute(
                    sa.select(Component, ComponentVersion)
                    .join(
                        ComponentVersion,
                        ComponentVersion.component_id == Component.id,
                    )
                    .where(
                        Component.organization_id == self._org_id,
                        ComponentVersion.id == version_id,
                    )
                )
            ).first()
            if row is None:
                raise AppError(
                    code="not_found",
                    message=f"组件版本不存在: {version_id}",
                    retryable=False,
                    fields={"version_id": str(version_id)},
                )
            component: Component = row[0]
            version: ComponentVersion = row[1]
            return component, version

    async def list(
        self,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[tuple[Component, ComponentVersion]]:
        """列表查询组件及其版本。

        Args:
            kind: 可选，按类别过滤
                （ingestion/transform/quality/statistics/output/model）。
            status: 可选，按组件状态过滤
                （draft/published/deprecated）。

        Returns:
            list[tuple[Component, ComponentVersion]]:
                组件 + 版本记录列表，按 name, created_at 排序。
        """
        async with session_scope(self._factory) as session:
            query = (
                sa.select(Component, ComponentVersion)
                .join(
                    ComponentVersion,
                    ComponentVersion.component_id == Component.id,
                )
                .where(Component.organization_id == self._org_id)
            )
            if kind is not None:
                query = query.where(Component.kind == kind)
            if status is not None:
                query = query.where(Component.status == status)

            query = query.order_by(
                Component.name, ComponentVersion.created_at
            )
            result = await session.execute(query)
            return [(row[0], row[1]) for row in result.all()]

    async def list_versions(self, component_id: UUID) -> list[ComponentVersion]:
        """列出指定组件的所有版本（按版本创建时间降序）。

        Args:
            component_id: 组件主记录 UUID。

        Returns:
            list[ComponentVersion]: 版本记录列表。
        """
        async with session_scope(self._factory) as session:
            result = await session.execute(
                sa.select(ComponentVersion)
                .where(ComponentVersion.component_id == component_id)
                .order_by(ComponentVersion.created_at.desc())
            )
            return list(result.scalars().all())

    async def deprecate(self, name: str) -> Component:
        """废弃组件。

        将组件主记录 status 改为 deprecated。

        Args:
            name: 组件名称。

        Returns:
            Component: 更新后的组件记录。

        Raises:
            AppError: code="not_found"，当组件不存在。
        """
        now: datetime = self._clock.now()
        async with session_scope(self._factory) as session:
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.organization_id == self._org_id,
                    Component.name == name,
                )
            )
            if component is None:
                raise AppError(
                    code="not_found",
                    message=f"组件不存在: {name}",
                    retryable=False,
                    fields={"name": name},
                )

            component.status = "deprecated"
            component.updated_at = now
            component.lock_version += 1
            await session.flush()

            return component
