"""AI 协作权限矩阵 + 授权硬编码放行单元测试（irip-ai-collab）。

覆盖（P0-10）：
- Permission 新增 conversation:* / account:* 常量；
- Permission.all() 包含新增权限；
- BUILTIN_ROLES 权限矩阵：lab_director / lab_member 新增协作权限，lab_viewer 无协作权限；
- platform_administrator 拥有全部权限（含新增）；
- require_permission 硬编码放行 account:profile / account:password（不查角色）。
纯 Python，无需数据库。
"""

from uuid import uuid4

import pytest

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.auth.permissions import (
    BUILTIN_ROLES,
    Permission,
    has_role_permission,
)


class TestNewPermissionConstants:
    """新增权限常量测试。"""

    def test_conversation_permissions_exist(self) -> None:
        """conversation:* 权限常量存在。"""
        assert Permission.CONVERSATION_CREATE == "conversation:create"
        assert Permission.CONVERSATION_INVITE == "conversation:invite"
        assert Permission.CONVERSATION_REMOVE_MEMBER == "conversation:remove_member"
        assert Permission.CONVERSATION_DELETE == "conversation:delete"
        assert Permission.CONVERSATION_MANAGE == "conversation:manage"

    def test_account_permissions_exist(self) -> None:
        """account:* 权限常量存在。"""
        assert Permission.ACCOUNT_PROFILE == "account:profile"
        assert Permission.ACCOUNT_PASSWORD == "account:password"

    def test_all_includes_new_permissions(self) -> None:
        """Permission.all() 包含所有新增权限。"""
        all_perms = set(Permission.all())
        assert Permission.CONVERSATION_CREATE in all_perms
        assert Permission.CONVERSATION_INVITE in all_perms
        assert Permission.CONVERSATION_REMOVE_MEMBER in all_perms
        assert Permission.CONVERSATION_DELETE in all_perms
        assert Permission.CONVERSATION_MANAGE in all_perms
        assert Permission.ACCOUNT_PROFILE in all_perms
        assert Permission.ACCOUNT_PASSWORD in all_perms

    def test_all_permissions_unique(self) -> None:
        """所有权限字符串唯一。"""
        all_perms = Permission.all()
        assert len(all_perms) == len(set(all_perms))


class TestBuiltinRolesCollaborationPermissions:
    """BUILTIN_ROLES 协作权限矩阵测试。"""

    def test_lab_director_has_all_conversation_permissions(self) -> None:
        """lab_director 拥有全部 conversation:* 权限 + role:assign。"""
        perms = set(BUILTIN_ROLES["lab_director"]["permissions"])  # type: ignore[arg-type]
        assert Permission.CONVERSATION_CREATE in perms
        assert Permission.CONVERSATION_INVITE in perms
        assert Permission.CONVERSATION_REMOVE_MEMBER in perms
        assert Permission.CONVERSATION_DELETE in perms
        assert Permission.CONVERSATION_MANAGE in perms
        assert Permission.ROLE_ASSIGN in perms

    def test_lab_member_has_partial_conversation_permissions(self) -> None:
        """lab_member 拥有 create/invite/delete，无 remove_member/manage。"""
        perms = set(BUILTIN_ROLES["lab_member"]["permissions"])  # type: ignore[arg-type]
        assert Permission.CONVERSATION_CREATE in perms
        assert Permission.CONVERSATION_INVITE in perms
        assert Permission.CONVERSATION_DELETE in perms
        assert Permission.CONVERSATION_REMOVE_MEMBER not in perms
        assert Permission.CONVERSATION_MANAGE not in perms

    def test_lab_viewer_has_no_conversation_permissions(self) -> None:
        """lab_viewer 无任何 conversation:* 权限（@人通过 assistant:use）。"""
        perms = set(BUILTIN_ROLES["lab_viewer"]["permissions"])  # type: ignore[arg-type]
        assert Permission.CONVERSATION_CREATE not in perms
        assert Permission.CONVERSATION_INVITE not in perms
        assert Permission.CONVERSATION_REMOVE_MEMBER not in perms
        assert Permission.CONVERSATION_DELETE not in perms
        assert Permission.CONVERSATION_MANAGE not in perms
        # lab_viewer 仍保留 assistant:use
        assert Permission.ASSISTANT_USE in perms

    def test_lab_member_keeps_assistant_use(self) -> None:
        """lab_member 保留 assistant:use。"""
        perms = set(BUILTIN_ROLES["lab_member"]["permissions"])  # type: ignore[arg-type]
        assert Permission.ASSISTANT_USE in perms

    def test_lab_director_keeps_assistant_use(self) -> None:
        """lab_director 保留 assistant:use。"""
        perms = set(BUILTIN_ROLES["lab_director"]["permissions"])  # type: ignore[arg-type]
        assert Permission.ASSISTANT_USE in perms

    def test_platform_administrator_has_all_new_permissions(self) -> None:
        """platform_administrator 拥有全部新增权限。"""
        admin_perms = set(BUILTIN_ROLES["platform_administrator"]["permissions"])  # type: ignore[arg-type]
        all_perms = set(Permission.all())
        assert admin_perms == all_perms

    def test_has_role_permission_for_conversation_invite(self) -> None:
        """has_role_permission 对协作权限正确判定。"""
        assert has_role_permission("lab_director", "conversation:invite") is True
        assert has_role_permission("lab_member", "conversation:invite") is True
        assert has_role_permission("lab_viewer", "conversation:invite") is False

    def test_has_role_permission_for_role_assign(self) -> None:
        """has_role_permission 对 role:assign 正确判定。"""
        assert has_role_permission("lab_director", "role:assign") is True
        assert has_role_permission("lab_member", "role:assign") is False
        assert has_role_permission("lab_viewer", "role:assign") is False


