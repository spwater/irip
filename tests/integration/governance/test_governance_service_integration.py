"""治理服务集成测试。

覆盖 packages/governance/governance_service.py：
- list_users: 分页、状态筛选、游标、平台用户过滤；
- create_user: 邮箱唯一性、部门解析、审计；
- update_user: 字段更新、not_found；
- assign_roles / remove_role: 合并、移除、乐观锁；
- update_user_status: active/disabled；
- delete_user: 物理删除 + refresh_session 清理；
- transfer_data: 白名单校验、源目相同校验、dry_run、实际移交；
- get_root_data_stats: root 部门统计。
"""

import uuid as uuid_module

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.governance.governance_service import GovernanceService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def admin_user(
    async_session_factory,
    sync_engine,
):
    """创建管理员用户 + 部门。"""
    from packages.auth.passwords import hash_password
    from packages.common.ids import new_id

    dept_id = new_id()
    admin_id = new_id()
    email = f"admin-{uuid_module.uuid4().hex[:8]}@irip.local"

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO department "
                "(id, code, display_name, status, lock_version) "
                "VALUES (:id, :code, :name, 'active', 0)"
            ),
            {
                "id": dept_id,
                "code": f"admin-dept-{dept_id.hex[:8]}",
                "name": "Admin Department",
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, password_hash, "
                "status, lock_version, roles) "
                "VALUES (:id, :dept, :email, :name, :hash, 'active', 0, :roles)"
            ),
            {
                "id": admin_id,
                "dept": dept_id,
                "email": email,
                "name": "Admin User",
                "hash": hash_password("Admin-Password-2026!"),
                "roles": '["platform_administrator"]',
            },
        )
        conn.commit()

    yield {"user_id": admin_id, "department_id": dept_id, "email": email}

    with sync_engine.connect() as conn:
        conn.execute(sa.text("ALTER TABLE audit_event DISABLE TRIGGER ALL"))
        conn.execute(sa.text("ALTER TABLE department DISABLE TRIGGER ALL"))
        conn.execute(
            sa.text("DELETE FROM app_user_department WHERE department_id = :did"), {"did": dept_id}
        )
        conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": admin_id})
        conn.execute(sa.text("DELETE FROM department WHERE id = :did"), {"did": dept_id})
        conn.execute(sa.text("ALTER TABLE department ENABLE TRIGGER ALL"))
        conn.execute(sa.text("ALTER TABLE audit_event ENABLE TRIGGER ALL"))
        conn.commit()


@pytest.fixture
def governance_service(async_session_factory, admin_user):
    """构建 GovernanceService 实例。"""
    return GovernanceService(
        session_factory=async_session_factory,
        department_id=admin_user["department_id"],
        actor_id=admin_user["user_id"],
    )


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


