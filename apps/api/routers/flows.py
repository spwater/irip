"""流程管理路由：创建 / 发布 / 列表 / 详情 / 执行 / 恢复 / 取消 / 重试。

端点（IRIP V2-T03）：
  POST   /api/v1/flows                         — 创建流程定义（含 DAG 校验，flow:manage）
  POST   /api/v1/flows/{flow_id}/publish        — 发布流程版本（不可变，flow:manage）
  GET    /api/v1/flows                          — 列表（flow:read）
  GET    /api/v1/flows/{flow_id}                 — 详情（flow:read）
  POST   /api/v1/flows/{flow_id}/runs            — 创建执行（202 Accepted，flow:execute）
  POST   /api/v1/flows/runs/{run_id}/resume      — 恢复（flow:execute）
  POST   /api/v1/flows/runs/{run_id}/cancel       — 取消（flow:execute）
  POST   /api/v1/flows/runs/{run_id}/retry/{node_id} — 重试节点（flow:execute）
  GET    /api/v1/flows/runs/{run_id}             — 运行详情（含节点状态，flow:read）

安全约定：
- 创建/发布需 require_permission("flow:manage")；
- 列表/详情/运行详情需 require_permission("flow:read")；
- 创建执行/恢复/取消/重试需 require_permission("flow:execute")。

DI 约定（与 V1 standards 路由一致）：
- get_flow_service() 抛 NotImplementedError，
  生产环境通过 dependency_overrides 注入按请求构造的实例。
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.components.flow_runtime import (
    FlowDefinition,
    FlowDefinitionVersionORM,
    FlowNodeExecution,
    FlowRun,
    FlowRuntimeService,
)
from packages.components.flows import (
    FlowEdge,
    FlowNode,
    edge_from_dict,
    node_from_dict,
)

#: 路由实例。
flows_router = APIRouter(prefix="/api/v1/flows", tags=["flows"])

#: 需 flow:manage 权限的当前用户依赖。
ManageUserDep = Annotated[
    CurrentUser, Depends(require_permission("flow:manage"))
]

#: 需 flow:read 权限的当前用户依赖。
ReadUserDep = Annotated[
    CurrentUser, Depends(require_permission("flow:read"))
]

#: 需 flow:execute 权限的当前用户依赖。
ExecuteUserDep = Annotated[
    CurrentUser, Depends(require_permission("flow:execute"))
]


def get_flow_service() -> FlowRuntimeService:
    """获取 FlowRuntimeService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError(
        "get_flow_service must be overridden via dependency_overrides"
    )


#: FlowRuntimeService 依赖类型别名。
FlowServiceDep = Annotated[
    FlowRuntimeService, Depends(get_flow_service)
]


# ---- 请求模型 ----


