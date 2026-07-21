"""机构/实验室权限矩阵单元测试（P0）。

验证（docs/arch-department.md §3.6）：
- BUILTIN_ROLES 中 7 个角色均含 department:read；
- 仅 platform_administrator 含 department:manage（通过 _ALL_PERMISSIONS 自动包含）；
- Permission.all() 包含 DEPARTMENT_MANAGE 和 DEPARTMENT_READ。
"""

from packages.auth.permissions import (
    BUILTIN_ROLES,
    Permission,
    RoleCode,
)


class TestDepartmentPermissions:
    """实验室权限矩阵单元测试。"""

    def test_permission_all_includes_department_permissions(self) -> None:
        """Permission.all() 包含 DEPARTMENT_MANAGE 和 DEPARTMENT_READ。"""
        all_perms = Permission.all()
        assert Permission.DEPARTMENT_MANAGE in all_perms
        assert Permission.DEPARTMENT_READ in all_perms

    def test_department_permission_values(self) -> None:
        """权限常量值正确。"""
        assert Permission.DEPARTMENT_MANAGE == "department:manage"
        assert Permission.DEPARTMENT_READ == "department:read"

    def test_all_seven_roles_have_department_read(self) -> None:
        """7 个内置角色均包含 department:read。"""
        for role_code in RoleCode:
            role_def = BUILTIN_ROLES.get(role_code.value)
            assert role_def is not None, f"角色 {role_code.value} 不在 BUILTIN_ROLES 中"
            permissions = role_def["permissions"]
            assert isinstance(permissions, list)
            assert Permission.DEPARTMENT_READ in permissions, (
                f"角色 {role_code.value} 缺少 department:read 权限"
            )

    def test_only_platform_admin_has_department_manage(self) -> None:
        """仅 platform_administrator 含 department:manage。"""
        for role_code in RoleCode:
            role_def = BUILTIN_ROLES.get(role_code.value)
            assert role_def is not None
            permissions = role_def["permissions"]
            assert isinstance(permissions, list)
            if role_code == RoleCode.PLATFORM_ADMINISTRATOR:
                assert Permission.DEPARTMENT_MANAGE in permissions, (
                    "platform_administrator 应包含 department:manage"
                )
            else:
                assert Permission.DEPARTMENT_MANAGE not in permissions, (
                    f"角色 {role_code.value} 不应包含 department:manage"
                )

    def test_platform_admin_has_all_permissions_including_department(self) -> None:
        """platform_administrator 拥有全部权限（含 department 权限）。"""
        admin_perms = set(BUILTIN_ROLES["platform_administrator"]["permissions"])  # type: ignore[arg-type]
        all_perms = set(Permission.all())
        assert admin_perms == all_perms

    def test_standard_owner_has_department_read(self) -> None:
        """标准负责人含 department:read。"""
        perms = set(BUILTIN_ROLES["standard_owner"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_data_steward_has_department_read(self) -> None:
        """数据管家含 department:read。"""
        perms = set(BUILTIN_ROLES["data_steward"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_researcher_has_department_read(self) -> None:
        """研究员含 department:read。"""
        perms = set(BUILTIN_ROLES["researcher"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_model_engineer_has_department_read(self) -> None:
        """模型工程师含 department:read。"""
        perms = set(BUILTIN_ROLES["model_engineer"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_reviewer_has_department_read(self) -> None:
        """审核员含 department:read。"""
        perms = set(BUILTIN_ROLES["reviewer"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_read_only_user_has_department_read(self) -> None:
        """只读用户含 department:read。"""
        perms = set(BUILTIN_ROLES["read_only_user"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_read_only_user_no_department_manage(self) -> None:
        """只读用户不含 department:manage。"""
        perms = set(BUILTIN_ROLES["read_only_user"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_MANAGE not in perms

    def test_total_permission_count(self) -> None:
        """权限总数 = 20（V0） + 2（department）= 22。"""
        assert len(Permission.all()) == 22
