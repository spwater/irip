"""IRIP 流程运行时：ORM 实体 + 定义管理 + 执行服务。

提供：
- FlowDefinition: 流程定义主表 ORM（组织内按 code 唯一）；
- FlowDefinitionVersionORM: 流程版本表 ORM（按 definition+version 唯一，
  已发布不可变）；
- FlowRun: 流程执行记录 ORM（关联 job + 输入快照 + 输出摘要）；
- FlowNodeExecution: 节点执行记录 ORM（逐节点状态 + 输入/输出摘要）；
- FlowRuntimeService: 流程定义管理 + 执行编排服务。

设计要点（IRIP V2-T03）：
- 流程定义创建后处于 draft 状态，发布版本后变为 published；
- 已发布版本不可变（无更新端点），digest 校验完整性；
- 执行采用拓扑排序逐节点执行，记录每个节点的执行状态；
- 支持 resume（跳过已成功节点）、cancel（协作式取消）、retry_node
  （重试单个失败节点）；
- cancel 通过 asyncio.Event 实现协作式取消，执行循环在节点间检查。
"""

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
import yaml
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.clock import Clock, SystemClock
from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.components.flow_validation import (
    FlowValidationService,
    ValidationResult,
)
from packages.components.flows import (
    FlowEdge,
    FlowNode,
    compute_flow_digest,
    edges_from_json,
    edges_to_json,
    nodes_from_json,
    nodes_to_json,
)
from packages.components.manifest import ComponentManifest
from packages.components.registry import (
    ComponentRegistryService,
    ComponentVersion,
)
from packages.components.sdk import ComponentContext, ComponentResult, ComponentRunner

#: 受保护参数白名单：外部运行 inputs 禁止覆盖这些文件路径类参数（F-13 安全约束）。
#: 防止通过流程 inputs 注入任意文件路径，绕过节点参数的安全校验。
PROTECTED_PARAMS: frozenset[str] = frozenset(
    {
        "path",
        "file_path",
        "input_path",
        "output_path",
        "file",
        "filename",
        "source_path",
        "dest_path",
        "input_file",
        "output_file",
        "data_path",
        "template_path",
        "config_path",
        "script_path",
        "executable_path",
    }
)


# ---- ORM 实体 ----


class FlowDefinition(Base):
    """流程定义主表 ORM 模型（对应 flow_definition 表）。

    组织内按 code 唯一，一个定义可包含多个版本。

    Attributes:
        id: 流程定义 UUID。
        organization_id: 所属组织 ID。
        code: 流程编码（组织内唯一）。
        display_name: 显示名称。
        status: 生命周期状态（draft/published/deprecated）。
        lock_version: 乐观锁版本号。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC）。
    """

    __tablename__ = "flow_definition"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(
        GUID,
        sa.ForeignKey("department.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    operator: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    experimental_object_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
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
        return f"FlowDefinition(code={self.code!r}, status={self.status!r})"


class FlowDefinitionVersionORM(Base):
    """流程版本表 ORM 模型（对应 flow_definition_version 表）。

    每次发布创建一行，已发布版本不可变。

    Attributes:
        id: 版本 UUID。
        flow_definition_id: 所属流程定义 ID（FK）。
        version: 版本号（从 1 递增）。
        nodes_json: 节点列表（JSONB）。
        edges_json: 边列表（JSONB）。
        random_seed: 随机种子。
        digest: 内容摘要（SHA-256）。
        status: 版本状态（默认 published）。
        published_at: 发布时间（UTC）。
        created_at: 创建时间（UTC）。
    """

    __tablename__ = "flow_definition_version"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    flow_definition_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "flow_definition.id",
            name="fk_flow_version_definition_id",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    nodes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    edges_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    random_seed: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    digest: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="published",
        server_default=sa.text("'published'"),
    )
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"FlowDefinitionVersionORM(version={self.version!r}, status={self.status!r})"


class FlowRun(Base):
    """流程执行记录 ORM 模型（对应 flow_run 表）。

    Attributes:
        id: 执行记录 UUID。
        organization_id: 所属组织 ID。
        flow_version_id: 流程版本 ID（FK）。
        status: 执行状态（pending/running/succeeded/failed/cancelled）。
        job_id: 关联作业 ID（FK→job.id）。
        input_snapshot: 输入快照（JSONB）。
        output_digest: 输出摘要（SHA-256）。
        started_at: 开始执行时间。
        completed_at: 完成时间。
        created_at: 创建时间。
    """

    __tablename__ = "flow_run"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    flow_version_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "flow_definition_version.id",
            name="fk_flow_run_version_id",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="pending",
        server_default=sa.text("'pending'"),
    )
    job_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_digest: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=sa.func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"FlowRun(status={self.status!r}, flow_version_id={self.flow_version_id!r})"


