"""权限矩阵与授权服务单元测试（实施计划 Task 5 Step 1/4）。

覆盖：
- 7 角色权限矩阵（BUILTIN_ROLES 完整性）；
- 角色级权限检查（require_permission 逻辑）；
- 对象级授权：子对象可见 / 兄弟拒绝；
- user grant vs role grant 优先级；
- 过期 grant 拒绝；
- 审计脱敏（敏感字段替换）。
"""



from packages.audit.redaction import redact
from packages.auth.permissions import (
    BUILTIN_ROLES,
    Permission,
    RoleCode,
    get_role_permissions,
    has_role_permission,
)

# ============================================================
# 1. 角色权限矩阵测试（纯 Python，无需数据库）
# ============================================================


class TestRolePermissionMatrix:
    """5 个内置角色的权限矩阵完整性测试。"""

    def test_builtin_roles_has_five_roles(self) -> None:
        """BUILTIN_ROLES 包含且仅包含 5 个角色。"""
        assert len(BUILTIN_ROLES) == 5

    def test_all_role_codes_present(self) -> None:
        """5 个角色代码全部存在。"""
        expected_codes = {
            "platform_administrator",
            "platform_auditor",
            "lab_director",
            "lab_member",
            "lab_viewer",
        }
        assert set(BUILTIN_ROLES.keys()) == expected_codes

    def test_each_role_has_display_name_and_permissions(self) -> None:
        """每个角色定义包含 display_name 和 permissions。"""
        for _code, info in BUILTIN_ROLES.items():
            assert "display_name" in info
            assert "permissions" in info
            assert isinstance(info["display_name"], str)
            assert isinstance(info["permissions"], list)
            assert len(info["permissions"]) > 0

    def test_platform_administrator_has_all_permissions(self) -> None:
        """平台管理员拥有所有权限。"""
        admin_perms = set(BUILTIN_ROLES["platform_administrator"]["permissions"])  # type: ignore[arg-type]
        all_perms = set(Permission.all())
        assert admin_perms == all_perms

    def test_lab_director_permissions(self) -> None:
        """实验室负责人权限：全实验操作 + 管理 + 审批 + 协作。"""
        perms = set(BUILTIN_ROLES["lab_director"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "standard:read",
            "standard:write",
            "standard:publish",
            "fact:read",
            "fact:write",
            "artifact:read",
            "artifact:upload",
            "artifact:download",
            "job:read",
            "job:submit",
            "job:cancel",
            "model:read",
            "model:manage",
            "model:write",
            "model:publish",
            "model:predict",
            "parameter:read",
            "parameter:write",
            "parameter:review",
            "parameter:approve",
            "parameter:publish",
            "department:manage",
            "department:read",
            "equipment:manage",
            "equipment:read",
            "ingestion:read",
            "ingestion:write",
            "ingestion:publish",
            "provenance:read",
            "provenance:write",
            "provenance:publish",
            "component:manage",
            "component:read",
            "flow:manage",
            "flow:execute",
            "flow:read",
            "assistant:use",
            "conversation:create",
            "conversation:invite",
            "conversation:remove_member",
            "conversation:delete",
            "conversation:manage",
            "role:assign",
        }

    def test_lab_member_permissions(self) -> None:
        """实验室成员权限：实验操作 + 只读管理 + 协作。"""
        perms = set(BUILTIN_ROLES["lab_member"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "fact:read",
            "fact:write",
            "artifact:read",
            "artifact:upload",
            "artifact:download",
            "job:read",
            "job:submit",
            "job:cancel",
            "model:read",
            "model:predict",
            "parameter:read",
            "parameter:write",
            "department:read",
            "equipment:read",
            "ingestion:read",
            "ingestion:write",
            "provenance:read",
            "provenance:write",
            "component:read",
            "flow:execute",
            "flow:read",
            "assistant:use",
            "conversation:create",
            "conversation:invite",
            "conversation:delete",
        }

    def test_lab_viewer_permissions(self) -> None:
        """实验室只读成员权限：standard:read + fact:read + artifact:read + job:read
        + model:read + parameter:read + department:read + equipment:read
        + ingestion:read + provenance:read + component:read + flow:read + assistant:use。"""
        perms = set(BUILTIN_ROLES["lab_viewer"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "standard:read",
            "fact:read",
            "artifact:read",
            "job:read",
            "model:read",
            "parameter:read",
            "department:read",
            "equipment:read",
            "ingestion:read",
            "provenance:read",
            "component:read",
            "flow:read",
            "assistant:use",
        }

    def test_lab_viewer_cannot_write(self) -> None:
        """实验室只读成员无任何写权限。"""
        perms = BUILTIN_ROLES["lab_viewer"]["permissions"]  # type: ignore[assignment]
        for p in perms:  # type: ignore[union-attr]
            assert ":write" not in p
            assert ":publish" not in p
            assert ":upload" not in p
            assert ":cancel" not in p
            assert ":manage" not in p
            assert ":assign" not in p

    def test_role_code_enum_values(self) -> None:
        """RoleCode 枚举值与 BUILTIN_ROLES 键一致。"""
        for member in RoleCode:
            assert member.value in BUILTIN_ROLES

    def test_get_role_permissions_returns_list(self) -> None:
        """get_role_permissions 返回权限列表。"""
        perms = get_role_permissions("lab_member")
        assert "fact:read" in perms
        assert "job:submit" in perms
        assert isinstance(perms, list)

    def test_get_role_permissions_unknown_returns_empty(self) -> None:
        """未知角色返回空列表。"""
        assert get_role_permissions("nonexistent") == []

    def test_has_role_permission_true(self) -> None:
        """已知角色+权限返回 True。"""
        assert has_role_permission("lab_member", "fact:read") is True

    def test_has_role_permission_false(self) -> None:
        """角色无该权限返回 False。"""
        assert has_role_permission("lab_viewer", "fact:write") is False

    def test_has_role_permission_unknown_role(self) -> None:
        """未知角色返回 False。"""
        assert has_role_permission("nonexistent", "fact:read") is False


# ============================================================
# 2. 脱敏测试（纯 Python，无需数据库）
# ============================================================


class TestRedaction:
    """审计脱敏函数测试。"""

    def test_audit_redacts_credentials(self) -> None:
        """计划骨架：password 脱敏，value 保留。"""
        assert redact({"password": "secret", "value": 3}) == {
            "password": "[REDACTED]",
            "value": 3,
        }

    def test_redact_token(self) -> None:
        """token 字段脱敏。"""
        assert redact({"token": "abc123"}) == {"token": "[REDACTED]"}

    def test_redact_secret(self) -> None:
        """secret 字段脱敏。"""
        assert redact({"secret": "key"}) == {"secret": "[REDACTED]"}

    def test_redact_api_key(self) -> None:
        """api_key 字段脱敏。"""
        assert redact({"api_key": "key123"}) == {"api_key": "[REDACTED]"}

    def test_redact_refresh_token(self) -> None:
        """refresh_token 字段脱敏。"""
        assert redact({"refresh_token": "rt123"}) == {"refresh_token": "[REDACTED]"}

    def test_redact_access_token(self) -> None:
        """access_token 字段脱敏。"""
        assert redact({"access_token": "at123"}) == {"access_token": "[REDACTED]"}

    def test_redact_case_insensitive(self) -> None:
        """不区分大小写匹配。"""
        assert redact({"Password": "x"}) == {"Password": "[REDACTED]"}
        assert redact({"TOKEN": "x"}) == {"TOKEN": "[REDACTED]"}
        assert redact({"Api_Key": "x"}) == {"Api_Key": "[REDACTED]"}

    def test_redact_preserves_non_sensitive(self) -> None:
        """非敏感字段原样保留。"""
        payload = {"name": "test", "count": 42, "active": True, "data": None}
        result = redact(payload)
        assert result == payload

    def test_redact_nested_dict(self) -> None:
        """嵌套字典递归脱敏。"""
        payload = {"outer": {"password": "secret", "name": "ok"}}
        result = redact(payload)
        assert result == {"outer": {"password": "[REDACTED]", "name": "ok"}}

    def test_redact_nested_list_of_dicts(self) -> None:
        """列表中的字典元素递归脱敏。"""
        payload = {"items": [{"token": "a"}, {"name": "b"}]}
        result = redact(payload)
        assert result == {"items": [{"token": "[REDACTED]"}, {"name": "b"}]}

    def test_redact_empty_dict(self) -> None:
        """空字典返回空字典。"""
        assert redact({}) == {}

    def test_redact_does_not_modify_original(self) -> None:
        """不修改原始字典。"""
        original = {"password": "secret", "name": "test"}
        redact(original)
        assert original == {"password": "secret", "name": "test"}

    def test_redact_multiple_sensitive_fields(self) -> None:
        """多个敏感字段同时脱敏。"""
        payload = {"password": "p", "token": "t", "secret": "s", "name": "n"}
        result = redact(payload)
        assert result == {
            "password": "[REDACTED]",
            "token": "[REDACTED]",
            "secret": "[REDACTED]",
            "name": "n",
        }


# ============================================================
# 3. 对象级授权测试（已移除 — scope_grant 表在迁移 0036 中删除）
# ============================================================
# 以下 14 个 TestObjectScopeAuthorization 测试依赖 scope_grant 表，
# 该表已在迁移 0036_remove_scope_grant_add_department 中删除。
# 对象级授权功能已移除，角色级权限（BUILTIN_ROLES）仍在使用。
# 如需恢复对象级授权，需重建 scope_grant 表及相关 ORM。
