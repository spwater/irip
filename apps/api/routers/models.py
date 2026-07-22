"""模型管理路由：创建 / 列表 / 详情 / 版本 / 验证 / 发布 / 回滚 / 预测 / 废弃。

端点（IRIP V2-T04）：
  POST   /api/v1/models                                       — 创建模型（model:manage）
  GET    /api/v1/models                                       — 列表（model:read）
  GET    /api/v1/models/{model_id}                            — 详情（model:read）
  GET    /api/v1/models/{model_id}/versions                   — 版本列表（model:read）
  POST   /api/v1/models/{model_id}/versions/{version_id}/validate  — 提交验证（model:manage）
  POST   /api/v1/models/{model_id}/versions/{version_id}/publish   — 发布（model:manage）
  POST   /api/v1/models/{model_id}/rollback                  — 回滚（model:manage）
  POST   /api/v1/models/{model_id}/predict                   — 预测（model:read）
  POST   /api/v1/models/{model_id}/deprecate                 — 废弃（model:manage）

安全约定：
- 创建/验证/发布/回滚/废弃需 require_permission("model:manage")；
- 列表/详情/版本/预测需 require_permission("model:read")。

DI 约定（与 V1 standards / V2-T01 components 路由一致）：
- get_model_service() 抛 NotImplementedError，
  生产环境通过 dependency_overrides 注入按请求构造的实例。
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission

#: 路由实例。
models_router = APIRouter(
    prefix="/api/v1/models", tags=["models"]
)

#: 需 model:manage 权限的当前用户依赖。
ManageUserDep = Annotated[
    CurrentUser, Depends(require_permission("model:manage"))
]

#: 需 model:read 权限的当前用户依赖。
ReadUserDep = Annotated[
    CurrentUser, Depends(require_permission("model:read"))
]


def get_model_service() -> Any:
    """获取 ModelService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError(
        "get_model_service must be overridden via dependency_overrides"
    )


#: ModelService 依赖类型别名。
ModelServiceDep = Annotated[Any, Depends(get_model_service)]


# ---- 请求模型 ----


class CreateModelRequest(BaseModel):
    """创建模型请求。"""

    code: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="模型代码（组织内唯一）",
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="模型显示名称",
    )


class ValidateModelRequest(BaseModel):
    """提交验证请求。"""

    dataset_artifact_id: str | None = Field(
        None, description="验证数据集工件 ID"
    )
    metrics: dict[str, Any] | None = Field(
        None, description="验证指标字典"
    )
    applicability_domain: dict[str, Any] | None = Field(
        None, description="适用域字典"
    )


class RollbackRequest(BaseModel):
    """回滚请求。"""

    target_version_id: str = Field(
        ..., description="目标版本 ID（UUID 字符串）"
    )


class PredictRequest(BaseModel):
    """预测请求。"""

    inputs: dict[str, Any] = Field(
        ..., description="输入参数字典"
    )


# ---- 响应模型 ----


class ModelResponse(BaseModel):
    """模型响应。"""

    id: str
    code: str
    display_name: str
    status: str
    current_version_id: str | None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class ModelListResponse(BaseModel):
    """模型列表响应。"""

    items: list[ModelResponse]


class ModelVersionResponse(BaseModel):
    """模型版本响应。"""

    id: str
    model_id: str
    version: int
    status: str
    contract_sha256: str | None
    model_artifact_id: str | None
    metrics: dict[str, Any]
    applicability_domain: dict[str, Any]
    code_hash: str | None
    dependency_hash: str | None
    model_hash: str | None
    created_at: datetime
    published_at: datetime | None


class ModelVersionListResponse(BaseModel):
    """模型版本列表响应。"""

    items: list[ModelVersionResponse]


class PredictionResponse(BaseModel):
    """预测响应。"""

    model_id: str
    model_version_id: str
    version: int
    predictions: dict[str, Any]
    metadata: dict[str, Any]
    fact_id: str | None


# ---- 端点 ----


@models_router.post(
    "/",
    response_model=ModelResponse,
    status_code=201,
)
async def create_model(
    body: CreateModelRequest,
    current_user: ManageUserDep,
    service: ModelServiceDep,
) -> ModelResponse:
    """创建模型。

    Args:
        body: 创建请求。
        current_user: 当前认证用户（需 model:manage 权限）。
        service: 模型服务。

    Returns:
        ModelResponse: 新创建的模型信息（201 Created）。
    """
    model = await service.create_model(
        code=body.code, display_name=body.display_name
    )
    return _to_model_response(model)


@models_router.get("/", response_model=ModelListResponse)
async def list_models(
    current_user: ReadUserDep,
    service: ModelServiceDep,
    status: str | None = None,
) -> ModelListResponse:
    """列表查询模型。

    Args:
        current_user: 当前认证用户（需 model:read 权限）。
        service: 模型服务。
        status: 可选，按状态过滤。

    Returns:
        ModelListResponse: 模型列表。
    """
    models = await service.list_models(status=status)
    return ModelListResponse(
        items=[_to_model_response(m) for m in models]
    )


@models_router.get(
    "/{model_id}", response_model=ModelResponse
)
async def get_model(
    model_id: UUID,
    current_user: ReadUserDep,
    service: ModelServiceDep,
) -> ModelResponse:
    """获取模型详情。

    Args:
        model_id: 模型 UUID。
        current_user: 当前认证用户（需 model:read 权限）。
        service: 模型服务。

    Returns:
        ModelResponse: 模型详情。
    """
    model = await service.get_model(model_id)
    return _to_model_response(model)


