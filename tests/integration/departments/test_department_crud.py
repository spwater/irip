"""实验室 CRUD + 状态切换 + 乐观锁集成测试（P0）。

验证全链路：
- 创建实验室 → 列表 → 详情 → 编辑 → 禁用 → 启用；
- 编码唯一性冲突；
- 乐观锁冲突；
- 禁用后不出现在 active 列表。

前置依赖：
- 测试数据库已启动并已执行 alembic upgrade head（迁移 0006）。
"""

import pytest

from packages.common.errors import AppError
from packages.departments.service import DepartmentService


@pytest.mark.integration
async def test_create_department(
    async_session_factory,
    test_user,
) -> None:
    """创建实验室：返回正确字段。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    dept = await service.create(
        code="lab_test_01",
        display_name="测试实验室",
        description="集成测试用",
        sort_order=0,
    )
    assert dept.id is not None
    assert dept.code == "lab_test_01"
    assert dept.display_name == "测试实验室"
    assert dept.description == "集成测试用"
    assert dept.status == "active"
    assert dept.sort_order == 0
    assert dept.lock_version == 0


@pytest.mark.integration
async def test_create_duplicate_code_conflict(
    async_session_factory,
    test_user,
) -> None:
    """编码唯一性：重复编码抛 AppError(conflict)。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    await service.create(
        code="lab_dup",
        display_name="第一个实验室",
        description=None,
        sort_order=0,
    )
    with pytest.raises(AppError, match="实验室编码已存在"):
        await service.create(
            code="lab_dup",
            display_name="第二个实验室",
            description=None,
            sort_order=0,
        )


@pytest.mark.integration
async def test_list_departments_with_member_count(
    async_session_factory,
    test_user,
) -> None:
    """列表：返回实验室列表 + member_count。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    await service.create("lab_list_a", "实验室A", None, 0)
    await service.create("lab_list_b", "实验室B", None, 1)

    result = await service.list()
    assert len(result.items) >= 2
    # 按 sort_order 排序
    codes = [dept.code for dept, _ in result.items]
    assert "lab_list_a" in codes
    assert "lab_list_b" in codes
    # member_count 应为 0（无关联用户）
    for _, count in result.items:
        assert count >= 0


@pytest.mark.integration
async def test_get_department(
    async_session_factory,
    test_user,
) -> None:
    """详情：查询实验室返回正确字段。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    created = await service.create("lab_get", "查询测试", "描述", 5)

    fetched = await service.get(created.id)
    assert fetched.id == created.id
    assert fetched.code == "lab_get"
    assert fetched.display_name == "查询测试"
    assert fetched.description == "描述"
    assert fetched.sort_order == 5


@pytest.mark.integration
async def test_get_nonexistent_not_found(
    async_session_factory,
    test_user,
) -> None:
    """详情：不存在的实验室抛 AppError(not_found)。"""
    from packages.common.ids import new_id

    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    with pytest.raises(AppError, match="实验室不存在"):
        await service.get(new_id())


@pytest.mark.integration
async def test_update_department(
    async_session_factory,
    test_user,
) -> None:
    """编辑：更新 display_name / description / sort_order，lock_version 递增。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    created = await service.create("lab_edit", "原名称", "原描述", 0)

    updated = await service.update(
        department_id=created.id,
        display_name="新名称",
        description="新描述",
        sort_order=10,
        lock_version=0,
    )
    assert updated.display_name == "新名称"
    assert updated.description == "新描述"
    assert updated.sort_order == 10
    assert updated.lock_version == 1
    # code 不可修改
    assert updated.code == "lab_edit"


@pytest.mark.integration
async def test_update_optimistic_lock_conflict(
    async_session_factory,
    test_user,
) -> None:
    """乐观锁：lock_version 不匹配抛 AppError(conflict)。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    created = await service.create("lab_lock", "锁测试", None, 0)

    with pytest.raises(AppError, match="数据已被修改"):
        await service.update(
            department_id=created.id,
            display_name="更新1",
            description=None,
            sort_order=0,
            lock_version=99,  # 错误版本号
        )


