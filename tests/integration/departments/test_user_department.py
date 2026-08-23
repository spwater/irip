"""用户-实验室关联集成测试（P1）。

验证：
- 设置用户实验室关联；
- 查询实验室下用户；
- is_primary 唯一性；
- 移除关联。

前置依赖：
- 测试数据库已启动并已执行 alembic upgrade head（迁移 0006）。
"""

import pytest
import sqlalchemy as sa

from packages.departments.service import DepartmentService
from packages.departments.user_departments import UserDepartmentService


async def _create_test_user(
    session_factory: object,
    email: str,
    org_id: object,
) -> object:
    """插入测试用户，返回 user_id。"""
    from packages.auth.passwords import hash_password
    from packages.common.ids import new_id

    user_id = new_id()
    async with session_factory() as session:  # type: ignore[operator]
        async with session.begin():
            await session.execute(
                sa.text(
                    "INSERT INTO app_user "
                    "(id, department_id, email, display_name, "
                    "password_hash, status, lock_version) "
                    "VALUES (:id, :org, :email, :name, :hash, 'active', 0)"
                ),
                {
                    "id": user_id,
                    "org": org_id,
                    "email": email,
                    "name": "Test Member",
                    "hash": hash_password("Test-Password-2026!"),
                },
            )
    return user_id


async def _cleanup_test_user(session_factory: object, user_id: object) -> None:
    """清理测试用户及其关联。"""
    async with session_factory() as session:  # type: ignore[operator]
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM app_user_department WHERE user_id = :uid"),
                {"uid": user_id},
            )
            await session.execute(
                sa.text("DELETE FROM app_user WHERE id = :uid"),
                {"uid": user_id},
            )


async def _cleanup_departments(session_factory: object, org_id: object, codes: list[str]) -> None:
    """清理测试实验室。

    按 code 删除（parent_id 为 NULL 的顶级实验室）。
    """
    async with session_factory() as session:  # type: ignore[operator]
        async with session.begin():
            await session.execute(
                sa.text(
                    "DELETE FROM app_user_department WHERE department_id IN ("
                    "SELECT id FROM department WHERE parent_id IS NULL "
                    "AND code = ANY(:codes))"
                ),
                {"codes": codes},
            )
            await session.execute(
                sa.text("DELETE FROM department WHERE parent_id IS NULL AND code = ANY(:codes)"),
                {"codes": codes},
            )


@pytest.mark.integration
async def test_set_and_get_user_departments(
    async_session_factory,
    test_user,
) -> None:
    """设置用户实验室关联后查询。"""
    dept_service = DepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )
    ud_service = UserDepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )

    dept1 = await dept_service.create("ud_lab_01", "实验室1", None, 0)
    dept2 = await dept_service.create("ud_lab_02", "实验室2", None, 1)

    user_id = await _create_test_user(
        async_session_factory, "ud_member@irip.local", test_user.department_id
    )

    try:
        await ud_service.set_user_departments(
            user_id=user_id,
            department_ids=[dept1.id, dept2.id],
            primary_department_id=dept1.id,
        )

        user_depts = await ud_service.get_user_departments(user_id)
        assert len(user_depts) == 2
        codes = {ud.department_code for ud in user_depts}
        assert codes == {"ud_lab_01", "ud_lab_02"}

        primary = [ud for ud in user_depts if ud.is_primary]
        assert len(primary) == 1
        assert primary[0].department_id == dept1.id
    finally:
        await _cleanup_test_user(async_session_factory, user_id)
        await _cleanup_departments(
            async_session_factory,
            test_user.department_id,
            ["ud_lab_01", "ud_lab_02"],
        )


@pytest.mark.integration
async def test_get_department_users(
    async_session_factory,
    test_user,
) -> None:
    """查询实验室下用户列表。"""
    dept_service = DepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )
    ud_service = UserDepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )

    dept = await dept_service.create("ud_users_lab", "用户列表实验室", None, 0)
    user1 = await _create_test_user(
        async_session_factory, "ud_user1@irip.local", test_user.department_id
    )
    user2 = await _create_test_user(
        async_session_factory, "ud_user2@irip.local", test_user.department_id
    )

    try:
        await ud_service.set_user_departments(
            user_id=user1,
            department_ids=[dept.id],
            primary_department_id=dept.id,
        )
        await ud_service.set_user_departments(
            user_id=user2,
            department_ids=[dept.id],
            primary_department_id=None,
        )

        users = await ud_service.get_department_users(dept.id)
        assert len(users) == 2
        emails = {u.email for u in users}
        assert emails == {"ud_user1@irip.local", "ud_user2@irip.local"}

        primary_users = [u for u in users if u.is_primary]
        assert len(primary_users) == 1
        assert primary_users[0].user_id == user1
    finally:
        await _cleanup_test_user(async_session_factory, user1)
        await _cleanup_test_user(async_session_factory, user2)
        await _cleanup_departments(async_session_factory, test_user.department_id, ["ud_users_lab"])