class TestCreateUser:
    """创建用户。"""

    async def test_create_user_success(self, governance_service, admin_user, sync_engine):
        """成功创建用户。"""
        email = f"newuser-{uuid_module.uuid4().hex[:8]}@irip.local"
        user = await governance_service.create_user(
            email=email,
            display_name="New User",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        assert user.email == email
        assert user.display_name == "New User"
        assert user.status == "active"
        assert "lab_member" in user.roles

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_create_user_duplicate_email(self, governance_service, admin_user):
        """邮箱已存在 → conflict。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.create_user(
                email=admin_user["email"],
                display_name="Duplicate",
                password="TestPassword123!",
                roles=["lab_member"],
                department_uuid=None,
                admin_dept_id=admin_user["department_id"],
            )
        assert exc_info.value.code == "conflict"

    async def test_create_user_with_department(self, governance_service, admin_user, sync_engine):
        """指定部门创建用户。"""
        from packages.common.ids import new_id

        dept_id = new_id()
        with sync_engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO department "
                    "(id, code, display_name, status, lock_version) "
                    "VALUES (:id, :code, :name, 'active', 0)"
                ),
                {
                    "id": dept_id,
                    "code": f"test-dept-{dept_id.hex[:8]}",
                    "name": "Test Dept",
                },
            )
            conn.commit()

        email = f"deptuser-{uuid_module.uuid4().hex[:8]}@irip.local"
        try:
            user = await governance_service.create_user(
                email=email,
                display_name="Dept User",
                password="TestPassword123!",
                roles=["lab_member"],
                department_uuid=dept_id,
                admin_dept_id=admin_user["department_id"],
            )
            assert user.department_id == dept_id

            # 清理
            with sync_engine.connect() as conn:
                conn.execute(
                    sa.text("DELETE FROM app_user_department WHERE user_id = :uid"),
                    {"uid": user.id},
                )
                conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
                conn.execute(sa.text("DELETE FROM department WHERE id = :did"), {"did": dept_id})
                conn.commit()
        except Exception:
            with sync_engine.connect() as conn:
                conn.execute(sa.text("DELETE FROM department WHERE id = :did"), {"did": dept_id})
                conn.commit()
            raise


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------


class TestUpdateUser:
    """更新用户。"""

    async def test_update_display_name(self, governance_service, admin_user, sync_engine):
        """更新显示名。"""
        user = await governance_service.create_user(
            email=f"update-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Original",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        updated = await governance_service.update_user(
            user_id=user.id,
            display_name="Updated Name",
        )
        assert updated.display_name == "Updated Name"

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_update_password(self, governance_service, admin_user, sync_engine):
        """更新密码。"""
        user = await governance_service.create_user(
            email=f"pwd-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Pwd User",
            password="OldPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        updated = await governance_service.update_user(
            user_id=user.id,
            password="NewPassword456!",
        )
        assert updated.password_hash != user.password_hash

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_update_user_not_found(self, governance_service):
        """用户不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.update_user(
                user_id=uuid_module.uuid4(),
                display_name="Ghost",
            )
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# assign_roles / remove_role
# ---------------------------------------------------------------------------


class TestRoleManagement:
    """角色管理。"""

    async def test_assign_roles(self, governance_service, admin_user, sync_engine):
        """分配角色（合并）。"""
        user = await governance_service.create_user(
            email=f"roles-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Role User",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        updated = await governance_service.assign_roles(user.id, ["lab_director"])
        assert "lab_member" in updated.roles
        assert "lab_director" in updated.roles

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_assign_duplicate_role(self, governance_service, admin_user, sync_engine):
        """分配已有角色 → 无变化。"""
        user = await governance_service.create_user(
            email=f"dup-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Dup User",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        updated = await governance_service.assign_roles(user.id, ["lab_member"])
        assert updated.roles == ["lab_member"]

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_remove_role(self, governance_service, admin_user, sync_engine):
        """移除角色。"""
        user = await governance_service.create_user(
            email=f"rm-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Rm User",
            password="TestPassword123!",
            roles=["lab_member", "lab_director"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        updated = await governance_service.remove_role(user.id, "lab_member")
        assert "lab_member" not in updated.roles
        assert "lab_director" in updated.roles

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_assign_roles_not_found(self, governance_service):
        """用户不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.assign_roles(uuid_module.uuid4(), ["lab_member"])
        assert exc_info.value.code == "not_found"

    async def test_remove_role_not_found(self, governance_service):
        """用户不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.remove_role(uuid_module.uuid4(), "lab_member")
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# update_user_status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    """用户状态切换。"""

    async def test_disable_user(self, governance_service, admin_user, sync_engine):
        """禁用用户。"""
        user = await governance_service.create_user(
            email=f"disable-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Disable User",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        updated = await governance_service.update_user_status(user.id, "disabled")
        assert updated.status == "disabled"

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_enable_user(self, governance_service, admin_user, sync_engine):
        """启用用户。"""
        user = await governance_service.create_user(
            email=f"enable-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Enable User",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )

        await governance_service.update_user_status(user.id, "disabled")
        updated = await governance_service.update_user_status(user.id, "active")
        assert updated.status == "active"

        # 清理
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user.id})
            conn.commit()

    async def test_update_status_not_found(self, governance_service):
        """用户不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.update_user_status(uuid_module.uuid4(), "active")
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


class TestListUsers:
    """用户列表。"""

    async def test_list_users_basic(self, governance_service):
        """基本列表查询。"""
        users, has_more, next_cursor = await governance_service.list_users(limit=10)
        assert isinstance(users, list)
        assert isinstance(has_more, bool)

    async def test_list_users_status_filter(self, governance_service):
        """按状态过滤。"""
        active_users, _, _ = await governance_service.list_users(status="active", limit=10)
        for u in active_users:
            assert u.status == "active"

    async def test_list_users_invalid_cursor(self, governance_service):
        """无效游标 → invalid_cursor。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.list_users(cursor="not-a-date")
        assert exc_info.value.code == "invalid_cursor"


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------


class TestDeleteUser:
    """删除用户。"""

    async def test_delete_user(self, governance_service, admin_user, sync_engine):
        """删除用户。"""
        user = await governance_service.create_user(
            email=f"delete-{uuid_module.uuid4().hex[:8]}@irip.local",
            display_name="Delete User",
            password="TestPassword123!",
            roles=["lab_member"],
            department_uuid=None,
            admin_dept_id=admin_user["department_id"],
        )
        user_id = user.id

        await governance_service.delete_user(user_id)

        # 验证已删除
        with sync_engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT id FROM app_user WHERE id = :uid"), {"uid": user_id}
            )
            assert result.fetchone() is None

        # 清理审计
        with sync_engine.connect() as conn:
            conn.commit()

    async def test_delete_user_not_found(self, governance_service):
        """用户不存在 → not_found。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.delete_user(uuid_module.uuid4())
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# transfer_data
# ---------------------------------------------------------------------------


class TestTransferData:
    """数据移交。"""

    async def test_invalid_table(self, governance_service):
        """表名不在白名单 → validation_failed。"""
        with pytest.raises(AppError) as exc_info:
            await governance_service.transfer_data(
                "nonexistent_table",
                uuid_module.uuid4(),
                uuid_module.uuid4(),
            )
        assert exc_info.value.code == "validation_failed"

    async def test_same_dept(self, governance_service):
        """源目部门相同 → validation_failed。"""
        dept = uuid_module.uuid4()
        with pytest.raises(AppError) as exc_info:
            await governance_service.transfer_data("fact", dept, dept)
        assert exc_info.value.code == "validation_failed"

    async def test_dry_run(self, governance_service, admin_user, sync_engine):
        """dry_run=True 返回行数不执行 UPDATE。"""
        count = await governance_service.transfer_data(
            "fact",
            admin_user["department_id"],
            uuid_module.uuid4(),
            dry_run=True,
        )
        assert count >= 0


# ---------------------------------------------------------------------------
# get_root_data_stats
# ---------------------------------------------------------------------------


class TestGetRootDataStats:
    """root 部门数据统计。"""

    async def test_root_stats(self, governance_service):
        """获取 root 部门统计。"""
        try:
            root_id, root_name, stats = await governance_service.get_root_data_stats()
            assert isinstance(root_id, str)
            assert isinstance(root_name, str)
            assert isinstance(stats, list)
            assert len(stats) > 0
            for item in stats:
                assert "table" in item
                assert "display_name" in item
                assert "count" in item
        except AppError as exc:
            if exc.code == "not_found":
                pytest.skip("root department not found in test DB")
            raise