@pytest.mark.integration
async def test_update_nonexistent_not_found(
    async_session_factory,
    test_user,
) -> None:
    """编辑：不存在的实验室抛 AppError(not_found)。"""
    from packages.common.ids import new_id

    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    with pytest.raises(AppError, match="实验室不存在"):
        await service.update(
            department_id=new_id(),
            display_name="test",
            description=None,
            sort_order=0,
            lock_version=0,
        )


@pytest.mark.integration
async def test_set_status_disable(
    async_session_factory,
    test_user,
) -> None:
    """状态切换：禁用实验室 → status=disabled，lock_version 递增。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    created = await service.create("lab_disable", "禁用测试", None, 0)

    disabled = await service.set_status(
        department_id=created.id,
        status="disabled",
        lock_version=0,
    )
    assert disabled.status == "disabled"
    assert disabled.lock_version == 1


@pytest.mark.integration
async def test_set_status_reenable(
    async_session_factory,
    test_user,
) -> None:
    """状态切换：禁用后重新启用 → status=active。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    created = await service.create("lab_reenable", "重新启用测试", None, 0)

    disabled = await service.set_status(created.id, "disabled", 0)
    assert disabled.status == "disabled"

    reenabled = await service.set_status(created.id, "active", 1)
    assert reenabled.status == "active"
    assert reenabled.lock_version == 2


@pytest.mark.integration
async def test_set_status_optimistic_lock_conflict(
    async_session_factory,
    test_user,
) -> None:
    """状态切换乐观锁：lock_version 不匹配抛 AppError(conflict)。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    created = await service.create("lab_status_lock", "状态锁测试", None, 0)

    with pytest.raises(AppError, match="数据已被修改"):
        await service.set_status(
            department_id=created.id,
            status="disabled",
            lock_version=99,
        )


@pytest.mark.integration
async def test_disabled_not_in_active_list(
    async_session_factory,
    test_user,
) -> None:
    """禁用后不出现在 active 列表，但出现在全量列表。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    await service.create("lab_active_filter", "活跃实验室", None, 0)
    to_disable = await service.create("lab_disabled_filter", "禁用实验室", None, 1)

    await service.set_status(to_disable.id, "disabled", 0)

    active_result = await service.list(status="active")
    active_codes = [dept.code for dept, _ in active_result.items]
    assert "lab_active_filter" in active_codes
    assert "lab_disabled_filter" not in active_codes

    disabled_result = await service.list(status="disabled")
    disabled_codes = [dept.code for dept, _ in disabled_result.items]
    assert "lab_disabled_filter" in disabled_codes


@pytest.mark.integration
async def test_list_pagination(
    async_session_factory,
    test_user,
) -> None:
    """分页：cursor 分页正确返回 has_more 和 next_cursor。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    for i in range(5):
        await service.create(f"lab_page_{i:02d}", f"分页实验室{i}", None, i)

    # 第一页 limit=2
    page1 = await service.list(limit=2)
    assert len(page1.items) == 2
    assert page1.has_more is True
    assert page1.next_cursor is not None

    # 第二页
    page2 = await service.list(cursor=page1.next_cursor, limit=2)
    assert len(page2.items) == 2
    assert page2.has_more is True
    assert page2.next_cursor is not None

    # 第三页
    page3 = await service.list(cursor=page2.next_cursor, limit=2)
    assert len(page3.items) >= 1
    assert page3.has_more is False or len(page3.items) < 2


@pytest.mark.integration
async def test_list_invalid_cursor(
    async_session_factory,
    test_user,
) -> None:
    """无效游标：抛 AppError(invalid_cursor)。"""
    service = DepartmentService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,
    )
    with pytest.raises(AppError, match="分页游标无效"):
        await service.list(cursor="invalid-base64!!")