@pytest.mark.integration
async def test_is_primary_uniqueness(
    async_session_factory,
    test_user,
) -> None:
    """is_primary 唯一性：同一 user 仅一条 is_primary=true。"""
    dept_service = DepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )
    ud_service = UserDepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )

    dept1 = await dept_service.create("ud_primary_01", "主要测试1", None, 0)
    dept2 = await dept_service.create("ud_primary_02", "主要测试2", None, 1)
    user_id = await _create_test_user(
        async_session_factory, "ud_primary@irip.local", test_user.department_id
    )

    try:
        # 设置两个实验室，primary 为 dept1
        await ud_service.set_user_departments(
            user_id=user_id,
            department_ids=[dept1.id, dept2.id],
            primary_department_id=dept1.id,
        )

        user_depts = await ud_service.get_user_departments(user_id)
        primary = [ud for ud in user_depts if ud.is_primary]
        assert len(primary) == 1
        assert primary[0].department_id == dept1.id

        # 切换 primary 为 dept2
        await ud_service.set_user_departments(
            user_id=user_id,
            department_ids=[dept1.id, dept2.id],
            primary_department_id=dept2.id,
        )

        user_depts = await ud_service.get_user_departments(user_id)
        primary = [ud for ud in user_depts if ud.is_primary]
        assert len(primary) == 1
        assert primary[0].department_id == dept2.id
    finally:
        await _cleanup_test_user(async_session_factory, user_id)
        await _cleanup_departments(
            async_session_factory,
            test_user.department_id,
            ["ud_primary_01", "ud_primary_02"],
        )


@pytest.mark.integration
async def test_remove_department_association(
    async_session_factory,
    test_user,
) -> None:
    """移除关联：从用户实验室列表中移除一个实验室。"""
    dept_service = DepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )
    ud_service = UserDepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )

    dept1 = await dept_service.create("ud_remove_01", "保留实验室", None, 0)
    dept2 = await dept_service.create("ud_remove_02", "移除实验室", None, 1)
    user_id = await _create_test_user(
        async_session_factory, "ud_remove@irip.local", test_user.department_id
    )

    try:
        await ud_service.set_user_departments(
            user_id=user_id,
            department_ids=[dept1.id, dept2.id],
            primary_department_id=dept1.id,
        )

        # 移除 dept2
        await ud_service.set_user_departments(
            user_id=user_id,
            department_ids=[dept1.id],
            primary_department_id=dept1.id,
        )

        user_depts = await ud_service.get_user_departments(user_id)
        assert len(user_depts) == 1
        assert user_depts[0].department_id == dept1.id

        # dept2 下用户列表应为空
        users = await ud_service.get_department_users(dept2.id)
        assert len(users) == 0
    finally:
        await _cleanup_test_user(async_session_factory, user_id)
        await _cleanup_departments(
            async_session_factory,
            test_user.department_id,
            ["ud_remove_01", "ud_remove_02"],
        )


@pytest.mark.integration
async def test_member_count_aggregation(
    async_session_factory,
    test_user,
) -> None:
    """member_count 聚合：列表正确返回成员数。"""
    dept_service = DepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )
    ud_service = UserDepartmentService(
        session_factory=async_session_factory,
        department_id=test_user.department_id,
    )

    dept = await dept_service.create("ud_count_lab", "计数实验室", None, 0)
    user1 = await _create_test_user(
        async_session_factory, "ud_count1@irip.local", test_user.department_id
    )
    user2 = await _create_test_user(
        async_session_factory, "ud_count2@irip.local", test_user.department_id
    )

    try:
        await ud_service.set_user_departments(
            user_id=user1,
            department_ids=[dept.id],
            primary_department_id=dept.id,
        )
        await ud_service.set_user_departments(
            user_id=user2,
            department_ids=[dept.id],
            primary_department_id=None,
        )

        result = await dept_service.list_all(limit=100)
        dept_item = next(
            (d for d, c, _, _ in result.items if d.code == "ud_count_lab"),
            None,
        )
        assert dept_item is not None
        count = next(c for d, c, _, _ in result.items if d.code == "ud_count_lab")
        assert count == 2
    finally:
        await _cleanup_test_user(async_session_factory, user1)
        await _cleanup_test_user(async_session_factory, user2)
        await _cleanup_departments(async_session_factory, test_user.department_id, ["ud_count_lab"])
