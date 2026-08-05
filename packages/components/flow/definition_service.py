"""流程定义管理服务。

从 ``flow_runtime.py`` 提取的流程定义 CRUD + 版本发布逻辑。
职责：创建定义、发布版本、列表查询、获取详情、归档/恢复、删除。

依赖注入：
- 继承 ScopedSessionMixin，通过 ``_scoped_session()`` 获取带 GUC 的会话；
- 需要实例属性 ``_factory``, ``_dept_id``, ``_actor_id``, ``_registry``, ``_clock``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.components.flow.entities import (
    FlowDefinition,
    FlowDefinitionVersionORM,
    FlowNodeExecution,
    FlowRun,
)
from packages.components.flow.flow_validation import (
    FlowValidationService,
    ValidationResult,
)
from packages.components.flow.flows import (
    FlowEdge,
    FlowNode,
    compute_flow_digest,
    edges_to_json,
    nodes_to_json,
)
from packages.components.flow.manifest_utils import build_manifest_from_version
from packages.components.manifest import ComponentManifest
from packages.components.registry import (
    ComponentRegistryService,
    ComponentVersion,
)


class FlowDefinitionService(ScopedSessionMixin):
    """流程定义管理服务。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作者用户 ID。
        _registry: 组件注册表服务。
        _clock: 时钟实例。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID,
        registry: ComponentRegistryService,
        clock: Clock,
    ) -> None:
        """初始化流程定义服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作者用户 ID。
            registry: 组件注册表服务。
            clock: 时钟实例。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._registry = registry
        self._clock = clock

    async def create_definition(
        self,
        code: str,
        display_name: str,
        nodes: tuple[FlowNode, ...] = (),
        edges: tuple[FlowEdge, ...] = (),
        department_id: UUID | None = None,
        project_id: UUID | None = None,
        operator: str | None = None,
        experimental_object_code: str | None = None,
    ) -> FlowDefinition:
        """创建流程定义（含 DAG 校验）。

        创建后处于 draft 状态。若提供 nodes/edges，则先进行 DAG 校验。

        Args:
            code: 流程编码（组织内唯一）。
            display_name: 显示名称。
            nodes: 节点元组（可选，用于创建时 DAG 校验）。
            edges: 边元组（可选，用于创建时 DAG 校验）。
            department_id: 执行实验部门 ID（可选，默认当前部门）。
            project_id: 所属实验项目 ID（可选）。
            operator: 执行人（可选）。
            experimental_object_code: 关联实验对象编码（可选）。

        Returns:
            FlowDefinition: 新创建的流程定义。

        Raises:
            AppError: code="conflict"，当编码已存在。
            AppError: code="validation_failed"，当 DAG 校验失败。
        """
        # DAG 校验
        if nodes:
            dag_result: ValidationResult = FlowValidationService.validate_dag(nodes, edges)
            if not dag_result.valid:
                raise AppError(
                    code="validation_failed",
                    message="DAG 校验失败: " + "; ".join(dag_result.errors),
                    retryable=False,
                    fields={"errors": list(dag_result.errors)},
                )

        async with self._scoped_session() as session:
            existing: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.code == code,
                )
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message=f"流程编码已存在: {code}",
                    retryable=False,
                    fields={"code": code},
                )

            definition = FlowDefinition(
                code=code,
                display_name=display_name,
                department_id=department_id or self._dept_id,
                owner_user_id=self._actor_id,
                visibility_scope="tree",
                project_id=project_id,
                operator=operator,
                experimental_object_code=experimental_object_code,
                status="draft",
            )
            session.add(definition)
            await session.flush()
            return definition

    async def publish_version(
        self,
        flow_definition_id: UUID,
        nodes: tuple[FlowNode, ...],
        edges: tuple[FlowEdge, ...],
        random_seed: int = 0,
    ) -> FlowDefinitionVersionORM:
        """发布流程版本（不可变）。

        流程：
        1. 加载流程定义；
        2. DAG 校验（validate_dag）；
        3. 端口类型校验（check_port_types）；
        4. 参数 schema 校验（check_param_schema，逐节点）；
        5. 计算版本号（max+1）与摘要；
        6. 创建 FlowDefinitionVersionORM；
        7. 更新 FlowDefinition 状态为 published。

        Args:
            flow_definition_id: 流程定义 ID。
            nodes: 节点元组。
            edges: 边元组。
            random_seed: 随机种子。

        Returns:
            FlowDefinitionVersionORM: 新发布的版本。

        Raises:
            AppError: code="not_found"，当定义不存在。
            AppError: code="validation_failed"，当校验失败。
        """
        # 1. DAG 校验
        dag_result: ValidationResult = FlowValidationService.validate_dag(nodes, edges)
        if not dag_result.valid:
            raise AppError(
                code="validation_failed",
                message="DAG 校验失败: " + "; ".join(dag_result.errors),
                retryable=False,
                fields={"errors": list(dag_result.errors)},
            )

        # 2. 端口类型校验
        port_result: ValidationResult = await FlowValidationService.check_port_types(
            nodes, edges, self._registry
        )
        if not port_result.valid:
            raise AppError(
                code="validation_failed",
                message="端口类型校验失败: " + "; ".join(port_result.errors),
                retryable=False,
                fields={"errors": list(port_result.errors)},
            )

        # 3. 参数 schema 校验（逐节点）
        for node in nodes:
            try:
                version_row: ComponentVersion = await self._registry.get(
                    node.component_name, node.component_version
                )
            except AppError as exc:
                raise AppError(
                    code="validation_failed",
                    message=(
                        f"节点 {node.node_id} 引用的组件不存在: "
                        f"{node.component_name}@"
                        f"{node.component_version}"
                    ),
                    retryable=False,
                    fields={
                        "node_id": node.node_id,
                        "component": node.component_name,
                    },
                ) from exc

            manifest: ComponentManifest = build_manifest_from_version(version_row)
            param_result: ValidationResult = FlowValidationService.check_param_schema(
                node, manifest
            )
            if not param_result.valid:
                raise AppError(
                    code="validation_failed",
                    message=f"节点 {node.node_id} 参数校验失败: " + "; ".join(param_result.errors),
                    retryable=False,
                    fields={
                        "node_id": node.node_id,
                        "errors": list(param_result.errors),
                    },
                )

        # 4. 计算摘要
        digest: str = compute_flow_digest(nodes, edges, random_seed)
        now: datetime = self._clock.now()

        async with self._scoped_session() as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.id == flow_definition_id,
                )
            )
            if definition is None:
                raise AppError(
                    code="not_found",
                    message=f"流程定义不存在: {flow_definition_id}",
                    retryable=False,
                    fields={"flow_definition_id": str(flow_definition_id)},
                )

            # 计算版本号
            max_version: int | None = await session.scalar(
                sa.select(sa.func.max(FlowDefinitionVersionORM.version)).where(
                    FlowDefinitionVersionORM.flow_definition_id == flow_definition_id
                )
            )
            next_version: int = (max_version or 0) + 1

            version = FlowDefinitionVersionORM(
                flow_definition_id=flow_definition_id,
                version=next_version,
                nodes_json=nodes_to_json(nodes),
                edges_json=edges_to_json(edges),
                random_seed=random_seed,
                digest=digest,
                status="published",
                published_at=now,
            )
            session.add(version)
            await session.flush()

            definition.status = "published"
            definition.updated_at = now
            definition.lock_version += 1
            await session.flush()

            return version

    async def list_definitions(
        self,
        status: str | None = None,
        project_id: str | None = None,
    ) -> list[tuple[FlowDefinition, FlowDefinitionVersionORM | None]]:
        """列表查询流程定义及其最新版本。

        Args:
            status: 可选，按状态过滤（draft/published/deprecated）。
            project_id: 可选，按所属项目 ID 过滤（UUID 字符串）。

        Returns:
            list[tuple[FlowDefinition, FlowDefinitionVersionORM | None]]:
                定义 + 最新版本（无版本时为 None），按 code 排序。
        """
        async with self._scoped_session() as session:
            query = sa.select(FlowDefinition)
            if status is not None:
                query = query.where(FlowDefinition.status == status)
            if project_id is not None:
                query = query.where(FlowDefinition.project_id == UUID(project_id))
            query = query.order_by(FlowDefinition.code)

            definitions: list[FlowDefinition] = list((await session.execute(query)).scalars().all())

            result: list[tuple[FlowDefinition, FlowDefinitionVersionORM | None]] = []
            for definition in definitions:
                latest: FlowDefinitionVersionORM | None = await session.scalar(
                    sa.select(FlowDefinitionVersionORM)
                    .where(FlowDefinitionVersionORM.flow_definition_id == definition.id)
                    .order_by(FlowDefinitionVersionORM.version.desc())
                    .limit(1)
                )
                result.append((definition, latest))
            return result

    async def get_definition(
        self, flow_id: UUID
    ) -> tuple[FlowDefinition, FlowDefinitionVersionORM | None]:
        """获取流程定义详情（含最新版本）。

        Args:
            flow_id: 流程定义 ID。

        Returns:
            tuple[FlowDefinition, FlowDefinitionVersionORM | None]:
                定义 + 最新版本。

        Raises:
            AppError: code="not_found"，当定义不存在。
        """
        async with self._scoped_session() as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.id == flow_id,
                )
            )
            if definition is None:
                raise AppError(
                    code="not_found",
                    message=f"流程定义不存在: {flow_id}",
                    retryable=False,
                    fields={"flow_id": str(flow_id)},
                )

            latest: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM)
                .where(FlowDefinitionVersionORM.flow_definition_id == definition.id)
                .order_by(FlowDefinitionVersionORM.version.desc())
                .limit(1)
            )
            return definition, latest

    async def deprecate_definition(self, flow_id: UUID) -> FlowDefinition:
        """将流程定义标记为已归档（deprecated）。

        Args:
            flow_id: 流程定义 ID。

        Returns:
            FlowDefinition: 更新后的定义。

        Raises:
            AppError: code="not_found"，当定义不存在。
        """
        now: datetime = self._clock.now()
        async with self._scoped_session() as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.id == flow_id,
                )
            )
            if definition is None:
                raise AppError(
                    code="not_found",
                    message=f"流程定义不存在: {flow_id}",
                    retryable=False,
                    fields={"flow_id": str(flow_id)},
                )
            definition.status = "deprecated"
            definition.updated_at = now
            definition.lock_version += 1
            await session.flush()
            return definition

    async def restore_definition(self, flow_id: UUID) -> FlowDefinition:
        """从归档恢复流程定义（deprecated → published）。

        Args:
            flow_id: 流程定义 ID。

        Returns:
            FlowDefinition: 更新后的定义。

        Raises:
            AppError: code="not_found"，当定义不存在。
        """
        now: datetime = self._clock.now()
        async with self._scoped_session() as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.id == flow_id,
                )
            )
            if definition is None:
                raise AppError(
                    code="not_found",
                    message=f"流程定义不存在: {flow_id}",
                    retryable=False,
                    fields={"flow_id": str(flow_id)},
                )
            definition.status = "published"
            definition.updated_at = now
            definition.lock_version += 1
            await session.flush()
            return definition

    async def get_definition_by_id(
        self, version_id: UUID
    ) -> tuple[FlowDefinition, FlowDefinitionVersionORM]:
        """按版本 ID 获取流程定义 + 版本。

        Args:
            version_id: 流程版本 ID。

        Returns:
            tuple[FlowDefinition, FlowDefinitionVersionORM]:
                定义 + 版本。

        Raises:
            AppError: code="not_found"，当版本不存在。
        """
        async with self._scoped_session() as session:
            row = (
                await session.execute(
                    sa.select(FlowDefinition, FlowDefinitionVersionORM)
                    .join(
                        FlowDefinitionVersionORM,
                        FlowDefinitionVersionORM.flow_definition_id == FlowDefinition.id,
                    )
                    .where(
                        FlowDefinitionVersionORM.id == version_id,
                    )
                )
            ).first()
            if row is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {version_id}",
                    retryable=False,
                    fields={"version_id": str(version_id)},
                )
            return row[0], row[1]

    async def delete_flow(self, flow_id: UUID) -> None:
        """删除流程定义及其所有版本和运行记录。

        删除顺序（手动级联，避免依赖数据库 FK CASCADE）：
        1. 查询该流程定义的所有版本 ID；
        2. 删除这些版本关联的所有运行记录的节点执行记录；
        3. 删除运行记录；
        4. 删除流程版本；
        5. 删除流程定义本身。

        Args:
            flow_id: 流程定义 ID。
        """
        async with self._scoped_session() as session:
            # 1. 查询该流程定义的所有版本 ID
            version_ids_result = await session.execute(
                sa.select(FlowDefinitionVersionORM.id).where(
                    FlowDefinitionVersionORM.flow_definition_id == flow_id
                )
            )
            version_ids: list[UUID] = [row[0] for row in version_ids_result.all()]

            if version_ids:
                # 2. 删除这些版本关联的所有运行记录的节点执行记录
                run_ids_result = await session.execute(
                    sa.select(FlowRun.id).where(FlowRun.flow_version_id.in_(version_ids))
                )
                run_ids: list[UUID] = [row[0] for row in run_ids_result.all()]

                if run_ids:
                    await session.execute(
                        sa.delete(FlowNodeExecution).where(
                            FlowNodeExecution.flow_run_id.in_(run_ids)
                        )
                    )

                    # 3. 删除运行记录
                    await session.execute(
                        sa.delete(FlowRun).where(FlowRun.flow_version_id.in_(version_ids))
                    )

                # 4. 删除流程版本（flow_definition_version 是不可变表，需 GUC 开关）
                await session.execute(sa.text("SET LOCAL app.allow_immutable_delete = 'on'"))
                await session.execute(
                    sa.delete(FlowDefinitionVersionORM).where(
                        FlowDefinitionVersionORM.flow_definition_id == flow_id
                    )
                )
                await session.execute(sa.text("SET LOCAL app.allow_immutable_delete = 'off'"))

            # 5. 删除流程定义本身
            await session.execute(
                sa.delete(FlowDefinition).where(
                    FlowDefinition.id == flow_id,
                )
            )
            await session.flush()

    async def update_definition(
        self,
        flow_id: UUID,
        display_name: str,
        department_id: str | None = None,
        project_id: str | None = None,
        operator: str | None = None,
        experimental_object_code: str | None = None,
    ) -> FlowDefinition:
        """更新流程定义（display_name + 可选字段）。

        可选更新 department_id/project_id/operator/experimental_object_code。

        Args:
            flow_id: 流程定义 ID。
            display_name: 新显示名称。
            department_id: 新部门 ID（字符串 UUID，空串清空为 None）。
            project_id: 新项目 ID（字符串 UUID，空串清空为 None）。
            operator: 新执行人（None 不修改）。
            experimental_object_code: 新实验对象编码（空串清空为 None）。

        Returns:
            FlowDefinition: 更新后的定义。

        Raises:
            AppError: code="not_found"，当定义不存在。
        """
        async with self._scoped_session() as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.id == flow_id,
                )
            )
            if definition is None:
                raise AppError(
                    code="not_found",
                    message=f"流程定义不存在: {flow_id}",
                    retryable=False,
                    fields={"flow_id": str(flow_id)},
                )

            definition.display_name = display_name
            if department_id is not None:
                definition.department_id = UUID(department_id) if department_id else None
            if project_id is not None:
                definition.project_id = UUID(project_id) if project_id else None
            if operator is not None:
                definition.operator = operator
            if experimental_object_code is not None:
                definition.experimental_object_code = experimental_object_code or None
            definition.updated_at = datetime.now(UTC)
            await session.flush()
            return definition
