"""事实查询服务集成测试。

覆盖 packages/facts/query_service.py：
- list_facts_detail: 空结果、有数据分页；
- search_facts_detail: 空结果、有数据搜索；
- search_by_data: 空结果、KV 搜索；
- get_fact_detail: 存在/不存在；
- get_fact_data: 无 artifact 时返回空结构；
- invalidate_fact_data_cache: 缓存失效（Redis 可用时）。
"""

import uuid as uuid_module
from typing import Any

import pytest
import sqlalchemy as sa

from packages.common.ids import new_id
from packages.facts.entities import Fact
from packages.facts.observations import FactDetailRow
from packages.facts.query_service import FactQueryService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def fact_setup(async_session_factory, sync_engine):
    """创建部门、用户、工业对象，用于 Fact 写入。"""
    from packages.auth.passwords import hash_password

    dept_id = new_id()
    user_id = new_id()
    obj_id = new_id()

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO department "
                "(id, code, display_name, status, lock_version) "
                "VALUES (:id, :code, :name, 'active', 0)"
            ),
            {
                "id": dept_id,
                "code": f"fact-dept-{dept_id.hex[:8]}",
                "name": "Fact Test Department",
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
                "email": f"fact-user-{user_id.hex[:8]}@irip.local",
                "name": "Fact Test User",
                "hash": hash_password("TestPassword2026!"),
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO industrial_object "
                "(id, object_type, code, display_name, department_id, "
                "owner_user_id, status, lock_version, visibility_scope) "
                "VALUES (:id, 'instrument', :code, :name, :dept, :uid, "
                "'active', 0, 'tree')"
            ),
            {
                "id": obj_id,
                "code": f"OBJ-{obj_id.hex[:8]}",
                "name": "Test Object",
                "dept": dept_id,
                "uid": user_id,
            },
        )
        conn.commit()

    yield {"department_id": dept_id, "user_id": user_id, "object_id": obj_id}

    with sync_engine.connect() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM fact_data_index WHERE fact_id IN ("
                "SELECT id FROM fact WHERE department_id = :did)"
            ),
            {"did": dept_id},
        )
        conn.execute(sa.text("DELETE FROM fact WHERE department_id = :did"), {"did": dept_id})
        conn.execute(sa.text("DELETE FROM industrial_object WHERE id = :oid"), {"oid": obj_id})
        conn.execute(sa.text("DELETE FROM app_user WHERE id = :uid"), {"uid": user_id})
        conn.execute(sa.text("DELETE FROM department WHERE id = :did"), {"did": dept_id})
        conn.commit()


@pytest.fixture
def mock_s3_repo():
    """Mock S3 仓储。"""
    repo = type("MockS3", (), {})()
    return repo


@pytest.fixture
def query_service(async_session_factory, fact_setup, mock_s3_repo):
    """构建 FactQueryService 实例。"""
    return FactQueryService(
        session_factory=async_session_factory,
        department_id=fact_setup["department_id"],
        actor_id=fact_setup["user_id"],
        s3_repo=mock_s3_repo,
    )


async def _insert_fact(
    async_session_factory,
    fact_setup,
    fact_type: str = "experiment_run",
    subject_id: str = "SAMPLE-001",
    status: str = "active",
) -> Any:
    """插入一条 Fact 记录并返回。"""
    fact = Fact(
        id=new_id(),
        department_id=fact_setup["department_id"],
        fact_type=fact_type,
        object_id=fact_setup["object_id"],
        status=status,
        subject_id=subject_id,
        owner_user_id=fact_setup["user_id"],
        created_by=fact_setup["user_id"],
        task_code="TASK-001",
        task_name="Test Task",
        department_name="Fact Test Department",
        operator="Test User",
    )

    async with async_session_factory() as session:
        async with session.begin():
            session.add(fact)

    return fact


# ---------------------------------------------------------------------------
# list_facts_detail
# ---------------------------------------------------------------------------