class FlowNodeExecution(Base):
    """节点执行记录 ORM 模型（对应 flow_node_execution 表）。

    Attributes:
        id: 执行记录 UUID。
        flow_run_id: 所属流程执行 ID（FK→flow_run.id CASCADE）。
        node_id: 节点 ID（流程内标识）。
        status: 执行状态（pending/running/succeeded/failed）。
        input_summary: 输入摘要（JSONB）。
        output_summary: 输出摘要（JSONB）。
        diagnostics: 诊断信息（JSONB）。
        started_at: 开始时间。
        completed_at: 完成时间。
        duration_ms: 执行耗时（毫秒）。
    """

    __tablename__ = "flow_node_execution"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    flow_run_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey(
            "flow_run.id",
            name="fk_flow_node_execution_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="pending",
        server_default=sa.text("'pending'"),
    )
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    def __repr__(self) -> str:
        return f"FlowNodeExecution(node_id={self.node_id!r}, status={self.status!r})"


# ---- 辅助函数 ----


def _build_manifest_from_version(
    version_row: ComponentVersion,
) -> ComponentManifest:
    """从 ComponentVersion ORM 构建 ComponentManifest（解析 manifest_yaml）。

    ComponentVersion 存储了 manifest_yaml，需要解析为 ComponentManifest
    值对象供 runner 使用。manifest 已在发布时校验过，此处不再校验。

    Args:
        version_row: 组件版本 ORM 记录。

    Returns:
        ComponentManifest: 组件清单值对象。

    Raises:
        AppError: code="invalid_manifest"，当 YAML 解析失败。
    """
    from packages.components.manifest import _parse_port_specs

    try:
        raw: Any = yaml.safe_load(version_row.manifest_yaml)
    except yaml.YAMLError as exc:
        raise AppError(
            code="invalid_manifest",
            message=f"清单 YAML 解析失败: {exc}",
            retryable=False,
            fields={},
        ) from exc

    if not isinstance(raw, dict):
        raise AppError(
            code="invalid_manifest",
            message="清单根节点必须为对象（mapping）",
            retryable=False,
            fields={},
        )

    dependencies_raw: list[str] | None = raw.get("dependencies")
    dependencies: tuple[str, ...] = tuple(dependencies_raw) if dependencies_raw else ()

    return ComponentManifest(
        name=raw["name"],
        display_name=raw.get("display_name", ""),
        version=raw.get("version", "auto"),
        kind=raw["kind"],
        runtime=raw.get("runtime", "python"),
        inputs=_parse_port_specs(raw.get("inputs")),
        outputs=_parse_port_specs(raw.get("outputs")),
        parameters=raw.get("parameters", {}) or {},
        dependencies=dependencies,
        raw_yaml=version_row.manifest_yaml,
        sha256=version_row.manifest_sha256,
    )