class TestRequirePermissionHardcodedPassthrough:
    """require_permission 硬编码放行测试（P0-10）。

    account:profile / account:password 不通过角色分配，所有登录用户均可访问。
    """

    def _make_user(self, roles: list[str]) -> CurrentUser:
        return CurrentUser(
            user_id=uuid4(),
            email="test@irip.local",
            roles=roles,
            department_id=uuid4(),
        )

    def test_account_profile_passthrough_for_lab_viewer(self) -> None:
        """account:profile 对 lab_viewer 放行（无该权限但硬编码放行）。"""
        dep = require_permission("account:profile")
        user = self._make_user(["lab_viewer"])
        # _dependency 是同步函数
        result = dep(user=user)
        assert result is user

    def test_account_password_passthrough_for_lab_viewer(self) -> None:
        """account:password 对 lab_viewer 放行。"""
        dep = require_permission("account:password")
        user = self._make_user(["lab_viewer"])
        result = dep(user=user)
        assert result is user

    def test_account_profile_passthrough_for_lab_member(self) -> None:
        """account:profile 对 lab_member 放行。"""
        dep = require_permission("account:profile")
        user = self._make_user(["lab_member"])
        result = dep(user=user)
        assert result is user

    def test_conversation_invite_rejected_for_lab_viewer(self) -> None:
        """conversation:invite 对 lab_viewer 拒绝（非硬编码放行）。"""
        from packages.common.errors import AppError

        dep = require_permission("conversation:invite")
        user = self._make_user(["lab_viewer"])
        with pytest.raises(AppError) as exc_info:
            dep(user=user)
        assert exc_info.value.code == "forbidden"

    def test_conversation_invite_allowed_for_lab_director(self) -> None:
        """conversation:invite 对 lab_director 放行（通过角色矩阵）。"""
        dep = require_permission("conversation:invite")
        user = self._make_user(["lab_director"])
        result = dep(user=user)
        assert result is user

    def test_role_assign_allowed_for_lab_director(self) -> None:
        """role:assign 对 lab_director 放行。"""
        dep = require_permission("role:assign")
        user = self._make_user(["lab_director"])
        result = dep(user=user)
        assert result is user

    def test_role_assign_rejected_for_lab_member(self) -> None:
        """role:assign 对 lab_member 拒绝。"""
        from packages.common.errors import AppError

        dep = require_permission("role:assign")
        user = self._make_user(["lab_member"])
        with pytest.raises(AppError) as exc_info:
            dep(user=user)
        assert exc_info.value.code == "forbidden"