class FlowNodeSchema(BaseModel):
    """流程节点请求模型。"""

    node_id: str = Field(..., min_length=1, max_length=128)
    component_name: str = Field(..., min_length=1, max_length=128)
    component_version: str = Field(..., min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    input_bindings: dict[str, str] = Field(default_factory=dict)


class FlowEdgeSchema(BaseModel):
    """流程边请求模型。"""

    source_node: str = Field(..., min_length=1, max_length=128)
    source_port: str = Field(..., min_length=1, max_length=128)
    target_node: str = Field(..., min_length=1, max_length=128)
    target_port: str = Field(..., min_length=1, max_length=128)


class CreateFlowRequest(BaseModel):
    """创建流程定义请求。"""

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="流程编码，仅小写字母/数字/下划线",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    nodes: list[FlowNodeSchema] = Field(default_factory=list)
    edges: list[FlowEdgeSchema] = Field(default_factory=list)


class PublishFlowRequest(BaseModel):
    """发布流程版本请求。"""

    nodes: list[FlowNodeSchema] = Field(..., min_length=1)
    edges: list[FlowEdgeSchema] = Field(default_factory=list)
    random_seed: int = Field(0, ge=0)


class CreateRunRequest(BaseModel):
    """创建执行请求。"""

    inputs: dict[str, Any] = Field(default_factory=dict)


# ---- 响应模型 ----


class FlowDefinitionResponse(BaseModel):
    """流程定义响应。"""

    id: str
    code: str
    display_name: str
    status: str
    lock_version: int
    created_at: datetime
    updated_at: datetime
    latest_version: dict[str, Any] | None = None


class FlowListResponse(BaseModel):
    """流程列表响应。"""

    items: list[FlowDefinitionResponse]


class FlowVersionResponse(BaseModel):
    """流程版本响应。"""

    id: str
    flow_definition_id: str
    version: int
    digest: str
    random_seed: int
    status: str
    published_at: datetime | None
    created_at: datetime
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class FlowRunResponse(BaseModel):
    """流程执行响应。"""

    id: str
    flow_version_id: str
    status: str
    job_id: str | None
    output_digest: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class FlowNodeExecutionResponse(BaseModel):
    """节点执行记录响应。"""

    id: str
    node_id: str
    status: str
    duration_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None


class FlowRunDetailResponse(BaseModel):
    """流程执行详情响应（含节点状态）。"""

    id: str
    flow_version_id: str
    status: str
    job_id: str | None
    output_digest: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    node_executions: list[FlowNodeExecutionResponse]


# ---- 辅助函数 ----


def _nodes_to_schema_list(
    nodes: list[dict[str, Any]],
) -> tuple[FlowNode, ...]:
    """将请求中的节点字典列表转为 FlowNode 元组。"""
    return tuple(node_from_dict(d) for d in nodes)


def _edges_to_schema_list(
    edges: list[dict[str, Any]],
) -> tuple[FlowEdge, ...]:
    """将请求中的边字典列表转为 FlowEdge 元组。"""
    return tuple(edge_from_dict(d) for d in edges)


def _definition_to_response(
    definition: FlowDefinition,
    version: FlowDefinitionVersionORM | None,
) -> FlowDefinitionResponse:
    """将 FlowDefinition ORM 转为响应模型。"""
    latest_version: dict[str, Any] | None = None
    if version is not None:
        latest_version = {
            "id": str(version.id),
            "version": version.version,
            "digest": version.digest,
            "status": version.status,
            "published_at": version.published_at.isoformat()
            if version.published_at
            else None,
        }
    return FlowDefinitionResponse(
        id=str(definition.id),
        code=definition.code,
        display_name=definition.display_name,
        status=definition.status,
        lock_version=definition.lock_version,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
        latest_version=latest_version,
    )


def _version_to_response(
    definition: FlowDefinition,
    version: FlowDefinitionVersionORM,
    include_graph: bool = False,
) -> FlowVersionResponse:
    """将 FlowDefinitionVersionORM 转为响应模型。"""
    response = FlowVersionResponse(
        id=str(version.id),
        flow_definition_id=str(definition.id),
        version=version.version,
        digest=version.digest,
        random_seed=version.random_seed,
        status=version.status,
        published_at=version.published_at,
        created_at=version.created_at,
    )
    if include_graph:
        response.nodes = version.nodes_json or []
        response.edges = version.edges_json or []
    return response


def _run_to_response(run: FlowRun) -> FlowRunResponse:
    """将 FlowRun ORM 转为响应模型。"""
    return FlowRunResponse(
        id=str(run.id),
        flow_version_id=str(run.flow_version_id),
        status=run.status,
        job_id=str(run.job_id) if run.job_id else None,
        output_digest=run.output_digest,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _execution_to_response(
    exec_record: FlowNodeExecution,
) -> FlowNodeExecutionResponse:
    """将 FlowNodeExecution ORM 转为响应模型。"""
    return FlowNodeExecutionResponse(
        id=str(exec_record.id),
        node_id=exec_record.node_id,
        status=exec_record.status,
        duration_ms=exec_record.duration_ms,
        started_at=exec_record.started_at,
        completed_at=exec_record.completed_at,
    )


# ---- 端点：定义管理 ----


@flows_router.post(
    "/",
    response_model=FlowDefinitionResponse,
    status_code=201,
)
async def create_flow(
    body: CreateFlowRequest,
    current_user: ManageUserDep,
    service: FlowServiceDep,
) -> FlowDefinitionResponse:
    """创建流程定义（含 DAG 校验）。

    创建后处于 draft 状态。若提供 nodes/edges，则先进行 DAG 校验。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 flow:manage 权限）。
        service: 流程运行时服务。

    Returns:
        FlowDefinitionResponse: 新创建的流程定义（201 Created）。

    Raises:
        AppError: code="conflict"，当编码已存在。
        AppError: code="validation_failed"，当 DAG 校验失败。
    """
    nodes = _nodes_to_schema_list(
        [n.model_dump() for n in body.nodes]
    )
    edges = _edges_to_schema_list(
        [e.model_dump() for e in body.edges]
    )
    definition = await service.create_definition(
        code=body.code,
        display_name=body.display_name,
        nodes=nodes,
        edges=edges,
    )
    return _definition_to_response(definition, None)


@flows_router.post(
    "/{flow_id}/publish",
    response_model=FlowVersionResponse,
)
async def publish_flow(
    flow_id: UUID,
    body: PublishFlowRequest,
    current_user: ManageUserDep,
    service: FlowServiceDep,
) -> FlowVersionResponse:
    """发布流程版本（不可变）。

    发布后版本不可修改。包含 DAG 校验、端口类型校验、参数 schema 校验。

    Args:
        flow_id: 流程定义 ID。
        body: 发布请求体。
        current_user: 当前认证用户（需 flow:manage 权限）。
        service: 流程运行时服务。

    Returns:
        FlowVersionResponse: 新发布的版本。

    Raises:
        AppError: code="not_found"，当定义不存在。
        AppError: code="validation_failed"，当校验失败。
    """
    nodes = _nodes_to_schema_list(
        [n.model_dump() for n in body.nodes]
    )
    edges = _edges_to_schema_list(
        [e.model_dump() for e in body.edges]
    )
    version = await service.publish_version(
        flow_definition_id=flow_id,
        nodes=nodes,
        edges=edges,
        random_seed=body.random_seed,
    )
    definition, _ = await service.get_definition(flow_id)
    return _version_to_response(definition, version, include_graph=True)


@flows_router.get(
    "/",
    response_model=FlowListResponse,
)
async def list_flows(
    current_user: ReadUserDep,
    service: FlowServiceDep,
    status: str | None = Query(None, description="按状态过滤"),
) -> FlowListResponse:
    """列表查询流程定义。

    Args:
        current_user: 当前认证用户（需 flow:read 权限）。
        service: 流程运行时服务。
        status: 可选，按状态过滤（draft/published/deprecated）。

    Returns:
        FlowListResponse: 流程列表。
    """
    items = await service.list_definitions(status=status)
    return FlowListResponse(
        items=[
            _definition_to_response(definition, version)
            for definition, version in items
        ]
    )


@flows_router.get(
    "/{flow_id}",
    response_model=FlowDefinitionResponse,
)
async def get_flow(
    flow_id: UUID,
    current_user: ReadUserDep,
    service: FlowServiceDep,
) -> FlowDefinitionResponse:
    """获取流程定义详情（含最新版本）。

    Args:
        flow_id: 流程定义 ID。
        current_user: 当前认证用户（需 flow:read 权限）。
        service: 流程运行时服务。

    Returns:
        FlowDefinitionResponse: 流程详情。

    Raises:
        AppError: code="not_found"，当定义不存在。
    """
    definition, version = await service.get_definition(flow_id)
    return _definition_to_response(definition, version)


# ---- 端点：执行管理 ----


@flows_router.post(
    "/{flow_id}/runs",
    response_model=FlowRunResponse,
    status_code=202,
)
async def create_run(
    flow_id: UUID,
    body: CreateRunRequest,
    current_user: ExecuteUserDep,
    service: FlowServiceDep,
) -> FlowRunResponse:
    """创建流程执行（202 Accepted）。

    创建后处于 pending 状态，由异步 worker 执行。

    Args:
        flow_id: 流程定义 ID。
        body: 创建执行请求体。
        current_user: 当前认证用户（需 flow:execute 权限）。
        service: 流程运行时服务。

    Returns:
        FlowRunResponse: 新创建的执行记录（202 Accepted）。

    Raises:
        AppError: code="not_found"，当定义不存在。
        AppError: code="validation_failed"，当无已发布版本。
    """
    from packages.common.errors import AppError

    definition, version = await service.get_definition(flow_id)
    if version is None:
        raise AppError(
            code="validation_failed",
            message=f"流程无已发布版本: {flow_id}",
            retryable=False,
            fields={"flow_id": str(flow_id)},
        )

    run = await service.create_run(
        flow_version_id=version.id,
        inputs=body.inputs,
    )
    return _run_to_response(run)


@flows_router.post(
    "/runs/{run_id}/resume",
    response_model=FlowRunResponse,
)
async def resume_run(
    run_id: UUID,
    current_user: ExecuteUserDep,
    service: FlowServiceDep,
) -> FlowRunResponse:
    """恢复流程执行（跳过已成功节点）。

    Args:
        run_id: 执行记录 ID。
        current_user: 当前认证用户（需 flow:execute 权限）。
        service: 流程运行时服务。

    Returns:
        FlowRunResponse: 恢复后的执行记录。

    Raises:
        AppError: code="not_found"，当执行记录不存在。
    """
    await service.resume(run_id)
    run, _executions = await service.get_run(run_id)
    return _run_to_response(run)


@flows_router.post(
    "/runs/{run_id}/cancel",
    response_model=FlowRunResponse,
)
async def cancel_run(
    run_id: UUID,
    current_user: ExecuteUserDep,
    service: FlowServiceDep,
) -> FlowRunResponse:
    """取消流程执行。

    Args:
        run_id: 执行记录 ID。
        current_user: 当前认证用户（需 flow:execute 权限）。
        service: 流程运行时服务。

    Returns:
        FlowRunResponse: 取消后的执行记录。

    Raises:
        AppError: code="not_found"，当执行记录不存在。
    """
    run = await service.cancel(run_id)
    return _run_to_response(run)


@flows_router.post(
    "/runs/{run_id}/retry/{node_id}",
    response_model=FlowNodeExecutionResponse,
)
async def retry_node(
    run_id: UUID,
    node_id: str,
    current_user: ExecuteUserDep,
    service: FlowServiceDep,
) -> FlowNodeExecutionResponse:
    """重试单个失败节点。

    Args:
        run_id: 执行记录 ID。
        node_id: 节点 ID。
        current_user: 当前认证用户（需 flow:execute 权限）。
        service: 流程运行时服务。

    Returns:
        FlowNodeExecutionResponse: 重试后的节点执行记录。

    Raises:
        AppError: code="not_found"，当执行记录或节点不存在。
        AppError: code="validation_failed"，当节点非失败状态。
    """
    execution = await service.retry_node(run_id, node_id)
    return _execution_to_response(execution)


@flows_router.get(
    "/runs/{run_id}",
    response_model=FlowRunDetailResponse,
)
async def get_run(
    run_id: UUID,
    current_user: ReadUserDep,
    service: FlowServiceDep,
) -> FlowRunDetailResponse:
    """获取执行详情（含节点状态）。

    Args:
        run_id: 执行记录 ID。
        current_user: 当前认证用户（需 flow:read 权限）。
        service: 流程运行时服务。

    Returns:
        FlowRunDetailResponse: 执行详情 + 节点执行列表。

    Raises:
        AppError: code="not_found"，当执行记录不存在。
    """
    run, executions = await service.get_run(run_id)
    return FlowRunDetailResponse(
        id=str(run.id),
        flow_version_id=str(run.flow_version_id),
        status=run.status,
        job_id=str(run.job_id) if run.job_id else None,
        output_digest=run.output_digest,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        node_executions=[
            _execution_to_response(e) for e in executions
        ],
    )