def _topological_sort(
    nodes: tuple[FlowNode, ...],
    edges: tuple[FlowEdge, ...],
) -> list[str]:
    """Kahn 算法拓扑排序，返回节点 ID 的执行顺序。

    Args:
        nodes: 节点元组。
        edges: 边元组。

    Returns:
        list[str]: 拓扑排序后的节点 ID 列表。

    Raises:
        AppError: code="validation_failed"，当存在环。
    """
    in_degree: dict[str, int] = {n.node_id: 0 for n in nodes}
    adjacency: dict[str, list[str]] = {n.node_id: [] for n in nodes}

    for edge in edges:
        adjacency[edge.source_node].append(edge.target_node)
        in_degree[edge.target_node] += 1

    queue: list[str] = [nid for nid, deg in in_degree.items() if deg == 0]
    order: list[str] = []

    while queue:
        current: str = queue.pop(0)
        order.append(current)
        for neighbor in adjacency[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(nodes):
        raise AppError(
            code="validation_failed",
            message="流程存在环，无法拓扑排序",
            retryable=False,
            fields={},
        )

    return order


def _resolve_input(
    binding: str,
    node_outputs: dict[str, dict[str, Any]],
    input_snapshot: dict[str, Any],
) -> Any:
    """解析输入绑定，从上游节点输出或外部输入获取数据。

    绑定格式：
    - ``"<source_node_id>:<source_port>"`` → 上游节点输出；
    - ``"<external_input_name>"`` → 流程外部输入。

    Args:
        binding: 绑定字符串。
        node_outputs: 已执行节点的输出映射。
        input_snapshot: 流程外部输入快照。

    Returns:
        Any: 解析后的输入数据（找不到时为 None）。
    """
    if ":" in binding:
        parts: list[str] = binding.split(":", 1)
        src_node: str = parts[0]
        src_port: str = parts[1]
        return node_outputs.get(src_node, {}).get(src_port)
    return input_snapshot.get(binding)


def _compute_output_digest(
    version_digest: str,
    input_snapshot: dict[str, Any],
    node_executions: list[dict[str, Any]],
) -> str:
    """计算流程执行的输出摘要。

    Args:
        version_digest: 流程版本摘要。
        input_snapshot: 输入快照。
        node_executions: 节点执行摘要列表。

    Returns:
        str: SHA-256 摘要（hex 小写）。
    """
    payload: dict[str, Any] = {
        "version_digest": version_digest,
        "input_snapshot": input_snapshot,
        "node_summaries": node_executions,
    }
    canonical: str = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_output_summary(
    result: ComponentResult,
) -> dict[str, Any]:
    """将 ComponentResult 输出序列化为 JSON 兼容摘要。

    仅保留输出端口的键与可序列化值，非可序列化值转为字符串。

    Args:
        result: 组件执行结果。

    Returns:
        dict[str, Any]: JSON 兼容摘要。
    """
    summary: dict[str, Any] = {}
    for key, value in result.outputs.items():
        try:
            summary[key] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            summary[key] = str(value)
    summary["_metadata"] = result.metadata
    summary["_summary_text"] = result.summary
    return summary


def _serialize_input_summary(
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """将节点输入序列化为 JSON 兼容摘要。

    Args:
        inputs: 节点输入字典。

    Returns:
        dict[str, Any]: JSON 兼容摘要。
    """
    summary: dict[str, Any] = {}
    for key, value in inputs.items():
        try:
            summary[key] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            summary[key] = str(value)
    return summary


# ---- 运行时服务 ----


class FlowRuntimeService:
    """流程运行时服务：定义管理 + 执行编排。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    registry（组件注册表）、runner（组件运行器）、job_service（作业服务）。

    核心操作：
    - 定义管理：create_definition, publish_version, list_definitions,
      get_definition, get_definition_by_id；
    - 执行管理：create_run, execute, resume, cancel, retry_node, get_run。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _registry: 组件注册表服务。
        _runner: 组件运行器。
        _job_service: 作业服务（创建异步作业）。
        _clock: 时钟实例。
        _artifact_service: 工件服务（可选，用于组件上传/下载）。
        _cancel_events: 运行 ID → 取消事件映射（内存中维护）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        registry: ComponentRegistryService,
        runner: ComponentRunner,
        job_service: Any,
        clock: Clock | None = None,
        artifact_service: Any = None,
        ai_config_provider: Any = None,
    ) -> None:
        """初始化流程运行时服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            registry: 组件注册表服务。
            runner: 组件运行器（PythonComponentRunner 或 CLIComponentRunner）。
            job_service: 作业服务（创建异步作业触发执行）。
            clock: 时钟（可选，默认 SystemClock）。
            artifact_service: 工件服务（可选，注入到 ComponentContext）。
            ai_config_provider: AI 配置异步提供函数（可选，注入到 ComponentContext，
                消除 packages→apps 反向依赖 T3-3）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._registry = registry
        self._runner = runner
        self._job_service = job_service
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._artifact_service = artifact_service
        self._ai_config_provider: Any = ai_config_provider
        self._cancel_events: dict[UUID, asyncio.Event] = {}

    # ---- 公开只读属性（替代路由直接访问私有属性） ----

    @property
    def organization_id(self) -> UUID:
        """当前组织 ID（公开只读访问，替代 ``service._org_id``）。"""
        return self._org_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂（公开只读访问，替代 ``service._factory``）。"""
        return self._factory

    # ---- 定义管理 ----

    async def create_definition(
        self,
        code: str,
        display_name: str,
        nodes: tuple[FlowNode, ...] = (),
        edges: tuple[FlowEdge, ...] = (),
        department_id: UUID | None = None,
        project_name: str | None = None,
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

        async with session_scope(self._factory) as session:
            existing: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.organization_id == self._org_id,
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
                organization_id=self._org_id,
                code=code,
                display_name=display_name,
                department_id=department_id,
                project_name=project_name,
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

            manifest: ComponentManifest = _build_manifest_from_version(version_row)
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

        async with session_scope(self._factory) as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.organization_id == self._org_id,
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
    ) -> list[tuple[FlowDefinition, FlowDefinitionVersionORM | None]]:
        """列表查询流程定义及其最新版本。

        Args:
            status: 可选，按状态过滤（draft/published/deprecated）。

        Returns:
            list[tuple[FlowDefinition, FlowDefinitionVersionORM | None]]:
                定义 + 最新版本（无版本时为 None），按 code 排序。
        """
        async with session_scope(self._factory) as session:
            query = sa.select(FlowDefinition).where(FlowDefinition.organization_id == self._org_id)
            if status is not None:
                query = query.where(FlowDefinition.status == status)
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
        async with session_scope(self._factory) as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.organization_id == self._org_id,
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
        async with session_scope(self._factory) as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.organization_id == self._org_id,
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
        async with session_scope(self._factory) as session:
            definition: FlowDefinition | None = await session.scalar(
                sa.select(FlowDefinition).where(
                    FlowDefinition.organization_id == self._org_id,
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
        async with session_scope(self._factory) as session:
            row = (
                await session.execute(
                    sa.select(FlowDefinition, FlowDefinitionVersionORM)
                    .join(
                        FlowDefinitionVersionORM,
                        FlowDefinitionVersionORM.flow_definition_id == FlowDefinition.id,
                    )
                    .where(
                        FlowDefinition.organization_id == self._org_id,
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

    # ---- 执行管理 ----

    async def list_runs(self, flow_id: UUID) -> list[FlowRun]:
        """列出流程的所有运行记录（按创建时间降序）。

        Args:
            flow_id: 流程定义 ID。

        Returns:
            list[FlowRun]: 运行记录列表。
        """
        async with session_scope(self._factory) as session:
            result = await session.execute(
                sa.select(FlowRun)
                .where(
                    FlowRun.flow_version_id.in_(
                        sa.select(FlowDefinitionVersionORM.id).where(
                            FlowDefinitionVersionORM.flow_definition_id == flow_id
                        )
                    )
                )
                .order_by(FlowRun.created_at.desc())
            )
            return list(result.scalars().all())

    async def create_run(
        self,
        flow_version_id: UUID,
        inputs: dict[str, Any] | None = None,
    ) -> FlowRun:
        """创建流程执行记录（关联作业）。

        流程：
        1. 验证流程版本存在；
        2. 通过 job_service 创建异步作业；
        3. 创建 FlowRun（status=pending, input_snapshot=inputs）。

        Args:
            flow_version_id: 流程版本 ID。
            inputs: 流程输入（存储为 input_snapshot）。

        Returns:
            FlowRun: 新创建的执行记录（status=pending）。

        Raises:
            AppError: code="not_found"，当版本不存在。
        """
        # 验证版本存在
        await self.get_definition_by_id(flow_version_id)

        run_id: UUID = new_id()
        input_snapshot: dict[str, Any] = inputs or {}

        # 创建作业
        job_ref: Any = await self._job_service.accept(
            kind="flow_execute",
            payload={
                "run_id": str(run_id),
                "flow_version_id": str(flow_version_id),
                "organization_id": str(self._org_id),
            },
            idempotency_key=f"flow-run-{run_id}",
        )

        async with session_scope(self._factory) as session:
            run = FlowRun(
                id=run_id,
                organization_id=self._org_id,
                flow_version_id=flow_version_id,
                status="pending",
                job_id=job_ref.job_id,
                input_snapshot=input_snapshot,
            )
            session.add(run)
            await session.flush()

            # F-04 §8.5：不再直接 send_task，统一走 Outbox→Dispatcher→Celery 链路
            # job_service.accept() 已在同事务中 INSERT outbox_event，
            # OutboxDispatcher 会定期拉取并通过 celery_app.send_task 发送。

            return run

    async def execute(self, run_id: UUID) -> None:
        """执行流程：拓扑排序 → 逐节点执行 → 记录 → 计算摘要。

        流程：
        1. 加载 FlowRun 及其 FlowDefinitionVersionORM；
        2. 解析节点和边，拓扑排序；
        3. 创建 cancel_event；
        4. 更新 FlowRun 状态为 running；
        5. 逐节点执行（获取 manifest → 绑定输入 → 运行 → 记录）；
        6. 节点间检查 cancel_event；
        7. 计算输出摘要；
        8. 更新 FlowRun 最终状态。

        Args:
            run_id: 执行记录 ID。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        # 1. 加载执行记录与版本
        async with session_scope(self._factory) as session:
            run: FlowRun | None = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"流程执行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            version: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == run.flow_version_id
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {run.flow_version_id}",
                    retryable=False,
                    fields={"flow_version_id": str(run.flow_version_id)},
                )

            nodes: tuple[FlowNode, ...] = nodes_from_json(version.nodes_json)
            edges: tuple[FlowEdge, ...] = edges_from_json(version.edges_json)
            input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
            job_id: UUID | None = run.job_id
            version_digest: str = version.digest

            # 更新状态为 running
            run.status = "running"
            run.started_at = self._clock.now()
            await session.flush()

        # 2. 创建取消事件
        cancel_event: asyncio.Event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event

        # 3. 拓扑排序
        order: list[str] = _topological_sort(nodes, edges)
        node_map: dict[str, FlowNode] = {n.node_id: n for n in nodes}

        # 4. 逐节点执行
        node_outputs: dict[str, dict[str, Any]] = {}
        node_exec_summaries: list[dict[str, Any]] = []

        for node_id in order:
            # 检查取消信号
            if cancel_event.is_set():
                await self._finalize_run(run_id, "cancelled", version_digest, node_exec_summaries)
                await self._update_job_status(job_id, "cancelled")
                return

            node: FlowNode = node_map[node_id]

            # 解析输入
            inputs: dict[str, Any] = {}
            for port_name, binding in node.input_bindings.items():
                inputs[port_name] = _resolve_input(binding, node_outputs, input_snapshot)
            # 合并外部输入（input_snapshot 里的参数覆盖节点默认值）
            # 安全约束（F-13）：禁止 inputs 覆盖文件路径类受保护参数
            # 但允许 artifact: 前缀的值通过（用户上传文件的合法引用）
            for key, val in input_snapshot.items():
                if not val:
                    continue
                if key in PROTECTED_PARAMS:
                    if isinstance(val, str) and val.startswith("artifact:"):
                        inputs[key] = val
                    continue
                if key not in inputs:
                    inputs[key] = val

            # 执行节点
            exec_result: dict[str, Any] = await self._execute_single_node(
                run_id, node, inputs, cancel_event, job_id
            )

            node_outputs[node_id] = exec_result.get("outputs", {})
            node_exec_summaries.append(
                {
                    "node_id": node_id,
                    "status": exec_result["status"],
                    "output_summary": exec_result.get("output_summary", {}),
                }
            )

            if exec_result["status"] != "succeeded":
                # 节点失败，终止执行
                await self._finalize_run(run_id, "failed", version_digest, node_exec_summaries)
                await self._update_job_status(job_id, "failed")
                return

        # 5. 全部成功
        await self._finalize_run(run_id, "succeeded", version_digest, node_exec_summaries)
        await self._update_job_status(job_id, "succeeded")

    async def _execute_single_node(
        self,
        run_id: UUID,
        node: FlowNode,
        inputs: dict[str, Any],
        cancel_event: asyncio.Event,
        job_id: UUID | None,
    ) -> dict[str, Any]:
        """执行单个节点并记录 FlowNodeExecution。

        Args:
            run_id: 执行记录 ID。
            node: 流程节点。
            inputs: 已解析的节点输入。
            cancel_event: 取消事件。
            job_id: 关联作业 ID。

        Returns:
            dict[str, Any]: 执行结果，包含 status/outputs/output_summary。
        """
        now_start: datetime = self._clock.now()
        input_summary: dict[str, Any] = _serialize_input_summary(inputs)

        # 创建 FlowNodeExecution（pending）
        async with session_scope(self._factory) as session:
            execution = FlowNodeExecution(
                flow_run_id=run_id,
                node_id=node.node_id,
                status="pending",
                input_summary=input_summary,
            )
            session.add(execution)
            await session.flush()
            execution_id: UUID = execution.id

        # 更新为 running
        async with session_scope(self._factory) as session:
            await session.execute(
                sa.update(FlowNodeExecution)
                .values(
                    status="running",
                    started_at=now_start,
                )
                .where(FlowNodeExecution.id == execution_id)
            )

        try:
            # 获取组件 manifest（始终取最新发布版本，而非 flow 版本中记录的版本号）
            version_row: ComponentVersion = await self._registry.get_latest(node.component_name)
            manifest: ComponentManifest = _build_manifest_from_version(version_row)

            # 从组件 manifest 的参数中动态加载 prompt 和 tool_type，覆盖 flow 版本中的快照
            props = manifest.parameters.get("properties", {})
            manifest_prompt = props.get("prompt", {}).get("default")
            if manifest_prompt and "prompt" in node.params:
                node.params["prompt"] = manifest_prompt
            manifest_tool_type = props.get("tool_type", {}).get("default")
            if manifest_tool_type:
                node.params["tool_type"] = manifest_tool_type

            # 构建 ComponentContext
            context: ComponentContext = ComponentContext(
                organization_id=self._org_id,
                user_id=UUID(str(job_id)) if job_id else new_id(),
                clock=self._clock,
                artifact_service=self._artifact_service,
                job_id=job_id or new_id(),
                cancel_event=cancel_event,
                workdir=Path("/tmp/irip-flow"),
                ai_config_provider=self._ai_config_provider,
            )

            # 合并节点参数与解析后的输入（输入端口数据注入 params）
            merged_params: dict[str, Any] = {**node.params, **inputs}

            # 运行组件
            result: ComponentResult = await self._runner.run(manifest, context, merged_params)

            # 序列化输出摘要
            output_summary: dict[str, Any] = _serialize_output_summary(result)

            now_end: datetime = self._clock.now()
            duration_ms: int = int((now_end - now_start).total_seconds() * 1000)

            # 更新为 succeeded
            async with session_scope(self._factory) as session:
                await session.execute(
                    sa.update(FlowNodeExecution)
                    .values(
                        status="succeeded",
                        output_summary=output_summary,
                        diagnostics=result.diagnostics,
                        completed_at=now_end,
                        duration_ms=duration_ms,
                    )
                    .where(FlowNodeExecution.id == execution_id)
                )

            return {
                "status": "succeeded",
                "outputs": result.outputs,
                "output_summary": output_summary,
            }

        except Exception as exc:
            now_end: datetime = self._clock.now()
            duration_ms: int = int((now_end - now_start).total_seconds() * 1000)

            diagnostics: dict[str, Any] = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

            async with session_scope(self._factory) as session:
                await session.execute(
                    sa.update(FlowNodeExecution)
                    .values(
                        status="failed",
                        diagnostics=diagnostics,
                        completed_at=now_end,
                        duration_ms=duration_ms,
                    )
                    .where(FlowNodeExecution.id == execution_id)
                )

            return {
                "status": "failed",
                "outputs": {},
                "output_summary": {},
                "error": str(exc),
            }

    async def resume(self, run_id: UUID) -> None:
        """恢复执行：跳过已成功节点，重新执行失败/待执行节点。

        流程：
        1. 加载 FlowRun 及版本；
        2. 加载已存在的 FlowNodeExecution 记录；
        3. 跳过 status=succeeded 的节点（复用其输出）；
        4. 重新执行 status=failed/pending 的节点；
        5. 继续执行剩余未执行节点。

        Args:
            run_id: 执行记录 ID。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        async with session_scope(self._factory) as session:
            run: FlowRun | None = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"流程执行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            version: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == run.flow_version_id
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {run.flow_version_id}",
                    retryable=False,
                    fields={"flow_version_id": str(run.flow_version_id)},
                )

            nodes: tuple[FlowNode, ...] = nodes_from_json(version.nodes_json)
            edges: tuple[FlowEdge, ...] = edges_from_json(version.edges_json)
            input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
            job_id: UUID | None = run.job_id
            version_digest: str = version.digest

            # 加载已存在的节点执行记录
            existing_execs: list[FlowNodeExecution] = list(
                (
                    await session.execute(
                        sa.select(FlowNodeExecution)
                        .where(FlowNodeExecution.flow_run_id == run_id)
                        .order_by(FlowNodeExecution.started_at)
                    )
                )
                .scalars()
                .all()
            )

            # 更新状态为 running，重置 started_at（重新计时）
            run.status = "running"
            run.started_at = self._clock.now()
            await session.flush()

        # 拓扑排序
        order: list[str] = _topological_sort(nodes, edges)
        node_map: dict[str, FlowNode] = {n.node_id: n for n in nodes}

        # 构建已成功节点的输出映射
        node_outputs: dict[str, dict[str, Any]] = {}
        succeeded_nodes: set[str] = set()
        for exec_record in existing_execs:
            if exec_record.status == "succeeded":
                succeeded_nodes.add(exec_record.node_id)
                node_outputs[exec_record.node_id] = dict(exec_record.output_summary or {})

        # 创建取消事件
        cancel_event: asyncio.Event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event

        # 逐节点执行（跳过已成功的）
        node_exec_summaries: list[dict[str, Any]] = []
        for exec_record in existing_execs:
            node_exec_summaries.append(
                {
                    "node_id": exec_record.node_id,
                    "status": exec_record.status,
                    "output_summary": exec_record.output_summary or {},
                }
            )

        for node_id in order:
            if cancel_event.is_set():
                await self._finalize_run(
                    run_id,
                    "cancelled",
                    version_digest,
                    node_exec_summaries,
                )
                await self._update_job_status(job_id, "cancelled")
                return

            # 跳过已成功节点
            if node_id in succeeded_nodes:
                continue

            node: FlowNode = node_map[node_id]

            # 解析输入
            inputs: dict[str, Any] = {}
            for port_name, binding in node.input_bindings.items():
                inputs[port_name] = _resolve_input(binding, node_outputs, input_snapshot)
            # 合并外部输入（input_snapshot 里的参数覆盖节点默认值）
            # 安全约束（F-13）：禁止 inputs 覆盖文件路径类受保护参数
            # 但允许 artifact: 前缀的值通过（用户上传文件的合法引用）
            for key, val in input_snapshot.items():
                if not val:
                    continue
                if key in PROTECTED_PARAMS:
                    if isinstance(val, str) and val.startswith("artifact:"):
                        inputs[key] = val
                    continue
                if key not in inputs:
                    inputs[key] = val

            # 执行节点
            exec_result: dict[str, Any] = await self._execute_single_node(
                run_id, node, inputs, cancel_event, job_id
            )

            node_outputs[node_id] = exec_result.get("outputs", {})

            # 更新摘要
            found: bool = False
            for i, s in enumerate(node_exec_summaries):
                if s["node_id"] == node_id:
                    node_exec_summaries[i] = {
                        "node_id": node_id,
                        "status": exec_result["status"],
                        "output_summary": exec_result.get("output_summary", {}),
                    }
                    found = True
                    break
            if not found:
                node_exec_summaries.append(
                    {
                        "node_id": node_id,
                        "status": exec_result["status"],
                        "output_summary": exec_result.get("output_summary", {}),
                    }
                )

            if exec_result["status"] != "succeeded":
                await self._finalize_run(
                    run_id,
                    "failed",
                    version_digest,
                    node_exec_summaries,
                )
                await self._update_job_status(job_id, "failed")
                return

        # 全部成功
        await self._finalize_run(run_id, "succeeded", version_digest, node_exec_summaries)
        await self._update_job_status(job_id, "succeeded")

    async def cancel(self, run_id: UUID) -> FlowRun:
        """取消流程执行（设置 cancel_event）。

        协作式取消：设置 cancel_event 后，执行循环在下一个节点前退出。
        若执行不在进行中，直接更新数据库状态为 cancelled。

        Args:
            run_id: 执行记录 ID。

        Returns:
            FlowRun: 更新后的执行记录。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        # 设置取消事件
        event: asyncio.Event | None = self._cancel_events.get(run_id)
        if event is not None:
            event.set()

        # 更新数据库状态
        async with session_scope(self._factory) as session:
            run: FlowRun | None = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"流程执行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            # 若处于 pending/running 状态，标记为 cancelled
            if run.status in ("pending", "running"):
                run.status = "cancelled"
                run.completed_at = self._clock.now()
                await session.flush()

            return run

    async def retry_node(self, run_id: UUID, node_id: str) -> FlowNodeExecution:
        """重试单个失败节点。

        重新执行指定节点，不影响其他已成功节点。

        Args:
            run_id: 执行记录 ID。
            node_id: 节点 ID。

        Returns:
            FlowNodeExecution: 重试后的节点执行记录。

        Raises:
            AppError: code="not_found"，当执行记录或节点不存在。
            AppError: code="validation_failed"，当节点非失败状态。
        """
        async with session_scope(self._factory) as session:
            run: FlowRun | None = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"流程执行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            version: FlowDefinitionVersionORM | None = await session.scalar(
                sa.select(FlowDefinitionVersionORM).where(
                    FlowDefinitionVersionORM.id == run.flow_version_id
                )
            )
            if version is None:
                raise AppError(
                    code="not_found",
                    message=f"流程版本不存在: {run.flow_version_id}",
                    retryable=False,
                    fields={"flow_version_id": str(run.flow_version_id)},
                )

            nodes: tuple[FlowNode, ...] = nodes_from_json(version.nodes_json)
            edges_from_json(version.edges_json)
            input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
            job_id: UUID | None = run.job_id

            # 查找节点定义
            node_map: dict[str, FlowNode] = {n.node_id: n for n in nodes}
            if node_id not in node_map:
                raise AppError(
                    code="not_found",
                    message=f"节点不存在: {node_id}",
                    retryable=False,
                    fields={"node_id": node_id},
                )

            # 查找已有的执行记录
            existing_exec: FlowNodeExecution | None = await session.scalar(
                sa.select(FlowNodeExecution).where(
                    FlowNodeExecution.flow_run_id == run_id,
                    FlowNodeExecution.node_id == node_id,
                )
            )
            if existing_exec is not None and existing_exec.status != "failed":
                raise AppError(
                    code="validation_failed",
                    message=(f"节点 {node_id} 状态为 {existing_exec.status}，仅失败节点可重试"),
                    retryable=False,
                    fields={
                        "node_id": node_id,
                        "status": existing_exec.status,
                    },
                )

            # 更新 run 状态为 running
            run.status = "running"
            await session.flush()

        # 构建上游输出（从已成功的节点执行记录获取）
        node_outputs: dict[str, dict[str, Any]] = {}
        async with session_scope(self._factory) as session:
            all_execs: list[FlowNodeExecution] = list(
                (
                    await session.execute(
                        sa.select(FlowNodeExecution).where(
                            FlowNodeExecution.flow_run_id == run_id,
                            FlowNodeExecution.status == "succeeded",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for exec_record in all_execs:
                node_outputs[exec_record.node_id] = dict(exec_record.output_summary or {})

        # 解析目标节点输入
        node: FlowNode = node_map[node_id]
        inputs: dict[str, Any] = {}
        for port_name, binding in node.input_bindings.items():
            inputs[port_name] = _resolve_input(binding, node_outputs, input_snapshot)

        # 创建取消事件
        cancel_event: asyncio.Event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event

        # 执行节点
        exec_result: dict[str, Any] = await self._execute_single_node(
            run_id, node, inputs, cancel_event, job_id
        )

        # 更新 run 状态
        version_digest: str = version.digest
        if exec_result["status"] == "succeeded":
            # 重新检查是否所有节点都成功
            all_succeeded: bool = True
            async with session_scope(self._factory) as session:
                pending_or_failed: list[FlowNodeExecution] = list(
                    (
                        await session.execute(
                            sa.select(FlowNodeExecution).where(
                                FlowNodeExecution.flow_run_id == run_id,
                                FlowNodeExecution.status.in_(["pending", "failed"]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                all_succeeded = len(pending_or_failed) == 0

            if all_succeeded:
                # 重新计算摘要
                async with session_scope(self._factory) as session:
                    all_execs2: list[FlowNodeExecution] = list(
                        (
                            await session.execute(
                                sa.select(FlowNodeExecution).where(
                                    FlowNodeExecution.flow_run_id == run_id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                summaries: list[dict[str, Any]] = [
                    {
                        "node_id": e.node_id,
                        "status": e.status,
                        "output_summary": e.output_summary or {},
                    }
                    for e in all_execs2
                ]
                await self._finalize_run(run_id, "succeeded", version_digest, summaries)
                await self._update_job_status(job_id, "succeeded")
            else:
                await self._finalize_run(run_id, "running", version_digest, [])
        else:
            await self._finalize_run(run_id, "failed", version_digest, [])
            await self._update_job_status(job_id, "failed")

        # 返回最新的节点执行记录
        async with session_scope(self._factory) as session:
            latest_exec: FlowNodeExecution | None = await session.scalar(
                sa.select(FlowNodeExecution)
                .where(
                    FlowNodeExecution.flow_run_id == run_id,
                    FlowNodeExecution.node_id == node_id,
                )
                .order_by(FlowNodeExecution.id.desc())
            )
            if latest_exec is None:
                raise AppError(
                    code="not_found",
                    message=f"节点执行记录不存在: {node_id}",
                    retryable=False,
                    fields={"node_id": node_id},
                )
            return latest_exec

    async def get_run(self, run_id: UUID) -> tuple[FlowRun, list[FlowNodeExecution]]:
        """获取执行记录详情（含节点执行状态）。

        Args:
            run_id: 执行记录 ID。

        Returns:
            tuple[FlowRun, list[FlowNodeExecution]]:
                执行记录 + 节点执行记录列表。

        Raises:
            AppError: code="not_found"，当执行记录不存在。
        """
        async with session_scope(self._factory) as session:
            run: FlowRun | None = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"流程执行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            executions: list[FlowNodeExecution] = list(
                (
                    await session.execute(
                        sa.select(FlowNodeExecution)
                        .where(FlowNodeExecution.flow_run_id == run_id)
                        .order_by(FlowNodeExecution.started_at)
                    )
                )
                .scalars()
                .all()
            )
            return run, executions

    async def delete_run(self, run_id: UUID) -> None:
        """删除执行记录及其所有节点执行记录和关联的作业。

        Args:
            run_id: 执行记录 ID。
        """
        async with session_scope(self._factory) as session:
            # 先查出关联的 job_id
            run = await session.scalar(
                sa.select(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            job_id = run.job_id if run else None

            # 删除节点执行记录
            await session.execute(
                sa.delete(FlowNodeExecution).where(FlowNodeExecution.flow_run_id == run_id)
            )
            # 删除执行记录
            await session.execute(
                sa.delete(FlowRun).where(
                    FlowRun.organization_id == self._org_id,
                    FlowRun.id == run_id,
                )
            )
            # 删除关联的作业（避免残留 job 在看板显示空名称）
            if job_id is not None:
                from packages.jobs.entities import Job

                await session.execute(sa.delete(Job).where(Job.id == job_id))
            await session.flush()

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
        async with session_scope(self._factory) as session:
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

                # 4. 删除流程版本
                await session.execute(
                    sa.delete(FlowDefinitionVersionORM).where(
                        FlowDefinitionVersionORM.flow_definition_id == flow_id
                    )
                )

            # 5. 删除流程定义本身
            await session.execute(
                sa.delete(FlowDefinition).where(
                    FlowDefinition.organization_id == self._org_id,
                    FlowDefinition.id == flow_id,
                )
            )
            await session.flush()

    # ---- 内部辅助方法 ----

    async def _finalize_run(
        self,
        run_id: UUID,
        status: str,
        version_digest: str,
        node_exec_summaries: list[dict[str, Any]],
    ) -> None:
        """更新 FlowRun 最终状态与输出摘要。

        Args:
            run_id: 执行记录 ID。
            status: 最终状态。
            version_digest: 版本摘要。
            node_exec_summaries: 节点执行摘要列表。
        """
        output_digest: str | None = None
        if status == "succeeded":
            async with session_scope(self._factory) as session:
                run: FlowRun | None = await session.scalar(
                    sa.select(FlowRun).where(FlowRun.id == run_id)
                )
                if run is not None:
                    input_snapshot: dict[str, Any] = dict(run.input_snapshot or {})
                    output_digest = _compute_output_digest(
                        version_digest,
                        input_snapshot,
                        node_exec_summaries,
                    )

        now: datetime = self._clock.now()
        async with session_scope(self._factory) as session:
            values: dict[str, Any] = {
                "status": status,
                "completed_at": now,
            }
            if output_digest is not None:
                values["output_digest"] = output_digest
            await session.execute(sa.update(FlowRun).values(**values).where(FlowRun.id == run_id))

    async def _update_job_status(self, job_id: UUID | None, status: str) -> None:
        """更新关联作业状态。

        Args:
            job_id: 作业 ID（None 时跳过）。
            status: 目标状态（succeeded/failed/cancelled）。
        """
        if job_id is None:
            return

        from packages.jobs.entities import Job, JobStatus

        status_map: dict[str, JobStatus] = {
            "succeeded": JobStatus.SUCCEEDED,
            "failed": JobStatus.FAILED,
            "cancelled": JobStatus.CANCELLED,
        }
        job_status: JobStatus = status_map.get(status, JobStatus.SUCCEEDED)

        async with session_scope(self._factory) as session:
            await session.execute(
                sa.update(Job)
                .values(
                    status=job_status.value,
                    updated_at=sa.func.now(),
                )
                .where(Job.id == job_id)
            )
