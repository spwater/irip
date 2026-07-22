"""文件浏览路由：为前端组件参数（如 llm_extractor 的 path）提供服务器端文件选择。

端点：
  GET /api/v1/files/browse?path=xxx  — 列出指定目录内容（flow:read）

安全约定：
- 浏览根目录限制为环境变量 IRIP_FILE_BROWSE_ROOT（默认项目根目录）；
- 解析路径并验证不超出根目录（防止目录穿越）；
- 隐藏文件（.开头）不返回。
"""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission

#: 路由实例。
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

#: 浏览根目录（环境变量或项目根）。
_BROWSE_ROOT = os.environ.get(
    "IRIP_FILE_BROWSE_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)

#: 需 flow:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("flow:read"))]


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
    root = os.path.realpath(_BROWSE_ROOT)

    # 解析目标路径
    if path is None or path == "":
        target = root
    else:
        target = os.path.realpath(os.path.join(root, path))
        # 安全检查：确保目标在根目录内
        if not target.startswith(root):
            target = root

    if not os.path.isdir(target):
        return BrowseResponse(current_path=target, items=[])

    items: list[FileItem] = []
    try:
        for entry in sorted(os.listdir(target)):
            # 跳过隐藏文件
            if entry.startswith("."):
                continue
            full_path = os.path.join(target, entry)
            if os.path.isdir(full_path):
                items.append(FileItem(name=entry, type="dir"))
            elif os.path.isfile(full_path):
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = None
                items.append(FileItem(name=entry, type="file", size=size))
    except PermissionError:
        pass

    # 计算相对根目录的路径
    rel_path = os.path.relpath(target, root)
    if rel_path == ".":
        rel_path = ""

    # 父目录
    parent: str | None = None
    if target != root:
        parent_rel = os.path.relpath(os.path.dirname(target), root)
        parent = "" if parent_rel == "." else parent_rel

    return BrowseResponse(
        current_path=rel_path,
        parent_path=parent,
        items=items,
    )
