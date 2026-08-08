"""账户管理 API 路由：改密码、改头像、改显示名、查个人信息。

端点（irip-ai-collab）：
  GET   /api/v1/account/profile    — 查询个人信息（account:profile 硬编码放行）
  PATCH /api/v1/account/profile    — 修改显示名/头像 URL（account:profile 硬编码放行）
  POST  /api/v1/account/password   — 修改密码（account:password 硬编码放行）
  POST  /api/v1/account/avatar     — 上传头像（account:profile 硬编码放行）

安全约定：
- 所有端点需登录用户（get_current_user）；
- account:profile 和 account:password 权限在 require_permission 中硬编码放行；
- 修改密码时 token_version + 1 使旧 JWT 失效（H-06 机制）；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。
"""

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.auth.service import AuthService
from packages.common.errors import AppError

#: 路由实例。
account_router = APIRouter(prefix="/api/v1/account", tags=["account"])

#: 个人信息/密码权限依赖（硬编码放行，所有登录用户可访问）。
ProfileUserDep = Annotated[CurrentUser, Depends(require_permission("account:profile"))]
PasswordUserDep = Annotated[CurrentUser, Depends(require_permission("account:password"))]


def get_account_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。

    保留用于向后兼容；账户端点的 ORM 操作已下沉到 AuthService。
    """
    raise NotImplementedError(
        "get_account_session_factory must be overridden via dependency_overrides"
    )


AccountSessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_account_session_factory)
]


def get_account_service() -> AuthService:
    """获取 AuthService 实例（由 DI 容器或测试覆盖提供）。

    账户自助操作（改密码、改头像、改显示名）的 ORM 查询已下沉到 AuthService。
    """
    raise NotImplementedError("get_account_service must be overridden via dependency_overrides")


AccountServiceDep = Annotated[AuthService, Depends(get_account_service)]


def get_s3_repo() -> Any:
    """获取 S3 / MinIO 客户端（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_s3_repo must be overridden via dependency_overrides")


S3RepoDep = Annotated[Any, Depends(get_s3_repo)]


# ---- 请求/响应模型 ----


class ProfileResponse(BaseModel):
    """个人信息响应。"""

    id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    roles: list[str] = Field(default_factory=list)
    department_id: str | None = None


class UpdateProfileRequest(BaseModel):
    """修改个人信息请求。"""

    display_name: str | None = Field(None, max_length=100, description="新显示名")
    avatar_url: str | None = Field(None, description="新头像 URL")


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""

    old_password: str = Field(..., min_length=1, max_length=128, description="旧密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码（至少 6 位）")


class AvatarUploadResponse(BaseModel):
    """头像上传响应。"""

    avatar_url: str


# ---- 端点 ----


@account_router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    current_user: ProfileUserDep,
    service: AccountServiceDep,
) -> ProfileResponse:
    """查询当前用户的个人信息。

    account:profile 权限硬编码放行，所有登录用户可访问。

    Args:
        current_user: 当前用户。
        service: 认证服务（ORM 查询已下沉）。

    Returns:
        ProfileResponse: 个人信息（含头像 URL、角色、组织 ID）。
    """
    user = await service.get_user_by_id(current_user.user_id)
    if user is None:
        raise AppError(
            code="not_found",
            message="用户不存在",
            retryable=False,
            fields={},
        )
    return ProfileResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        roles=list(user.roles) if user.roles else [],
        department_id=str(user.department_id) if user.department_id else None,
    )


@account_router.patch("/profile", response_model=ProfileResponse)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: ProfileUserDep,
    service: AccountServiceDep,
) -> ProfileResponse:
    """修改个人信息（显示名 / 头像 URL）。

    account:profile 权限硬编码放行。

    Args:
        body: 修改请求（display_name 和/或 avatar_url）。
        current_user: 当前用户。
        service: 认证服务（ORM 操作已下沉）。

    Returns:
        ProfileResponse: 更新后的个人信息。

    Raises:
        AppError: code="not_found"，用户不存在。
    """
    user = await service.update_profile(
        user_id=current_user.user_id,
        display_name=body.display_name,
        avatar_url=body.avatar_url,
    )
    return ProfileResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        roles=list(user.roles) if user.roles else [],
        department_id=str(user.department_id) if user.department_id else None,
    )


@account_router.post("/password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    current_user: PasswordUserDep,
    service: AccountServiceDep,
) -> None:
    """修改密码。

    account:password 权限硬编码放行。
    修改成功后 token_version + 1，使旧 JWT 失效（需重新登录）。

    Args:
        body: 修改密码请求（旧密码 + 新密码）。
        current_user: 当前用户。
        service: 认证服务（ORM 操作已下沉）。

    Raises:
        AppError: code="invalid_credentials"，旧密码不正确。
        AppError: code="not_found"，用户不存在。
    """
    await service.change_password(
        user_id=current_user.user_id,
        old_password=body.old_password,
        new_password=body.new_password,
    )


