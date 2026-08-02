"""IRIP 组件注册表：ORM 实体 + 发布/查询/废弃服务。

提供：
- Component: 组件主表 ORM（部门内按 name 唯一）；
- ComponentVersion: 组件版本表 ORM
  （按 component_id + version 唯一，已发布不可变）；
- ComponentRegistryService: 发布、查询、列表、废弃的领域服务。

设计要点（IRIP V2-T01）：
- 组件版本一旦发布即不可变（无更新端点，重复发布抛 conflict）；
- manifest_yaml 与 manifest_sha256 持久化，支持内容寻址校验；
- port_schemas 存储输入/输出端口规格的 JSONB 序列化；
- 乐观锁 lock_version 防止并发修改组件主记录；
- 服务依赖注入 session_factory、department_id、clock。
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
from packages.common.dept_visibility import compute_visible_dept_ids
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.components.manifest import ComponentManifest


class Component(Base):
    """组件主表 ORM 模型（对应 component 表）。

    部门内按 name 唯一，一个组件可包含多个版本。

    Attributes:
        id: 组件 UUID。
        department_id: 所属部门 ID。
        name: 组件名称（部门内唯一）。
        kind: 组件类别
            （ingestion/transform/quality/statistics/output/model）。
        status: 生命周期状态（draft/published/deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    __tablename__ = "component"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
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
    active_version_id: Mapped[UUID | None] = mapped_column(
        GUID,
        nullable=True,
        default=None,
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
    # ---- 多租户隔离键升级：A 类四列 ----
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id"),
        nullable=False,
        comment="所属部门 ID（内置组件归 root）",
    )
    visible_departments: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
        comment="跨实验室可见部门 ID 列表",
    )
    visibility_scope: Mapped[str] = mapped_column(
        sa.String(10),
        nullable=False,
        server_default=sa.text("'tree'"),
        comment="可见范围：tree / explicit / all",
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("app_user.id"),
        nullable=False,
        comment="所有者用户 ID",
    )

    def __repr__(self) -> str:
        return f"Component(name={self.name!r}, kind={self.kind!r}, status={self.status!r})"


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
        sa.ForeignKey("component.id", name="fk_component_version_component_id"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    manifest_yaml: Mapped[str] = mapped_column(sa.Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # 从 manifest 提取的实验对象编码（独立列，便于查询关联）
    experimental_object_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    equipment_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    runtime: Mapped[str] = mapped_column(sa.Text, nullable=False)
    port_schemas: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
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

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    clock（时间源）。

    核心操作：
    - publish: 发布组件版本（创建/复用组件主记录 + 插入版本记录）；
    - get: 按 name + version 查询版本；
    - get_version_by_id: 按版本 UUID 查询（供 API 详情端点使用）；
    - list: 按 kind / status 过滤列表；
    - deprecate: 废弃组件（主记录 status → deprecated）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _clock: 时钟实例。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID,
        clock: Clock | None = None,
    ) -> None:
        """初始化组件注册表服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID（用于组件所有者 owner_user_id）。
            clock: 时钟（可选，默认使用 SystemClock）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._clock: Clock = clock if clock is not None else SystemClock()

    # ---- 公开只读属性（替代路由直接访问私有属性） ----

    @property
    def department_id(self) -> UUID:
        """当前部门 ID（公开只读访问，替代 ``service._dept_id``）。"""
        return self._dept_id

    @property
    def actor_id(self) -> UUID:
        """当前操作者用户 ID（公开只读访问）。"""
        return self._actor_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂（公开只读访问，替代 ``service._factory``）。"""
        return self._factory

    async def publish(
        self,
        manifest: ComponentManifest,
        experimental_object_code: str | None = None,
        equipment_id: str | None = None,
        department_id: UUID | None = None,
    ) -> ComponentVersion:
        """发布组件版本。

        流程：
        1. 查找/创建组件主记录（按 department_id + name）；
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
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            # 1. 自动生成编码，查找/创建组件主记录
            from packages.common.ids import gen_code

            # 占位值 iface_ffffffff 表示新建接口，不用于查找
            is_placeholder = manifest.name.startswith("iface_ffff")
            component: Component | None = None
            if not is_placeholder:
                # 非占位值：按 name 查已有组件（发新版本）
                component = await session.scalar(
                    sa.select(Component).where(
                        Component.department_id.in_(visible_ids),
                        Component.name == manifest.name,
                    )
                )
            if component is None:
                # 新建接口：用自动生成的编码
                component = Component(
                    department_id=department_id or self._dept_id,
                    owner_user_id=self._actor_id,
                    name=gen_code("iface"),
                    kind=manifest.kind,
                    status="draft",
                )
                session.add(component)
                await session.flush()
            else:
                # 编辑发新版本：更新归属部门（如果传了新的）
                if department_id is not None:
                    component.department_id = department_id
                # 2. kind 一致性校验
                if component.kind != manifest.kind:
                    raise AppError(
                        code="conflict",
                        message=(
                            f"组件 kind 不一致: 已有 {component.kind}, 新版本 {manifest.kind}"
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
                parts = latest_version.version.split(".")
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
                parts = auto_version.split(".")
                auto_version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"

            # 4. 替换 manifest_yaml 里的 name 为组件真实编码
            import re

            updated_yaml = re.sub(
                r"^name:\s*\S+.*",
                f"name: {component.name}",
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
                equipment_id=equipment_id,
                runtime=manifest.runtime,
                port_schemas=port_schemas,
                status="published",
                published_at=now,
            )
            session.add(version)
            await session.flush()

            # 5. 更新组件主记录
            component.active_version_id = version.id
            component.status = "published"
            component.updated_at = now
            component.lock_version += 1
            await session.flush()

            return version

    async def get(self, name: str, version: str) -> ComponentVersion:
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
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.department_id.in_(visible_ids),
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

    async def get_latest(self, name: str) -> ComponentVersion:
        """按 name 查询组件的当前活跃版本。

        优先取 component.active_version_id 指定的版本（支持回滚），
        无 active_version_id 时取最新 published 版本。

        Args:
            name: 组件名称。

        Returns:
            ComponentVersion: 当前活跃版本记录。

        Raises:
            AppError: code="not_found"，当组件不存在或无已发布版本。
        """
        async with session_scope(self._factory) as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.department_id.in_(visible_ids),
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

            # 优先取 active_version_id 指定的版本
            if component.active_version_id is not None:
                version_row: ComponentVersion | None = await session.scalar(
                    sa.select(ComponentVersion).where(
                        ComponentVersion.id == component.active_version_id,
                        ComponentVersion.status == "published",
                    )
                )
                if version_row is not None:
                    return version_row

            # 回退：取最新 published 版本
            version_row = await session.scalar(
                sa.select(ComponentVersion)
                .where(
                    ComponentVersion.component_id == component.id,
                    ComponentVersion.status == "published",
                )
                .order_by(ComponentVersion.created_at.desc())
                .limit(1)
            )
            if version_row is None:
                raise AppError(
                    code="not_found",
                    message=f"组件无已发布版本: {name}",
                    retryable=False,
                    fields={"name": name},
                )

            return version_row

    async def get_version_by_id(self, version_id: UUID) -> tuple[Component, ComponentVersion]:
        """按版本 UUID 查询组件 + 版本（供 API 详情端点使用）。

        Args:
            version_id: 版本 UUID。

        Returns:
            tuple[Component, ComponentVersion]: 组件主记录 + 版本记录。

        Raises:
            AppError: code="not_found"，当版本不存在。
        """
        async with session_scope(self._factory) as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            row = (
                await session.execute(
                    sa.select(Component, ComponentVersion)
                    .join(
                        ComponentVersion,
                        ComponentVersion.component_id == Component.id,
                    )
                    .where(
                        Component.department_id.in_(visible_ids),
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
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            # 优先用 active_version_id 关联，没有才取 created_at 最新的版本
            query = sa.select(Component, ComponentVersion).outerjoin(
                ComponentVersion,
                sa.and_(
                    ComponentVersion.id == Component.active_version_id,
                ),
            )
            rows = (await session.execute(query)).all()
            # 过滤掉没有版本的，对没有 active_version_id 的用 created_at 最新
            result: list[tuple[Component, ComponentVersion]] = []
            no_active = [row for row in rows if row[1] is None]
            for comp, ver in rows:
                if ver is not None:
                    result.append((comp, ver))
            if no_active:
                # 对没有 active_version_id 的组件取最新 published 版本
                for comp, _ in no_active:
                    fallback = await session.scalar(
                        sa.select(ComponentVersion)
                        .where(
                            ComponentVersion.component_id == comp.id,
                            ComponentVersion.status == "published",
                        )
                        .order_by(ComponentVersion.created_at.desc())
                        .limit(1)
                    )
                    if fallback:
                        result.append((comp, fallback))

            # 过滤 kind 和 status
            filtered = [(c, v) for c, v in result if c.department_id in visible_ids]
            if kind is not None:
                filtered = [(c, v) for c, v in filtered if c.kind == kind]
            if status is not None:
                filtered = [(c, v) for c, v in filtered if c.status == status]
            filtered.sort(key=lambda x: x[0].name)
            return filtered

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
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.department_id.in_(visible_ids),
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
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.department_id.in_(visible_ids),
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

        技术设计文档 F-03 §8.3：回滚通过修改组件主表的指针
        ``current_version_id`` 实现，不修改版本行的 ``created_at`` 时间戳
        （不可变表，不允许 UPDATE）。

        将组件主记录的 ``current_version_id`` 指向目标版本，
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
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            # C-03 IDOR 修复：通过 JOIN Component 确保版本属于当前部门
            version: ComponentVersion | None = await session.scalar(
                sa.select(ComponentVersion)
                .join(Component, ComponentVersion.component_id == Component.id)
                .where(
                    ComponentVersion.id == version_id,
                    Component.department_id.in_(visible_ids),
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"组件版本不存在: {version_id}",
                    retryable=False,
                )

            # 查询组件主记录，修改 active_version_id 指针
            # 不修改 version 的 created_at（不可变表，不允许 UPDATE）
            # C-03 IDOR 修复：RLS 已处理租户隔离
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.id == version.component_id,
                    Component.department_id.in_(visible_ids),
                )
            )
            if component is not None:
                component.active_version_id = version.id
                component.status = "published"
                component.updated_at = now
                component.lock_version += 1
            await session.flush()
            return version

    async def delete_component(self, component_id: UUID) -> None:
        """彻底删除组件及其所有版本。

        Args:
            component_id: 组件主记录 UUID。

        Raises:
            AppError: code="not_found"，当组件不存在或不属于当前部门时。
        """
        async with session_scope(self._factory) as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            # C-03 IDOR 修复：先校验组件属于当前部门
            component: Component | None = await session.scalar(
                sa.select(Component).where(
                    Component.id == component_id,
                    Component.department_id.in_(visible_ids),
                )
            )
            if component is None:
                raise AppError(
                    code="not_found",
                    message=f"组件不存在: {component_id}",
                    retryable=False,
                    fields={"component_id": str(component_id)},
                )
            # 先清除 active_version_id 外键引用，再删版本，避免 FK 约束冲突
            await session.execute(
                sa.update(Component)
                .where(Component.id == component_id)
                .where(Component.active_version_id.isnot(None))
                .values(active_version_id=None)
            )
            # 删除所有版本（component_version 是不可变表，需通过 GUC 开关临时允许 DELETE）
            await session.execute(sa.text("SET LOCAL app.allow_immutable_delete = 'on'"))
            await session.execute(
                sa.delete(ComponentVersion).where(ComponentVersion.component_id == component_id)
            )
            await session.execute(sa.text("SET LOCAL app.allow_immutable_delete = 'off'"))
            # 删除主记录
            await session.execute(
                sa.delete(Component).where(
                    Component.id == component_id,
                    Component.department_id.in_(visible_ids),
                )
            )
            await session.flush()
