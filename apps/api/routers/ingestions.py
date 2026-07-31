"""数据源预览路由（IRIP Task 13，标准层空表清理后精简版）。

端点：
  POST /api/v1/ingestions/preview                  — 预览数据源（ingestion:write）

原映射评分（/mapping/rank）与映射配置生命周期（/mapping-profiles/*）
端点依赖的 variable / mapping_profile 表已在 migration 0057 中 DROP，
对应端点、请求/响应模型与 DI 占位一并删除。

安全约定：
- 预览需 require_permission("ingestion:write")；
- 密钥凭据绝不返回（仅 secret_id 引用出现在 source_config 中）。
"""

from typing import Annotated, Literal

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
)
from packages.connectors.mapping import IngestionService

#: 路由实例。
ingestions_router = APIRouter(prefix="/api/v1/ingestions", tags=["ingestions"])

#: 需 ingestion:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("ingestion:write"))]


def get_ingestion_service() -> IngestionService:
    """获取 IngestionService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_ingestion_service must be overridden via dependency_overrides")


#: 服务依赖类型别名。
IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]


# ---- 请求模型 ----


class SourceFileConfig(BaseModel):
    """文件数据源配置。

    C-01: 只接受 artifact_id（本租户已上传的 artifact ID），
    不再接受任意服务器路径 path。
    """

    artifact_id: UUID = Field(..., description="本租户已上传的 artifact ID")
    format: Literal["csv", "xlsx", "json"]


class SourcePostgresConfig(BaseModel):
    """PostgreSQL 数据源配置（仅 secret_id 引用）。"""

    secret_id: str = Field(..., description="密钥 UUID 引用")
    query: str = Field(..., min_length=1)


class SourceRestConfig(BaseModel):
    """REST 数据源配置（仅 secret_id 引用）。"""

    secret_id: str = Field(..., description="密钥 UUID 引用")
    path: str = Field(..., min_length=1)
    method: Literal["GET", "POST"] = "GET"


class SourceSpec(BaseModel):
    """数据源描述。"""

    kind: Literal["file", "postgres", "rest"]
    file: SourceFileConfig | None = None
    postgres: SourcePostgresConfig | None = None
    rest: SourceRestConfig | None = None


class PreviewRequest(BaseModel):
    """预览请求。"""

    source: SourceSpec
    limit: int = Field(100, ge=1, le=10000)


# ---- 响应模型 ----


class PreviewRowResponse(BaseModel):
    """预览行响应（值序列化为字符串）。"""

    values: list[str | None]


class PreviewResponse(BaseModel):
    """预览响应。"""

    columns: list[str]
    rows: list[PreviewRowResponse]
    row_count: int


# ---- 辅助函数 ----


def _source_to_contract(spec: SourceSpec) -> ConnectorSource:
    """将请求 SourceSpec 转为 ConnectorSource。"""
    if spec.kind == "file":
        config: dict = spec.file.model_dump() if spec.file else {}
    elif spec.kind == "postgres":
        config = spec.postgres.model_dump() if spec.postgres else {}
    elif spec.kind == "rest":
        config = spec.rest.model_dump() if spec.rest else {}
    else:
        config = {}
    return ConnectorSource(kind=spec.kind, config=config)


def _preview_to_response(table: PreviewTable) -> PreviewResponse:
    """将 PreviewTable 转为响应模型。"""
    return PreviewResponse(
        columns=list(table.columns),
        rows=[
            PreviewRowResponse(values=[None if v is None else str(v) for v in row])
            for row in table.rows
        ],
        row_count=table.row_count,
    )


# ---- 端点：预览 ----


@ingestions_router.post("/preview", response_model=PreviewResponse)
async def preview_source(
    body: PreviewRequest,
    current_user: WriteUserDep,
    service: IngestionServiceDep,
) -> PreviewResponse:
    """预览数据源（按 kind 构造连接器并读取前 limit 行）。

    Args:
        body: 预览请求（含 source 与 limit）。
        current_user: 当前认证用户（需 ingestion:write 权限）。
        service: 预览服务。

    Returns:
        PreviewResponse: 预览结果（列名 + 行 + 总行数）。
    """
    source = _source_to_contract(body.source)
    table = await service.preview(source, limit=body.limit)
    return _preview_to_response(table)
