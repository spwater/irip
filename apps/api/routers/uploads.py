"""工件上传与下载路由。

端点（docs/arch-v0.md §4.3 工件上传时序图）：
  POST /api/v1/uploads               — 请求预签名上传 URL
  POST /api/v1/uploads/{id}/complete  — 完成上传，校验并创建工件记录
  GET  /api/v1/artifacts/{id}/download — 获取预签名下载 URL

安全约定：
- 所有端点需 Authorization: Bearer <jwt>；
- media_type 必须在白名单中；
- complete 端点校验 S3 对象的 SHA-256 与 size。
"""

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.common.artifacts import (
    ALLOWED_MEDIA_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    ArtifactService,
)
from packages.common.errors import AppError
from packages.common.ids import new_id

#: 路由实例。
uploads_router = APIRouter(prefix="/api/v1", tags=["artifacts"])
artifacts_router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])

#: 需 artifact:upload 权限的当前用户依赖。
UploadUserDep = Annotated[CurrentUser, Depends(require_permission("artifact:upload"))]

#: 需 artifact:download 权限的当前用户依赖。
DownloadUserDep = Annotated[CurrentUser, Depends(require_permission("artifact:download"))]


def get_artifact_service() -> ArtifactService:
    """获取 ArtifactService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_artifact_service must be overridden via dependency_overrides")


ArtifactServiceDep = Annotated[ArtifactService, Depends(get_artifact_service)]


# ---- 请求/响应模型 ----


class PresignUploadRequest(BaseModel):
    """预签名上传请求体。"""

    filename: str = Field(..., min_length=1, max_length=255)
    media_type: str = Field(..., max_length=128)
    size_bytes: int = Field(..., ge=0)


class PresignUploadResponse(BaseModel):
    """预签名上传响应体。"""

    artifact_id: str
    upload_url: str
    object_key: str


class CompleteUploadRequest(BaseModel):
    """完成上传请求体。"""

    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(..., ge=0)
    media_type: str = Field(..., max_length=128)
    filename: str = Field(..., min_length=1, max_length=255)


class CompleteUploadResponse(BaseModel):
    """完成上传响应体。"""

    artifact_id: str
    sha256: str
    object_key: str
    media_type: str
    size_bytes: int


class DownloadResponse(BaseModel):
    """下载响应体。"""

    download_url: str


# ---- 端点 ----


@uploads_router.post("/uploads", response_model=PresignUploadResponse)
async def presign_upload(
    body: PresignUploadRequest,
    request: Request,
    current_user: UploadUserDep,
    service: ArtifactServiceDep,
) -> PresignUploadResponse:
    """请求预签名上传 URL。

    客户端提供文件名、媒体类型和大小，服务端返回预签名 PUT URL。
    客户端直接上传到 S3，完成后调用 /uploads/{id}/complete。

    Args:
        body: 上传请求体。
        request: FastAPI 请求对象（用于提取 Host）。
        current_user: 当前认证用户。
        service: 工件服务。

    Returns:
        PresignUploadResponse: 预签名 URL + 临时 artifact_id。

    Raises:
        AppError: code="unsupported_media_type"，当媒体类型不在白名单中。
    """
    if body.media_type not in ALLOWED_MEDIA_TYPES:
        raise AppError(
            code="unsupported_media_type",
            message=f"不支持的媒体类型: {body.media_type}",
            retryable=False,
            fields={"media_type": body.media_type},
        )

    if body.size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise AppError(
            code="file_too_large",
            message=(
                f"文件大小 {body.size_bytes} 超过上限 {MAX_UPLOAD_SIZE_BYTES} 字节（100 MiB）"
            ),
            retryable=False,
            fields={
                "size_bytes": body.size_bytes,
                "max_size_bytes": MAX_UPLOAD_SIZE_BYTES,
            },
        )

    # 从请求 Host header 构建外部端点，fallback 用环境变量配置的 MinIO 地址
    minio_endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    minio_external = minio_endpoint

    # H-04: 上传会话绑定 tenant/user（object_key 含 user_id 前缀）
    user_prefix = str(current_user.user_id)[:8]
    artifact_id = new_id()
    object_key = f"uploads/{user_prefix}/{artifact_id}"

    # H-04: 使用 presigned POST（带 content-length-range）替代 presigned PUT
    presigned = service.presign_upload_post(
        object_key=object_key,
        max_size=MAX_UPLOAD_SIZE_BYTES,
        endpoint_override=minio_external,
    )
    upload_url = presigned["url"]

    return PresignUploadResponse(
        artifact_id=str(artifact_id),
        upload_url=upload_url,
        object_key=object_key,
    )


@uploads_router.post(
    "/uploads/{artifact_id}/complete",
    response_model=CompleteUploadResponse,
)
async def complete_upload(
    artifact_id: UUID,
    body: CompleteUploadRequest,
    current_user: UploadUserDep,
    service: ArtifactServiceDep,
) -> CompleteUploadResponse:
    """完成上传，校验 S3 对象并创建工件记录。

    客户端上传完成后调用此端点。服务端下载临时对象、校验哈希和大小，
    然后通过 put_bytes 创建正式的工件记录（含去重逻辑）。

    Args:
        artifact_id: 临时工件 ID（用于定位 S3 临时对象）。
        body: 完成请求体。
        current_user: 当前认证用户。
        service: 工件服务。

    Returns:
        CompleteUploadResponse: 工件引用信息。

    Raises:
        AppError: code="hash_mismatch"，当 SHA-256 不匹配时。
        AppError: code="size_mismatch"，当大小不匹配时。
    """
    user_prefix = str(current_user.user_id)[:8]
    ref = await service.complete_upload(
        temp_key=f"uploads/{user_prefix}/{artifact_id}",
        media_type=body.media_type,
        filename=body.filename,
        expected_sha256=body.sha256,
        expected_size=body.size_bytes,
    )

    return CompleteUploadResponse(
        artifact_id=str(ref.artifact_id),
        sha256=ref.sha256,
        object_key=ref.object_key,
        media_type=ref.media_type,
        size_bytes=ref.size_bytes,
    )


@artifacts_router.get("/{artifact_id}/download", response_model=DownloadResponse)
async def download_artifact(
    artifact_id: UUID,
    request: Request,
    current_user: DownloadUserDep,
    service: ArtifactServiceDep,
) -> DownloadResponse:
    """获取工件预签名下载 URL。

    从请求的 Host header 动态推导 MinIO 外部访问地址，
    使预签名 URL 的 host 与浏览器访问的 host 一致，
    无需硬编码部署 IP。

    Args:
        artifact_id: 工件 UUID。
        request: FastAPI 请求对象（用于提取 Host）。
        current_user: 当前认证用户。
        service: 工件服务。

    Returns:
        DownloadResponse: 预签名下载 URL。

    Raises:
        AppError: code="not_found"，当工件不存在时。
    """
    # 从请求 Host header 构建外部端点（替换端口为 MinIO 的 9000）
    host = request.headers.get("host", "localhost:9000")
    # API 端口是 8000，MinIO 端口是 9000，替换端口部分
    if ":" in host:
        host_base = host.rsplit(":", 1)[0]
    else:
        host_base = host
    minio_external = f"http://{host_base}:9000"
    url = await service.presign_download(artifact_id, endpoint_override=minio_external)
    return DownloadResponse(download_url=url)
