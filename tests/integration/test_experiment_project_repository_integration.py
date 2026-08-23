"""实验项目仓库集成测试。

覆盖 packages/experiment_project/repository.py：
- insert / select_by_id / select_by_dept_and_code；
- select_list: 分页、部门筛选、状态筛选、游标；
- update / update_status: 乐观锁；
- delete: 物理删除；
- batch_count_flows_and_facts / batch_owner_names: 批量查询空列表；
- count_flows_by_project / count_facts_by_project: 无关联返回 0。
"""

import uuid as uuid_module

import pytest
import sqlalchemy as sa

from packages.common.ids import new_id
from packages.experiment_project.entities import ExperimentProject
from packages.experiment_project.repository import ExperimentProjectRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def dept_and_user(async_session_factory, sync_engine):
    """创建部门 + 用户。"""
    dept_id = new_id()
    user_id = new_id()
    from packages.auth.passwords import hash_password

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO department "
                "(id, code, display_name, status, lock_version) "
                "VALUES (:id, :code, :name, 'active', 0)"
            ),
            {
                "id": dept_id,
                "code": f"ep-dept-{dept_id.hex[:8]}",
                "name": "EP Test Department",
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO app_user "
                "(id, department_id, email, display_name, password_hash, "
                "status, lock_version) "
                "VALUES (:id, :dept, :email, :name, :hash, 'active', 0)"
            ),
            {
                "id": user_id,
                "dept": dept_id,
                "email": f"ep-user-{user_id.hex[:8]}@irip.local",
                "name": "EP Test User",
                "hash": hash_password("TestPassword2026!"),
            },
        )
        conn.commit()

    yield {"department_id": dept_id, "user_id": user_id}

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM experiment_project WHERE department_id = :did"),
            {"did": dept_id},
        )
        conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user_id})
        conn.execute(sa.text("DELETE FROM department WHERE id = :did"), {"did": dept_id})
        conn.commit()


def _make_project(dept_id, user_id, code=None):
    """构建实验项目实例。"""
    return ExperimentProject(
        id=new_id(),
        department_id=dept_id,
        code=code or f"PROJ-{uuid_module.uuid4().hex[:8]}",
        display_name="Test Project",
        description="A test project",
        status="active",
        visible_departments=[],
        visibility_scope="tree",
        owner_user_id=user_id,
    )


# ---------------------------------------------------------------------------
# insert / select_by_id
# ---------------------------------------------------------------------------


