"""机构/实验室权限矩阵单元测试（P0）。

验证（docs/arch-department.md §3.6）：
- BUILTIN_ROLES 中 5 个角色均含 department:read；
- platform_administrator 和 lab_director 含 department:manage；
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

    def test_all_five_roles_have_department_read(self) -> None:
        """5 个内置角色均包含 department:read。"""
        for role_code in RoleCode:
            role_def = BUILTIN_ROLES.get(role_code.value)
            assert role_def is not None, f"角色 {role_code.value} 不在 BUILTIN_ROLES 中"
            permissions = role_def["permissions"]
            assert isinstance(permissions, list)
            assert Permission.DEPARTMENT_READ in permissions, (
                f"角色 {role_code.value} 缺少 department:read 权限"
            )

    def test_only_admin_and_director_have_department_manage(self) -> None:
        """仅 platform_administrator 和 lab_director 含 department:manage。"""
        for role_code in RoleCode:
            role_def = BUILTIN_ROLES.get(role_code.value)
            assert role_def is not None
            permissions = role_def["permissions"]
            assert isinstance(permissions, list)
            if role_code in (RoleCode.PLATFORM_ADMINISTRATOR, RoleCode.LAB_DIRECTOR):
                assert Permission.DEPARTMENT_MANAGE in permissions, (
                    f"角色 {role_code.value} 应包含 department:manage"
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

    def test_lab_director_has_department_read(self) -> None:
        """实验室负责人含 department:read。"""
        perms = set(BUILTIN_ROLES["lab_director"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_lab_member_has_department_read(self) -> None:
        """实验室成员含 department:read。"""
        perms = set(BUILTIN_ROLES["lab_member"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_lab_viewer_has_department_read(self) -> None:
        """实验室只读成员含 department:read。"""
        perms = set(BUILTIN_ROLES["lab_viewer"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_platform_auditor_has_department_read(self) -> None:
        """平台监督员含 department:read。"""
        perms = set(BUILTIN_ROLES["platform_auditor"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_READ in perms

    def test_lab_member_no_department_manage(self) -> None:
        """实验室成员不含 department:manage。"""
        perms = set(BUILTIN_ROLES["lab_member"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_MANAGE not in perms

    def test_lab_viewer_no_department_manage(self) -> None:
        """实验室只读成员不含 department:manage。"""
        perms = set(BUILTIN_ROLES["lab_viewer"]["permissions"])  # type: ignore[arg-type]
        assert Permission.DEPARTMENT_MANAGE not in perms

    def test_total_permission_count(self) -> None:
        """权限总数 = 51（含实验项目 + 协作功能新增权限）。"""
        assert len(Permission.all()) == 51

    def test_permission_all_includes_equipment_permissions(self) -> None:
        """Permission.all() 包含 EQUIPMENT_MANAGE 和 EQUIPMENT_READ。"""
        all_perms = Permission.all()
        assert Permission.EQUIPMENT_MANAGE in all_perms
        assert Permission.EQUIPMENT_READ in all_perms

    def test_equipment_permission_values(self) -> None:
        """设备权限常量值正确。"""
        assert Permission.EQUIPMENT_MANAGE == "equipment:manage"
        assert Permission.EQUIPMENT_READ == "equipment:read"

    def test_all_five_roles_have_equipment_read(self) -> None:
        """5 个内置角色均包含 equipment:read。"""
        for role_code in RoleCode:
            role_def = BUILTIN_ROLES.get(role_code.value)
            assert role_def is not None, f"角色 {role_code.value} 不在 BUILTIN_ROLES 中"
            permissions = role_def["permissions"]
            assert isinstance(permissions, list)
            assert Permission.EQUIPMENT_READ in permissions, (
                f"角色 {role_code.value} 缺少 equipment:read 权限"
            )

    def test_lab_director_has_equipment_manage(self) -> None:
        """实验室负责人含 equipment:manage。"""
        perms = set(BUILTIN_ROLES["lab_director"]["permissions"])  # type: ignore[arg-type]
        assert Permission.EQUIPMENT_MANAGE in perms

    def test_lab_viewer_no_equipment_manage(self) -> None:
        """实验室只读成员不含 equipment:manage。"""
        perms = set(BUILTIN_ROLES["lab_viewer"]["permissions"])  # type: ignore[arg-type]
        assert Permission.EQUIPMENT_MANAGE not in perms