@models_router.get(
    "/{model_id}/versions",
    response_model=ModelVersionListResponse,
)
async def list_versions(
    model_id: UUID,
    current_user: ReadUserDep,
    service: ModelServiceDep,
) -> ModelVersionListResponse:
    """列出模型的所有版本。

    Args:
        model_id: 模型 UUID。
        current_user: 当前认证用户（需 model:read 权限）。
        service: 模型服务。

    Returns:
        ModelVersionListResponse: 版本列表。
    """
    versions = await service.get_versions(model_id)
    return ModelVersionListResponse(
        items=[_to_version_response(v) for v in versions]
    )


@models_router.post(
    "/{model_id}/versions/{version_id}/validate",
    response_model=ModelVersionResponse,
)
async def validate_version(
    model_id: UUID,
    version_id: UUID,
    body: ValidateModelRequest,
    current_user: ManageUserDep,
    service: ModelServiceDep,
) -> ModelVersionResponse:
    """提交验证（状态 pending_validation → validated）。

    Args:
        model_id: 模型 UUID。
        version_id: 版本 UUID。
        body: 验证请求。
        current_user: 当前认证用户（需 model:manage 权限）。
        service: 模型服务。

    Returns:
        ModelVersionResponse: 更新后的版本信息。
    """
    dataset_artifact_id: UUID | None = None
    if body.dataset_artifact_id is not None:
        dataset_artifact_id = UUID(body.dataset_artifact_id)
    version = await service.validate(
        model_id=model_id,
        version_id=version_id,
        dataset_artifact_id=dataset_artifact_id,
        metrics=body.metrics,
        applicability_domain=body.applicability_domain,
    )
    return _to_version_response(version)


@models_router.post(
    "/{model_id}/versions/{version_id}/publish",
    response_model=ModelResponse,
)
async def publish_version(
    model_id: UUID,
    version_id: UUID,
    current_user: ManageUserDep,
    service: ModelServiceDep,
) -> ModelResponse:
    """发布模型版本（更新发布指针）。

    Args:
        model_id: 模型 UUID。
        version_id: 版本 UUID。
        current_user: 当前认证用户（需 model:manage 权限）。
        service: 模型服务。

    Returns:
        ModelResponse: 更新后的模型信息。
    """
    model = await service.publish(model_id, version_id)
    return _to_model_response(model)


@models_router.post(
    "/{model_id}/rollback", response_model=ModelResponse
)
async def rollback_model(
    model_id: UUID,
    body: RollbackRequest,
    current_user: ManageUserDep,
    service: ModelServiceDep,
) -> ModelResponse:
    """回滚发布指针到指定版本。

    Args:
        model_id: 模型 UUID。
        body: 回滚请求。
        current_user: 当前认证用户（需 model:manage 权限）。
        service: 模型服务。

    Returns:
        ModelResponse: 更新后的模型信息。
    """
    target_version_id = UUID(body.target_version_id)
    model = await service.rollback(model_id, target_version_id)
    return _to_model_response(model)


@models_router.post(
    "/{model_id}/predict", response_model=PredictionResponse
)
async def predict_model(
    model_id: UUID,
    body: PredictRequest,
    current_user: ReadUserDep,
    service: ModelServiceDep,
) -> PredictionResponse:
    """使用当前发布版本预测。

    Args:
        model_id: 模型 UUID。
        body: 预测请求。
        current_user: 当前认证用户（需 model:read 权限）。
        service: 模型服务。

    Returns:
        PredictionResponse: 预测结果。
    """
    result = await service.predict(model_id, body.inputs)
    return PredictionResponse(
        model_id=str(result.model_id),
        model_version_id=str(result.model_version_id),
        version=result.version,
        predictions=dict(result.predictions),
        metadata=dict(result.metadata),
        fact_id=(
            str(result.fact_id) if result.fact_id else None
        ),
    )


@models_router.post(
    "/{model_id}/deprecate", response_model=ModelResponse
)
async def deprecate_model(
    model_id: UUID,
    current_user: ManageUserDep,
    service: ModelServiceDep,
) -> ModelResponse:
    """废弃模型（状态 → deprecated）。

    Args:
        model_id: 模型 UUID。
        current_user: 当前认证用户（需 model:manage 权限）。
        service: 模型服务。

    Returns:
        ModelResponse: 更新后的模型信息。
    """
    model = await service.deprecate(model_id)
    return _to_model_response(model)


# ---- 辅助函数 ----


def _to_model_response(model: Any) -> ModelResponse:
    """将 Model ORM 实体转换为响应模型。"""
    return ModelResponse(
        id=str(model.id),
        code=model.code,
        display_name=model.display_name,
        status=model.status,
        current_version_id=(
            str(model.current_version_id)
            if model.current_version_id is not None
            else None
        ),
        lock_version=model.lock_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_version_response(version: Any) -> ModelVersionResponse:
    """将 ModelVersion ORM 实体转换为响应模型。"""
    contract_dict: dict[str, Any] = dict(version.contract_json or {})
    contract_sha256: str | None = contract_dict.get("sha256")
    return ModelVersionResponse(
        id=str(version.id),
        model_id=str(version.model_id),
        version=version.version,
        status=version.status,
        contract_sha256=contract_sha256,
        model_artifact_id=(
            str(version.model_artifact_id)
            if version.model_artifact_id is not None
            else None
        ),
        metrics=dict(version.metrics_json or {}),
        applicability_domain=dict(
            version.applicability_domain_json or {}
        ),
        code_hash=version.code_hash,
        dependency_hash=version.dependency_hash,
        model_hash=version.model_hash,
        created_at=version.created_at,
        published_at=version.published_at,
    )