@account_router.post("/avatar", response_model=AvatarUploadResponse)
async def upload_avatar(
    file: UploadFile,
    current_user: ProfileUserDep,
    service: AccountServiceDep,
    s3_repo: S3RepoDep,
) -> AvatarUploadResponse:
    """上传头像到 MinIO 并更新用户 avatar_url。

    account:profile 权限硬编码放行。
    头像存储路径：avatars/{user_id}/{timestamp}.{ext}
    限制：jpg/png/gif，< 2MB。

    Args:
        file: 上传的图片文件。
        current_user: 当前用户。
        service: 认证服务（DB 写入已下沉）。
        s3_repo: S3 / MinIO 客户端。

    Returns:
        AvatarUploadResponse: 头像访问 URL。

    Raises:
        AppError: code="validation_failed"，文件类型/大小不符合要求。
    """
    # 校验文件类型
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/jpg"}
    content_type = file.content_type or ""
    if content_type not in allowed_types:
        raise AppError(
            code="validation_failed",
            message="仅支持 JPG/PNG/GIF 格式",
            retryable=False,
            fields={"content_type": content_type},
        )

    # 校验文件大小（< 2MB）
    file_data = await file.read()
    max_size = 2 * 1024 * 1024
    if len(file_data) > max_size:
        raise AppError(
            code="validation_failed",
            message="头像文件不能超过 2MB",
            retryable=False,
            fields={"size": str(len(file_data))},
        )

    # 生成对象 key
    ext_map = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
    }
    ext = ext_map.get(content_type, "jpg")
    timestamp = int(time.time())
    object_key = f"avatars/{current_user.user_id}/{timestamp}.{ext}"

    # 上传到 MinIO（同步操作用 asyncio.to_thread 包装）
    await asyncio.to_thread(
        s3_repo.put_object,
        object_key,
        file_data,
        content_type,
    )

    # 生成预签名 URL（有效期 7 天）
    avatar_url = await asyncio.to_thread(
        s3_repo.presigned_get,
        object_key,
        7 * 24 * 3600,
    )

    # 更新数据库（ORM 操作已下沉到 AuthService）
    await service.set_avatar_url(current_user.user_id, avatar_url)

    return AvatarUploadResponse(avatar_url=avatar_url)


# ============================================================
# P2-I12: GDPR 数据导出 / 删除
# ============================================================


@account_router.get("/export")
async def export_user_data(
    current_user: ProfileUserDep,
    service: AccountServiceDep,
) -> dict[str, Any]:
    """导出当前用户的全部数据（GDPR 数据可携带权）。

    account:profile 权限硬编码放行。
    返回用户个人数据 + 关联数据摘要（审计日志、对话、事实等）。

    Args:
        current_user: 当前用户。
        service: 认证服务。

    Returns:
        dict: 用户数据导出包。
    """
    user = await service.get_user_by_id(current_user.user_id)
    if user is None:
        raise AppError(code="not_found", message="用户不存在", retryable=False)

    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "roles": list(user.roles) if user.roles else [],
            "department_id": str(user.department_id) if user.department_id else None,
            "status": user.status,
            "created_at": str(user.created_at) if hasattr(user, "created_at") else None,
            "updated_at": str(user.updated_at) if hasattr(user, "updated_at") else None,
        },
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "此导出包含您的个人数据。关联的实验数据（facts/artifacts）"
        "因归属部门而非个人，不包含在此导出中。",
    }


class DeleteAccountRequest(BaseModel):
    """删除账户请求。"""

    confirm_email: str = Field(..., description="需输入邮箱确认删除操作")
    password: str = Field(..., description="需输入密码确认删除操作")


@account_router.delete("", status_code=204)
async def delete_account(
    body: DeleteAccountRequest,
    current_user: PasswordUserDep,
    service: AccountServiceDep,
) -> None:
    """删除用户账户（GDPR 被遗忘权）。

    account:password 权限硬编码放行。
    需邮箱 + 密码双重确认。

    删除策略：
    - 用户账户：标记为 deleted（软删除，保留 ID 用于审计追溯）
    - 个人数据：display_name → "已删除用户"，email → 匿名化
    - 关联数据（facts/artifacts/flows）：因归属部门，不删除
    - 审计日志：保留（合规要求），actor_user_id 保留
    - AI 对话：删除用户创建的对话
    - token_version + 1：使所有 JWT 立即失效

    Args:
        body: 删除请求（邮箱 + 密码确认）。
        current_user: 当前用户。
        service: 认证服务。

    Raises:
        AppError: code="validation_failed"，邮箱或密码不匹配。
    """
    user = await service.get_user_by_id(current_user.user_id)
    if user is None:
        raise AppError(code="not_found", message="用户不存在", retryable=False)

    # 双重确认：邮箱 + 密码
    if body.confirm_email != user.email:
        raise AppError(
            code="validation_failed",
            message="邮箱不匹配，请输入正确的邮箱以确认删除",
            retryable=False,
            fields={"confirm_email": "邮箱不匹配"},
        )

    # 验证密码
    if not service.verify_password(user, body.password):
        raise AppError(
            code="invalid_credentials",
            message="密码不正确",
            retryable=False,
            fields={"password": "密码不正确"},
        )

    # 执行删除（软删除 + 匿名化）
    await service.delete_account(current_user.user_id)
