"""权限矩阵与授权服务单元测试（实施计划 Task 5 Step 1/4）。

覆盖：
- 7 角色权限矩阵（BUILTIN_ROLES 完整性）；
- 角色级权限检查（require_permission 逻辑）；
- 对象级授权：子对象可见 / 兄弟拒绝；
- user grant vs role grant 优先级；
- 过期 grant 拒绝；
- 审计脱敏（敏感字段替换）。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.redaction import redact
from packages.auth.permissions import (
    BUILTIN_ROLES,
    Permission,
    RoleCode,
    get_role_permissions,
    has_role_permission,
)
from packages.auth.scope_grants import (
    AuthorizationService,
    ResourceRef,
    ScopeGrant,
)
from packages.common.clock import FixedClock, SystemClock
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from tests.unit.auth.conftest import AuthTestUser, DbHelper, KilnResource

# ============================================================
# 1. 角色权限矩阵测试（纯 Python，无需数据库）
# ============================================================


class TestRolePermissionMatrix:
    """7 个内置角色的权限矩阵完整性测试。"""

    def test_builtin_roles_has_seven_roles(self) -> None:
        """BUILTIN_ROLES 包含且仅包含 7 个角色。"""
        assert len(BUILTIN_ROLES) == 7

    def test_all_role_codes_present(self) -> None:
        """7 个角色代码全部存在。"""
        expected_codes = {
            "platform_administrator",
            "standard_owner",
            "data_steward",
            "researcher",
            "model_engineer",
            "reviewer",
            "read_only_user",
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

    def test_standard_owner_permissions(self) -> None:
        """标准负责人权限：standard:read/write/publish + department:read。"""
        perms = set(BUILTIN_ROLES["standard_owner"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "standard:read",
            "standard:write",
            "standard:publish",
            "department:read",
        }

    def test_data_steward_permissions(self) -> None:
        """数据管家权限：fact:read/write + artifact:read/upload/download + department:read。"""
        perms = set(BUILTIN_ROLES["data_steward"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "fact:read",
            "fact:write",
            "artifact:read",
            "artifact:upload",
            "artifact:download",
            "department:read",
        }

    def test_researcher_permissions(self) -> None:
        """研究员权限：fact:read + artifact:read/download + job:read/submit + department:read。"""
        perms = set(BUILTIN_ROLES["researcher"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "fact:read",
            "artifact:read",
            "artifact:download",
            "job:read",
            "job:submit",
            "department:read",
        }

    def test_model_engineer_permissions(self) -> None:
        """模型工程师权限：model:read/write/publish/predict + department:read。"""
        perms = set(BUILTIN_ROLES["model_engineer"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "model:read",
            "model:write",
            "model:publish",
            "model:predict",
            "department:read",
        }

    def test_reviewer_permissions(self) -> None:
        """审核员权限：parameter:read/review/approve + department:read。"""
        perms = set(BUILTIN_ROLES["reviewer"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "parameter:read",
            "parameter:review",
            "parameter:approve",
            "department:read",
        }

    def test_read_only_user_permissions(self) -> None:
        """只读用户权限：fact:read + standard:read + parameter:read + department:read。"""
        perms = set(BUILTIN_ROLES["read_only_user"]["permissions"])  # type: ignore[arg-type]
        assert perms == {
            "fact:read",
            "standard:read",
            "parameter:read",
            "department:read",
        }

    def test_read_only_user_cannot_write(self) -> None:
        """只读用户无任何写权限。"""
        perms = BUILTIN_ROLES["read_only_user"]["permissions"]  # type: ignore[assignment]
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
        perms = get_role_permissions("researcher")
        assert "fact:read" in perms
        assert "job:submit" in perms
        assert isinstance(perms, list)

    def test_get_role_permissions_unknown_returns_empty(self) -> None:
        """未知角色返回空列表。"""
        assert get_role_permissions("nonexistent") == []

    def test_has_role_permission_true(self) -> None:
        """已知角色+权限返回 True。"""
        assert has_role_permission("researcher", "fact:read") is True

    def test_has_role_permission_false(self) -> None:
        """角色无该权限返回 False。"""
        assert has_role_permission("researcher", "fact:write") is False

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
# 3. 对象级授权测试（需数据库）
# ============================================================


class TestObjectScopeAuthorization:
    """对象级授权：scope_grant 匹配与拒绝。"""

    async def test_child_object_is_visible_but_sibling_is_denied(
        self,
        authz: AuthorizationService,
        researcher: AuthTestUser,
        kiln: KilnResource,
        cooler: ResourceRef,
    ) -> None:
        """计划骨架：子对象可见，兄弟拒绝。"""
        await authz.require(researcher, "fact:read", kiln.child_measurement_point)
        with pytest.raises(AppError, match="无权访问"):
            await authz.require(researcher, "fact:read", cooler)

    async def test_no_grant_is_denied(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """无任何授权时拒绝。"""
        org = new_id()
        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            user = AuthTestUser(
                user_id=new_id(),
                email="noperm@irip.local",
                roles=["researcher"],
            )
            resource = ResourceRef(
                organization_id=org, object_id=new_id(), resource_type="fact"
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_user_grant_allows_without_role_grant(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """user 直连 grant 授权通过（无 role grant）。"""
        org = new_id()
        obj_id = new_id()
        user = AuthTestUser(
            user_id=new_id(),
            email="user-grant@irip.local",
            roles=[],
        )
        db_helper.insert_user_sync(user.user_id, user.email)

        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=user.user_id,
                role_id=None,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="fact"
            )
            await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)
            db_helper.cleanup_user_sync(user.user_id)

    async def test_role_grant_allows_without_user_grant(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """role grant 授权通过（无 user 直连 grant）。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="role-grant@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="fact"
            )
            await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_expired_grant_is_denied(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """过期 grant（effective_to 在过去）拒绝。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        past = datetime.now(UTC) - timedelta(hours=1)
        user = AuthTestUser(
            user_id=new_id(),
            email="expired@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
                effective_from=None,
                effective_to=past,
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="fact"
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_future_effective_grant_is_denied(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """未生效 grant（effective_from 在未来）拒绝。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        future = datetime.now(UTC) + timedelta(hours=1)
        user = AuthTestUser(
            user_id=new_id(),
            email="future@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
                effective_from=future,
                effective_to=None,
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="fact"
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_org_wide_grant_allows_any_object(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """object_root_id 为 NULL（全组织通配）时，任何对象均可访问。"""
        org = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="org-wide@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=None,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            for _ in range(3):
                resource = ResourceRef(
                    organization_id=org, object_id=new_id(), resource_type="fact"
                )
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_wildcard_resource_type_matches_any(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """resource_type="*" 通配符匹配任意资源类型。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="wildcard@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="*",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="artifact"
            )
            await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_wrong_organization_denied(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """不同 organization_id 的 grant 不匹配。"""
        org_a = new_id()
        org_b = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="cross-org@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org_a,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org_b, object_id=obj_id, resource_type="fact"
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org_a)

    async def test_wrong_action_denied(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """action 不匹配的 grant 拒绝。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="wrong-action@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="fact"
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:write", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_object_id_none_org_level_operation(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """object_id 为 None（组织级操作）时，只有全组织 grant 匹配。"""
        org = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="org-level@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=None,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=None, resource_type="fact"
            )
            await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_object_id_none_with_specific_grant_denied(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """object_id 为 None 时，特定对象 grant 不匹配。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        user = AuthTestUser(
            user_id=new_id(),
            email="org-level-denied@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=None, resource_type="fact"
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)

    async def test_user_with_no_roles_uses_user_grant_only(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """无角色的用户只能使用 user 直连 grant。"""
        org = new_id()
        obj_id = new_id()
        user = AuthTestUser(
            user_id=new_id(),
            email="no-roles@irip.local",
            roles=[],
        )
        db_helper.insert_user_sync(user.user_id, user.email)

        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=user.user_id,
                role_id=None,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
            )
            session.add(grant)
            await session.flush()

        try:
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(session=session, clock=SystemClock())
            resource = ResourceRef(
                organization_id=org, object_id=obj_id, resource_type="fact"
            )
            await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)
            db_helper.cleanup_user_sync(user.user_id)

    async def test_fixed_clock_controls_effective_range(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
        db_helper: DbHelper,
    ) -> None:
        """FixedClock 控制生效区间检查。"""
        org = new_id()
        obj_id = new_id()
        role_id = db_helper.get_role_id_sync("researcher")
        base_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        user = AuthTestUser(
            user_id=new_id(),
            email="clock-test@irip.local",
            roles=["researcher"],
        )
        async with session_scope(async_session_factory) as session:
            grant = ScopeGrant(
                id=new_id(),
                user_id=None,
                role_id=role_id,
                organization_id=org,
                object_root_id=obj_id,
                resource_type="fact",
                action="fact:read",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                effective_to=datetime(2026, 1, 31, tzinfo=UTC),
            )
            session.add(grant)
            await session.flush()

        resource = ResourceRef(
            organization_id=org, object_id=obj_id, resource_type="fact"
        )
        try:
            # 在生效区间内 → 允许
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(
                session=session, clock=FixedClock(base_time)
            )
            await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()

            # 在生效区间之前 → 拒绝
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(
                session=session,
                clock=FixedClock(datetime(2025, 12, 31, tzinfo=UTC)),
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()

            # 在生效区间之后 → 拒绝
            session = async_session_factory()
            await session.begin()
            service = AuthorizationService(
                session=session,
                clock=FixedClock(datetime(2026, 2, 1, tzinfo=UTC)),
            )
            with pytest.raises(AppError, match="无权访问"):
                await service.require(user, "fact:read", resource)
            await session.rollback()
            await session.close()
        finally:
            db_helper.cleanup_grants_by_org_sync(org)
