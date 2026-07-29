"""授权依赖：get_authorization_service + require_permission 快捷依赖。

提供 FastAPI Depends 依赖：
- get_authorization_service: 返回 AuthorizationService 实例（DI 覆盖）；
- require_permission(action): 快捷依赖，检查当前用户角色是否拥有
  指定权限（基于 BUILTIN_ROLES 权限矩阵，非对象级授权）。

对象级授权（scope_grant）请在端点内使用
``AuthorizationService.require(user, action, resource)`` 显式检查。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.dependencies.auth import CurrentUser, get_current_user
from packages.auth.permissions import BUILTIN_ROLES
from packages.auth.scope_grants import AuthorizationService
from packages.common.errors import AppError


def get_authorization_service() -> AuthorizationService:
    """获取 AuthorizationService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入带数据库会话的实例。
    """
    raise NotImplementedError(
        "get_authorization_service must be overridden via dependency_overrides"
    )


#: AuthorizationService 依赖类型别名。
AuthorizationServiceDep = Annotated[AuthorizationService, Depends(get_authorization_service)]


def require_permission(action: str):  # type: ignore[no-untyped-def]
    """创建 FastAPI 依赖：检查当前用户角色是否拥有指定权限。

    基于 BUILTIN_ROLES 权限矩阵进行角色级权限检查（非对象级）。
    适用于非资源特定的操作（如 ``user:manage``、``role:assign``）。
    对象级授权请使用 ``AuthorizationService.require()``。

    用法::

        @router.get("/api/v1/users")
        async def list_users(
            user: Annotated[CurrentUser, Depends(require_permission("user:manage"))],
        ) -> ...:
            ...

    Args:
        action: 权限字符串（如 ``"user:manage"``）。

    Returns:
        FastAPI 依赖函数，验证通过返回 CurrentUser，否则抛出 AppError。
    """

    def _dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        for role_code in user.roles:
            role_def = BUILTIN_ROLES.get(role_code)
            if role_def is not None:
                permissions = role_def["permissions"]
                if isinstance(permissions, list) and action in permissions:
                    return user
        raise AppError(
            code="forbidden",
            message="无权执行此操作",
            retryable=False,
            fields={},
        )

    return _dependency
