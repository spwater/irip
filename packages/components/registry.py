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
    # 从 manifest 提取的实验对象编码（独立列，便于查询关联）
    experimental_object_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
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
        self, manifest: ComponentManifest,
        experimental_object_code: str | None = None,
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
            # 1. 自动生成编码，查找/创建组件主记录
            from packages.common.ids import gen_code
            component_name = gen_code("iface")
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.organization_id == self._org_id,
                    Component.name == manifest.name,
                )
            )
            if component is None:
                component = Component(
                    organization_id=self._org_id,
                    name=component_name,
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

            # 3. 自动计算版本号（忽略 YAML 里的 version，系统自动管理）
            # 查当前组件的最大版本号
            latest_version: ComponentVersion | None = await session.scalar(
                sa.select(ComponentVersion)
                .where(ComponentVersion.component_id == component.id)
                .order_by(ComponentVersion.created_at.desc())
                .limit(1)
            )
            if latest_version:
                # 递增 patch 号（如 1.0.5 → 1.0.6）
                parts = latest_version.version.split('.')
                if len(parts) == 3:
                    auto_version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
                else:
                    auto_version = "1.0.0"
            else:
                # 新组件，从 1.0.0 开始
                auto_version = "1.0.0"

            # 版本唯一性检查（系统自动分配的版本号一般不会冲突，但防御性检查）
            existing: ComponentVersion | None = await session.scalar(
                sa.select(ComponentVersion).where(
                    ComponentVersion.component_id == component.id,
                    ComponentVersion.version == auto_version,
                )
            )
            if existing is not None:
                # 如果 manifest 内容完全相同（sha256 一致），说明是回滚同一个版本，直接返回
                if existing.manifest_sha256 == manifest.sha256:
                    return existing
                # 版本号冲突，再递增一次
                parts = auto_version.split('.')
                auto_version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"

            # 4. 替换 manifest_yaml 里的 name 为自动生成的编码
            import re
            updated_yaml = re.sub(
                r'^name:\s*\S+',
                f'name: {component_name}',
                manifest.raw_yaml,
                count=1,
                flags=re.MULTILINE,
            )

            # 5. 插入版本
            version = ComponentVersion(
                component_id=component.id,
                version=auto_version,
                manifest_yaml=updated_yaml,
                manifest_sha256=manifest.sha256,
                experimental_object_code=experimental_object_code,
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
        """列表查询组件及其最新版本。

        每个组件只返回 created_at 最新的那个版本（即当前活跃版本）。

        Args:
            kind: 可选，按类别过滤
                （ingestion/transform/quality/statistics/output/model）。
            status: 可选，按组件状态过滤
                （draft/published/deprecated）。

        Returns:
            list[tuple[Component, ComponentVersion]]:
                组件 + 最新版本记录列表。
        """
        async with session_scope(self._factory) as session:
            # 子查询：每个组件 created_at 最新的版本 id
            latest_version_subq = (
                sa.select(
                    ComponentVersion.component_id,
                    sa.func.max(ComponentVersion.created_at).label("max_created"),
                )
                .group_by(ComponentVersion.component_id)
                .subquery()
            )
            query = (
                sa.select(Component, ComponentVersion)
                .join(
                    ComponentVersion,
                    ComponentVersion.component_id == Component.id,
                )
                .join(
                    latest_version_subq,
                    sa.and_(
                        latest_version_subq.c.component_id == ComponentVersion.component_id,
                        latest_version_subq.c.max_created == ComponentVersion.created_at,
                    ),
                )
                .where(Component.organization_id == self._org_id)
            )
            if kind is not None:
                query = query.where(Component.kind == kind)
            if status is not None:
                query = query.where(Component.status == status)

            query = query.order_by(Component.name)
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

    async def restore(self, name: str) -> Component:
        """恢复组件（deprecated → published）。

        Args:
            name: 组件名称。

        Returns:
            Component: 更新后的组件记录。
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
            component.status = "published"
            component.updated_at = now
            component.lock_version += 1
            await session.flush()
            return component

    async def activate_version(self, version_id: UUID) -> ComponentVersion:
        """切换组件的当前活跃版本（回滚）。

        将目标版本的 created_at 更新为当前时间，使其成为列表中最新的版本。
        同时恢复组件主记录状态为 published。

        Args:
            version_id: 要激活的组件版本 UUID。

        Returns:
            ComponentVersion: 激活后的版本记录。

        Raises:
            AppError: code="not_found"，当版本不存在。
        """
        now: datetime = self._clock.now()
        async with session_scope(self._factory) as session:
            version: ComponentVersion | None = await session.scalar(
                sa.select(ComponentVersion).where(
                    ComponentVersion.id == version_id,
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"组件版本不存在: {version_id}",
                    retryable=False,
                )
            # 更新 created_at 使其成为最新版本
            version.created_at = now
            # 恢复组件主记录状态
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.id == version.component_id,
                )
            )
            if component and component.status == "deprecated":
                component.status = "published"
                component.updated_at = now
                component.lock_version += 1
            await session.flush()
            return version

    async def delete_component(self, component_id: UUID) -> None:
        """彻底删除组件及其所有版本。

        Args:
            component_id: 组件主记录 UUID。
        """
        async with session_scope(self._factory) as session:
            # 删除所有版本
            await session.execute(
                sa.delete(ComponentVersion).where(
                    ComponentVersion.component_id == component_id
                )
            )
            # 删除主记录
            await session.execute(
                sa.delete(Component).where(Component.id == component_id)
            )
            await session.flush()
