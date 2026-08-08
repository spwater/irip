"""文件浏览与上传路由：为前端组件参数（如 llm_extractor 的 path）提供文件选择与上传。

端点：
  GET  /api/v1/files/browse?path=xxx  — 列出指定目录内容（flow:read）
  POST /api/v1/files/upload           — 上传文件到 MinIO，返回 artifact_id（flow:read）

安全约定：
- 浏览根目录限制为环境变量 IRIP_FILE_BROWSE_ROOT（默认项目根目录）；
- 解析路径并验证不超出根目录（防止目录穿越）；
- 隐藏文件（.开头）不返回；
- 上传文件大小上限 100 MiB，媒体类型必须在白名单中。
"""

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, UploadFile
from pydantic import BaseModel

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.routers.uploads import get_artifact_service
from packages.common.artifacts import (
    ALLOWED_MEDIA_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    ArtifactService,
)
from packages.common.errors import AppError

#: 路由实例。
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

#: 浏览根目录（环境变量或项目根）。
_BROWSE_ROOT = os.environ.get(
    "IRIP_FILE_BROWSE_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)

#: 需 flow:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("flow:read"))]

#: 需 artifact:upload 权限的当前用户依赖（文件上传需独立写权限）。
UploadUserDep = Annotated[CurrentUser, Depends(require_permission("artifact:upload"))]
ArtifactServiceDep = Annotated[ArtifactService, Depends(get_artifact_service)]

#: 文件扩展名 → 媒体类型映射（用于从文件名推断 MIME 类型）。
_EXTENSION_MEDIA_TYPE_MAP: dict[str, str] = {
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json",
}


def _guess_media_type(filename: str, content_type: str | None) -> str:
    """根据文件名和上传 Content-Type 推断媒体类型。

    优先使用上传时的 Content-Type（若在白名单中），
    否则根据文件扩展名推断，最后回退到 application/octet-stream。

    Args:
        filename: 原始文件名。
        content_type: 上传请求中的 Content-Type。

    Returns:
        str: 推断出的媒体类型。
    """
    if content_type and content_type in ALLOWED_MEDIA_TYPES:
        return content_type

    ext: str = os.path.splitext(filename)[1].lower()
    if ext in _EXTENSION_MEDIA_TYPE_MAP:
        return _EXTENSION_MEDIA_TYPE_MAP[ext]

    return "application/octet-stream"


class FileItem(BaseModel):
    """目录条目。"""

    name: str
    type: str  # "file" | "dir"
    size: int | None = None


class BrowseResponse(BaseModel):
    """文件浏览响应。"""

    current_path: str
    parent_path: str | None = None
    items: list[FileItem] = []


@files_router.get("/browse", response_model=BrowseResponse)
async def browse_files(
    current_user: ReadUserDep,
    path: str | None = Query(None, description="要浏览的目录路径，默认为根目录"),
) -> BrowseResponse:
    """列出指定目录下的文件和子目录。

    Args:
        current_user: 当前认证用户（需 flow:read 权限）。
        path: 目录路径。如果为空，返回根目录内容。

    Returns:
        BrowseResponse: 目录内容列表。
    """
    root = os.path.realpath(_BROWSE_ROOT)  # noqa: ASYNC240

    # 解析目标路径
    if path is None or path == "":
        target = root
    else:
        target = os.path.realpath(os.path.join(root, path))  # noqa: ASYNC240
        # 安全检查：使用 Path.is_relative_to 确保目标在根目录内
        # 防止符号链接/路径穿越绕过 startswith 检查
        if not Path(target).is_relative_to(root):
            target = root

    if not os.path.isdir(target):  # noqa: ASYNC240
        return BrowseResponse(current_path=target, items=[])

    items: list[FileItem] = []
    try:
        for entry in sorted(os.listdir(target)):
            # 跳过隐藏文件
            if entry.startswith("."):
                continue
            full_path = os.path.join(target, entry)
            # 跳过符号链接（防止链接逃逸）
            if os.path.islink(full_path):  # noqa: ASYNC240
                continue
            # 安全检查：确保解析后路径仍在根目录内
            if not Path(os.path.realpath(full_path)).is_relative_to(root):  # noqa: ASYNC240
                continue
            if os.path.isdir(full_path):  # noqa: ASYNC240
                items.append(FileItem(name=entry, type="dir"))
            elif os.path.isfile(full_path):  # noqa: ASYNC240
                try:
                    size = os.path.getsize(full_path)  # noqa: ASYNC240
                except OSError:
                    size = None
                items.append(FileItem(name=entry, type="file", size=size))
    except PermissionError:
        pass

    # 计算相对根目录的路径
    rel_path = os.path.relpath(target, root)  # noqa: ASYNC240
    if rel_path == ".":
        rel_path = ""

    # 父目录
    parent: str | None = None
    if target != root:
        parent_rel = os.path.relpath(os.path.dirname(target), root)  # noqa: ASYNC240
        parent = "" if parent_rel == "." else parent_rel

    return BrowseResponse(
        current_path=rel_path,
        parent_path=parent,
        items=items,
    )


class UploadResponse(BaseModel):
    """文件上传响应。"""

    artifact_id: str  # MinIO 中的 artifact ID
    filename: str  # 原始文件名
    size: int  # 文件大小（字节）


@files_router.post("/upload", response_model=UploadResponse)
async def upload_file(
    current_user: UploadUserDep,
    service: ArtifactServiceDep,
    file: UploadFile,
) -> UploadResponse:
    """上传文件到 MinIO，返回 artifact_id 供后续使用。

    流程：
    1. 流式读取上传文件内容（分块读取，避免一次性加载大文件到内存，F-21）；
    2. 校验文件大小（上限 100 MiB，读取时即时检查）；
    3. 推断媒体类型（优先 Content-Type，其次文件扩展名）；
    4. 通过 ArtifactService.put_bytes 上传到 MinIO（含去重）；
    5. 返回 artifact_id + 原始文件名 + 大小。

    Args:
        current_user: 当前认证用户（需 artifact:upload 权限）。
        service: 工件服务（DI 注入）。
        file: 上传的文件对象。

    Returns:
        UploadResponse: 上传响应（artifact_id + filename + size）。

    Raises:
        AppError: code="file_too_large"，当文件超过 100 MiB。
        AppError: code="unsupported_media_type"，当媒体类型不在白名单。
    """
    # F-21: 流式分块读取，避免一次性将大文件加载到内存
    _CHUNK_SIZE: int = 1024 * 1024  # 1 MiB chunks
    chunks: list[bytes] = []
    total_size: int = 0

    while True:
        chunk: bytes = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_UPLOAD_SIZE_BYTES:
            raise AppError(
                code="file_too_large",
                message=(f"文件大小超过上限 {MAX_UPLOAD_SIZE_BYTES} 字节（100 MiB）"),
                retryable=False,
                fields={
                    "size_bytes": total_size,
                    "max_size_bytes": MAX_UPLOAD_SIZE_BYTES,
                },
            )
        chunks.append(chunk)

    data: bytes = b"".join(chunks)
    size: int = total_size

    filename: str = file.filename or "unnamed"
    content_type: str | None = file.content_type
    media_type: str = _guess_media_type(filename, content_type)

    if media_type not in ALLOWED_MEDIA_TYPES:
        raise AppError(
            code="unsupported_media_type",
            message=(
                f"不支持的文件类型: {media_type}（文件名: {filename}）。"
                f"允许的类型: pdf, txt, csv, json, xlsx, docx, png, jpg"
            ),
            retryable=False,
            fields={"media_type": media_type, "filename": filename},
        )

    ref = await service.put_bytes(
        data=data,
        media_type=media_type,
        filename=filename,
    )

    return UploadResponse(
        artifact_id=str(ref.artifact_id),
        filename=filename,
        size=size,
    )