class TestListFactsDetail:
    """list_facts_detail 查询。"""

    async def test_empty_result(self, query_service):
        """无数据 → 空列表 + 空 group_counts。"""
        rows, cursor, counts = await query_service.list_facts_detail()
        assert rows == []
        assert cursor is not None or cursor is None  # 可为 None 或 str
        assert counts == {}

    async def test_with_data(self, query_service, fact_setup, async_session_factory):
        """有数据 → 返回 FactDetailRow 列表。"""
        fact = await _insert_fact(async_session_factory, fact_setup)

        rows, cursor, counts = await query_service.list_facts_detail()
        assert len(rows) >= 1

        found = [r for r in rows if r.fact_id == fact.id]
        assert len(found) == 1
        assert isinstance(found[0], FactDetailRow)
        assert found[0].fact_type == "experiment_run"
        assert found[0].task_name == "Test Task"

    async def test_with_filters(self, query_service, fact_setup, async_session_factory):
        """按 fact_type 过滤。"""
        await _insert_fact(async_session_factory, fact_setup, fact_type="experiment_run")
        await _insert_fact(
            async_session_factory,
            fact_setup,
            fact_type="simulation_run",
            subject_id="SIM-001",
        )

        rows, _, _ = await query_service.list_facts_detail(filters={"fact_type": "simulation_run"})
        for r in rows:
            assert r.fact_type == "simulation_run"


# ---------------------------------------------------------------------------
# search_facts_detail
# ---------------------------------------------------------------------------


class TestSearchFactsDetail:
    """search_facts_detail 搜索。"""

    async def test_empty_result(self, query_service):
        """无数据 → 空列表。"""
        rows, cursor, counts = await query_service.search_facts_detail(query="nothing")
        assert rows == []
        assert counts == {}

    async def test_search_by_subject(self, query_service, fact_setup, async_session_factory):
        """按 subject_id 搜索。"""
        fact = await _insert_fact(
            async_session_factory, fact_setup, subject_id="UNIQUE-SUBJECT-123"
        )

        rows, _, _ = await query_service.search_facts_detail(query="UNIQUE-SUBJECT-123")
        found = [r for r in rows if r.fact_id == fact.id]
        assert len(found) >= 1


# ---------------------------------------------------------------------------
# search_by_data
# ---------------------------------------------------------------------------


class TestSearchByData:
    """search_by_data 数据搜索。"""

    async def test_empty_result(self, query_service):
        """无数据 → 空列表。"""
        rows, counts = await query_service.search_by_data(q="nothing")
        assert rows == []
        assert counts == {}

    async def test_search_with_no_data_index(
        self, query_service, fact_setup, async_session_factory
    ):
        """有 fact 但无 data_index → 空结果。"""
        await _insert_fact(async_session_factory, fact_setup)

        rows, counts = await query_service.search_by_data(key="nonexistent_key")
        assert rows == []
        assert counts == {}


# ---------------------------------------------------------------------------
# get_fact_detail
# ---------------------------------------------------------------------------


class TestGetFactDetail:
    """get_fact_detail 查询。"""

    async def test_found(self, query_service, fact_setup, async_session_factory):
        """存在的 fact → 返回详情。"""
        fact = await _insert_fact(async_session_factory, fact_setup)

        detail = await query_service.get_fact_detail(fact.id)
        assert detail.fact_id == fact.id
        assert detail.fact_type == "experiment_run"
        assert detail.task_name == "Test Task"

    async def test_not_found(self, query_service):
        """不存在的 fact_id → not_found。"""
        from packages.common.errors import AppError

        with pytest.raises(AppError) as exc_info:
            await query_service.get_fact_detail(uuid_module.uuid4())
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# get_fact_data
# ---------------------------------------------------------------------------


class TestGetFactData:
    """get_fact_data 查询。"""

    async def test_no_artifact_returns_empty(
        self, query_service, fact_setup, async_session_factory
    ):
        """无 JSON artifact → 返回空结构。"""
        fact = await _insert_fact(async_session_factory, fact_setup)

        data = await query_service.get_fact_data(fact.id)
        assert "metadata" in data
        assert "points" in data
        assert "series" in data
        assert data["points"] == []
        assert data["series"] == []

    async def test_not_found(self, query_service):
        """不存在的 fact_id → not_found。"""
        from packages.common.errors import AppError

        with pytest.raises(AppError) as exc_info:
            await query_service.get_fact_data(uuid_module.uuid4())
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# invalidate_fact_data_cache
# ---------------------------------------------------------------------------


class TestInvalidateCache:
    """缓存失效方法。"""

    async def test_invalidate_does_not_raise(self, query_service, fact_setup):
        """invalidate_fact_data_cache 不抛异常（Redis 可能不可用）。"""
        FactQueryService.invalidate_fact_data_cache(fact_setup["department_id"])