class TestInsertSelectById:
    """INSERT 和 SELECT by ID。"""

    async def test_insert_and_select(self, async_session_factory, dept_and_user):
        """插入后能按 ID 查到。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                inserted = await ExperimentProjectRepository.insert(session, project)

            assert inserted.id == project.id
            assert inserted.code == project.code

        async with async_session_factory() as session:
            async with session.begin():
                found = await ExperimentProjectRepository.select_by_id(session, project.id)

        assert found is not None
        assert found.code == project.code

    async def test_select_by_id_not_found(self, async_session_factory):
        """不存在的 ID → None。"""
        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.select_by_id(
                    session, uuid_module.uuid4()
                )
        assert result is None


# ---------------------------------------------------------------------------
# select_by_dept_and_code
# ---------------------------------------------------------------------------


class TestSelectByDeptAndCode:
    """按部门 + 编码查询。"""

    async def test_select_by_dept_and_code(self, async_session_factory, dept_and_user):
        """按部门 + 编码查到项目。"""
        project = _make_project(
            dept_and_user["department_id"], dept_and_user["user_id"], code="UNIQUE-CODE-1"
        )

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                found = await ExperimentProjectRepository.select_by_dept_and_code(
                    session, dept_and_user["department_id"], "UNIQUE-CODE-1"
                )

        assert found is not None
        assert found.code == "UNIQUE-CODE-1"

    async def test_select_by_dept_and_code_not_found(self, async_session_factory, dept_and_user):
        """不存在的编码 → None。"""
        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.select_by_dept_and_code(
                    session, dept_and_user["department_id"], "NONEXISTENT"
                )
        assert result is None


# ---------------------------------------------------------------------------
# select_list
# ---------------------------------------------------------------------------


class TestSelectList:
    """分页列表查询。"""

    async def test_list_with_department_filter(self, async_session_factory, dept_and_user):
        """按部门过滤查询。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                rows = await ExperimentProjectRepository.select_list(
                    session, department_id=dept_and_user["department_id"], limit=20
                )

        assert len(rows) >= 1
        found = [r for r in rows if r[0].id == project.id]
        assert len(found) == 1
        assert found[0][1] == "EP Test Department"

    async def test_list_with_status_filter(self, async_session_factory, dept_and_user):
        """按状态过滤。"""
        active = _make_project(
            dept_and_user["department_id"], dept_and_user["user_id"], code="ACTIVE-1"
        )
        archived = _make_project(
            dept_and_user["department_id"], dept_and_user["user_id"], code="ARCHIVED-1"
        )
        archived.status = "archived"

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, active)
                await ExperimentProjectRepository.insert(session, archived)

        async with async_session_factory() as session:
            async with session.begin():
                active_rows = await ExperimentProjectRepository.select_list(
                    session, department_id=dept_and_user["department_id"], status="active"
                )
                archived_rows = await ExperimentProjectRepository.select_list(
                    session, department_id=dept_and_user["department_id"], status="archived"
                )

        assert all(r[0].status == "active" for r in active_rows)
        assert all(r[0].status == "archived" for r in archived_rows)

    async def test_list_with_cursor(self, async_session_factory, dept_and_user):
        """游标分页。"""
        projects = [
            _make_project(
                dept_and_user["department_id"],
                dept_and_user["user_id"],
                code=f"CUR-{i}-{uuid_module.uuid4().hex[:4]}",
            )
            for i in range(5)
        ]

        async with async_session_factory() as session:
            async with session.begin():
                for p in projects:
                    await ExperimentProjectRepository.insert(session, p)

        async with async_session_factory() as session:
            async with session.begin():
                first_page = await ExperimentProjectRepository.select_list(
                    session, department_id=dept_and_user["department_id"], limit=2
                )

        assert len(first_page) <= 2

        if len(first_page) == 2:
            last = first_page[-1][0]
            async with async_session_factory() as session:
                async with session.begin():
                    second_page = await ExperimentProjectRepository.select_list(
                        session,
                        department_id=dept_and_user["department_id"],
                        limit=2,
                        cursor_created_at=last.created_at,
                        cursor_id=last.id,
                    )
            # 第二页不应包含第一页最后一项
            second_ids = {r[0].id for r in second_page}
            assert last.id not in second_ids

    async def test_list_no_department_filter(self, async_session_factory, dept_and_user):
        """不指定部门 → 返回所有。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                rows = await ExperimentProjectRepository.select_list(session, limit=100)

        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# update / update_status
# ---------------------------------------------------------------------------


class TestUpdate:
    """更新操作。"""

    async def test_update_success(self, async_session_factory, dept_and_user):
        """乐观锁成功更新。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                updated = await ExperimentProjectRepository.update(
                    session,
                    project_id=project.id,
                    display_name="Updated Name",
                    description="Updated desc",
                    lock_version=0,
                )

        assert updated is not None
        assert updated.display_name == "Updated Name"
        assert updated.lock_version == 1

    async def test_update_stale_lock_version(self, async_session_factory, dept_and_user):
        """乐观锁版本不匹配 → 返回 None。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.update(
                    session,
                    project_id=project.id,
                    display_name="Stale",
                    description="stale",
                    lock_version=99,  # 不匹配
                )

        assert result is None

    async def test_update_with_visible_departments(self, async_session_factory, dept_and_user):
        """更新含 visible_departments。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                updated = await ExperimentProjectRepository.update(
                    session,
                    project_id=project.id,
                    display_name="VD Update",
                    description=None,
                    lock_version=0,
                    visible_departments=["dept1", "dept2"],
                )

        assert updated is not None
        assert updated.visible_departments == ["dept1", "dept2"]

    async def test_update_status(self, async_session_factory, dept_and_user):
        """更新状态。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                updated = await ExperimentProjectRepository.update_status(
                    session, project.id, "archived", 0
                )

        assert updated is not None
        assert updated.status == "archived"
        assert updated.lock_version == 1

    async def test_update_status_stale(self, async_session_factory, dept_and_user):
        """状态更新乐观锁不匹配 → None。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.update_status(
                    session, project.id, "archived", 99
                )

        assert result is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    """删除操作。"""

    async def test_delete(self, async_session_factory, dept_and_user):
        """物理删除项目。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.delete(session, project.id)

        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.select_by_id(session, project.id)

        assert result is None


# ---------------------------------------------------------------------------
# count / batch methods
# ---------------------------------------------------------------------------


class TestCountAndBatch:
    """计数与批量查询。"""

    async def test_count_flows_no_data(self, async_session_factory, dept_and_user):
        """无关联 flow_definition → 0。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                count = await ExperimentProjectRepository.count_flows_by_project(
                    session, project.id
                )

        assert count == 0

    async def test_count_facts_no_data(self, async_session_factory, dept_and_user):
        """无关联 fact → 0。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                count = await ExperimentProjectRepository.count_facts_by_project(
                    session, project.id
                )

        assert count == 0

    async def test_batch_count_empty_ids(self, async_session_factory):
        """空 ID 列表 → 空 dict。"""
        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.batch_count_flows_and_facts(session, [])
        assert result == {}

    async def test_batch_count_with_project(self, async_session_factory, dept_and_user):
        """有项目但无关联 → (0, 0)。"""
        project = _make_project(dept_and_user["department_id"], dept_and_user["user_id"])

        async with async_session_factory() as session:
            async with session.begin():
                await ExperimentProjectRepository.insert(session, project)

        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.batch_count_flows_and_facts(
                    session, [project.id]
                )

        assert project.id in result
        assert result[project.id] == (0, 0)

    async def test_batch_owner_names_empty(self, async_session_factory):
        """空用户 ID 列表 → 空 dict。"""
        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.batch_owner_names(session, [])
        assert result == {}

    async def test_batch_owner_names_with_user(self, async_session_factory, dept_and_user):
        """有用户 → 返回 display_name。"""
        async with async_session_factory() as session:
            async with session.begin():
                result = await ExperimentProjectRepository.batch_owner_names(
                    session, [dept_and_user["user_id"]]
                )

        assert dept_and_user["user_id"] in result
        assert result[dept_and_user["user_id"]] == "EP Test User"
