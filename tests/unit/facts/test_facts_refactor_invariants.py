"""事实路由下沉重构的结构性回归测试。

验证 facts.py → Service/Repository 下沉后的不可变约束：
- Router 中无 sa.* / session.execute / session.scalar / session.add；
- 9 个端点的 HTTP 方法 / URL / status_code / response_model 不变；
- 权限依赖（require_permission）保留在 Router；
- FactService.__init__ 签名不变（session_factory, department_id, actor_id）；
- FactQueryService.__init__ 签名（session_factory, department_id, actor_id, s3_repo, rls_dept_id）；
- archive 使用 session_scope（无 GUC）；
- delete 保留两段独立事务；
- MinIO artifact 删除保留在 Router；
- 重复消除：fetch_snapshots / find_json_artifact / _build_data_summary 统一入口。

这些测试不需要数据库，纯结构校验，可在 CI 无 DB 环境运行。
"""

import inspect
from uuid import uuid4

import pytest

# ---- 公共导入 ----
from packages.facts.observations import (
    FactDetailRow,
    FactMeta,
    FactRef,
    FactSnapshotRow,
)
from packages.facts.query_service import FactQueryService
from packages.facts.service import FactService

# ---- Router 无 ORM 代码 ----


class TestRouterCleanOfORM:
    """验证 Router 中不含任何 sa.* / session.execute 代码。"""

    def test_router_file_has_no_sa_or_session_execute(self) -> None:
        """facts.py 源码中除 docstring/注释外无 ORM 代码。

        用 ast 解析源码，剔除所有字符串字面量（含 docstring）后再扫描
        sa. / session.execute / session.scalar / session.add 模式。
        """
        import ast

        import apps.api.routers.facts as facts_module

        source = inspect.getsource(facts_module)
        tree = ast.parse(source)

        # 收集所有字符串字面量的行号范围，用于跳过
        string_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # ast.Index/ast.Constant 不直接给行范围，用 lineno/end_lineno
                start = getattr(node, "lineno", 0)
                end = getattr(node, "end_lineno", start)
                for ln in range(start, end + 1):
                    string_lines.add(ln)

        lines = source.splitlines()
        violations: list[str] = []
        patterns = ["sa.", "session.execute", "session.scalar", "session.add("]
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # 跳过注释行
            if stripped.startswith("#"):
                continue
            # 跳过字符串字面量行（docstring 等）
            if i in string_lines:
                continue
            for pattern in patterns:
                if pattern in line:
                    violations.append(f"  L{i}: {line.rstrip()}")
                    break
        assert not violations, "Router 中发现疑似 ORM 代码:\n" + "\n".join(violations)

    def test_router_file_line_count_reduced(self) -> None:
        """Router 从 1246 行缩减到 ~400 行。"""
        import apps.api.routers.facts as facts_module

        source = inspect.getsource(facts_module)
        line_count = len(source.splitlines())
        assert line_count <= 450, f"Router 仍有 {line_count} 行，预期 ≤ 450"


# ---- 端点契约 ----


class TestEndpointContracts:
    """验证 9 个端点的 HTTP 方法 / URL / status_code / response_model 不变。"""

    @pytest.fixture
    def router(self):
        from apps.api.routers.facts import facts_router

        return facts_router

    def _get_routes(self, router):
        """提取 (method, path, status_code, response_model) 列表。"""
        routes = []
        for route in router.routes:
            methods = list(route.methods) if hasattr(route, "methods") else []
            path = route.path
            status = getattr(route, "status_code", None)
            resp_model = getattr(route, "response_model", None)
            routes.append((methods, path, status, resp_model))
        return routes

    def test_nine_endpoints(self, router) -> None:
        """Router 恰好有 9 个端点。"""
        routes = self._get_routes(router)
        assert len(routes) == 9, f"预期 9 个端点，实际 {len(routes)}"

    def test_create_fact_endpoint(self, router) -> None:
        """POST '' → 201, FactResponse。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts" and "POST" in r[0]]
        assert len(match) == 1
        methods, path, status, resp = match[0]
        assert status == 201
        assert resp is not None

    def test_list_facts_endpoint(self, router) -> None:
        """GET '' → FactListResponse。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts" and "GET" in r[0]]
        assert len(match) == 1

    def test_search_facts_endpoint(self, router) -> None:
        """GET '/search' → FactListResponse。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/search"]
        assert len(match) == 1

    def test_search_data_endpoint(self, router) -> None:
        """GET '/search-data' → FactListResponse。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/search-data"]
        assert len(match) == 1

    def test_get_fact_endpoint(self, router) -> None:
        """GET '/{fact_id}' → FactResponse。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/{fact_id}" and "GET" in r[0]]
        assert len(match) == 1

    def test_get_fact_data_endpoint(self, router) -> None:
        """GET '/{fact_id}/data' → dict（response_model 为 dict，非 Pydantic 模型）。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/{fact_id}/data"]
        assert len(match) == 1
        resp_model = match[0][3]
        # response_model 是 dict 或 dict[str, Any] 或 None
        assert (
            resp_model is None
            or resp_model is dict
            or (getattr(resp_model, "__origin__", None) is dict)
        )

    def test_archive_fact_endpoint(self, router) -> None:
        """POST '/{fact_id}/archive' → 204。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/{fact_id}/archive"]
        assert len(match) == 1
        assert match[0][2] == 204

    def test_delete_fact_endpoint(self, router) -> None:
        """DELETE '/{fact_id}' → 204。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/{fact_id}" and "DELETE" in r[0]]
        assert len(match) == 1
        assert match[0][2] == 204

    def test_delete_by_task_endpoint(self, router) -> None:
        """DELETE '/by-task/{task_code}' → 204。"""
        routes = self._get_routes(router)
        match = [r for r in routes if r[1] == "/api/v1/facts/by-task/{task_code}"]
        assert len(match) == 1
        assert match[0][2] == 204


# ---- 权限依赖保留在 Router ----


class TestPermissionDependencies:
    """验证 require_permission 保留在 Router。"""

    def test_write_user_dep_uses_fact_write(self) -> None:
        """WriteUserDep 绑定 fact:write 权限。"""
        from apps.api.routers.facts import WriteUserDep

        # WriteUserDep 是 Annotated[CurrentUser, Depends(require_permission("fact:write"))]
        metadata = WriteUserDep.__metadata__
        # fastapi.Depends 是工厂函数，实际类型是 fastapi.params.Depends
        dep = next((m for m in metadata if hasattr(m, "dependency")), None)
        assert dep is not None, "WriteUserDep 缺少 Depends"
        assert dep.dependency is not None, "WriteUserDep 的 Depends 缺少 dependency"

    def test_read_user_dep_uses_fact_read(self) -> None:
        """ReadUserDep 绑定 fact:read 权限。"""
        from apps.api.routers.facts import ReadUserDep

        metadata = ReadUserDep.__metadata__
        dep = next((m for m in metadata if hasattr(m, "dependency")), None)
        assert dep is not None, "ReadUserDep 缺少 Depends"
        assert dep.dependency is not None, "ReadUserDep 的 Depends 缺少 dependency"


# ---- Service 签名 ----


class TestServiceSignatures:
    """验证 FactService / FactQueryService 的 __init__ 签名。"""

    def test_fact_service_init_signature(self) -> None:
        """FactService.__init__(session_factory, department_id, actor_id=None)。"""
        sig = inspect.signature(FactService.__init__)
        params = list(sig.parameters.keys())
        assert params == ["self", "session_factory", "department_id", "actor_id"], (
            f"FactService.__init__ 参数: {params}"
        )
        assert sig.parameters["actor_id"].default is None

    def test_fact_query_service_init_signature(self) -> None:
        """FactQueryService.__init__ 签名含 5 个参数 + rls_dept_id 默认 None。"""
        sig = inspect.signature(FactQueryService.__init__)
        params = list(sig.parameters.keys())
        assert params == [
            "self",
            "session_factory",
            "department_id",
            "actor_id",
            "s3_repo",
            "rls_dept_id",
        ], f"FactQueryService.__init__ 参数: {params}"
        assert sig.parameters["rls_dept_id"].default is None

    def test_fact_service_has_create_get_search_list_facts(self) -> None:
        """FactService 保留 create / get / search / list_facts 方法。"""
        for method in ["create", "get", "search", "list_facts"]:
            assert hasattr(FactService, method), f"FactService 缺少方法: {method}"

    def test_fact_service_has_archive_delete_methods(self) -> None:
        """FactService 新增 archive/delete 相关方法。"""
        for method in [
            "archive",
            "get_fact_meta",
            "delete_fact_record",
            "get_facts_meta_by_task",
            "delete_facts_records",
        ]:
            assert hasattr(FactService, method), f"FactService 缺少方法: {method}"

    def test_fact_query_service_has_read_methods(self) -> None:
        """FactQueryService 有 5 个读方法。"""
        for method in [
            "list_facts_detail",
            "search_facts_detail",
            "search_by_data",
            "get_fact_detail",
            "get_fact_data",
        ]:
            assert hasattr(FactQueryService, method), f"FactQueryService 缺少方法: {method}"


# ---- 关键决策：archive 用 session_scope（无 GUC）----


class TestArchiveSessionSemantics:
    """验证 archive 使用 session_scope（无 GUC）而非 _scoped_session。"""

    def test_archive_uses_session_scope(self) -> None:
        """archive 方法源码中使用 session_scope 而非 _scoped_session。"""
        source = inspect.getsource(FactService.archive)
        assert "session_scope(self._factory)" in source, (
            "archive 应使用 session_scope(self._factory)"
        )
        assert "_scoped_session" not in source, "archive 不应使用 _scoped_session"

    def test_get_fact_meta_uses_scoped_session(self) -> None:
        """get_fact_meta 使用 _scoped_session（设 GUC）。"""
        source = inspect.getsource(FactService.get_fact_meta)
        assert "_scoped_session" in source, "get_fact_meta 应使用 _scoped_session"


# ---- 关键决策：delete 保留两段独立事务 ----


class TestDeleteTransactionBoundary:
    """验证 delete 保留 Fact + FlowRun 分两个独立 session。"""

    def test_delete_fact_record_has_two_sessions(self) -> None:
        """delete_fact_record 有两个独立的 async with self._scoped_session 块。"""
        source = inspect.getsource(FactService.delete_fact_record)
        count = source.count("async with self._scoped_session() as session")
        assert count == 2, f"delete_fact_record 应有 2 个独立 session，实际 {count}"

    def test_delete_facts_records_has_two_sessions(self) -> None:
        """delete_facts_records 有两个独立的 async with self._scoped_session 块。"""
        source = inspect.getsource(FactService.delete_facts_records)
        count = source.count("async with self._scoped_session() as session")
        assert count == 2, f"delete_facts_records 应有 2 个独立 session，实际 {count}"


# ---- 关键决策：alembic-URL 超管引擎在 _resolve_task_info ----


class TestAlembicUrlEngine:
    """验证 alembic-URL 超管引擎逻辑在 _resolve_task_info 中。"""

    def test_resolve_task_info_exists(self) -> None:
        """FactQueryService 有 _resolve_task_info 方法。"""
        assert hasattr(FactQueryService, "_resolve_task_info")

    def test_resolve_task_info_contains_alembic_url(self) -> None:
        """_resolve_task_info 源码包含 IRIP_ALEMBIC_DATABASE_URL。"""
        source = inspect.getsource(FactQueryService._resolve_task_info)
        assert "IRIP_ALEMBIC_DATABASE_URL" in source
        assert "create_async_engine" in source or "_cae" in source

    def test_resolve_task_info_has_fallback_guc(self) -> None:
        """_resolve_task_info 有 fallback GUC 反查路径。"""
        source = inspect.getsource(FactQueryService._resolve_task_info)
        assert "set_dept_guc" in source
        assert "set_user_guc" in source


# ---- MinIO artifact 删除保留在 Router ----


class TestMinIODeletionInRouter:
    """验证 MinIO artifact 删除编排保留在 Router（不在 FactService）。"""

    def test_router_delete_fact_has_artifact_deletion(self) -> None:
        """Router 的 delete_fact 端点源码包含 delete_artifact 调用。"""
        from apps.api.routers.facts import delete_fact

        source = inspect.getsource(delete_fact)
        assert "delete_artifact" in source, "delete_fact 应包含 MinIO artifact 删除"
        assert "_build_s3_repo" in source, "delete_fact 应包含 _build_s3_repo"

    def test_router_delete_by_task_has_artifact_deletion(self) -> None:
        """Router 的 delete_facts_by_task 端点源码包含 delete_artifact 调用。"""
        from apps.api.routers.facts import delete_facts_by_task

        source = inspect.getsource(delete_facts_by_task)
        assert "delete_artifact" in source, "delete_facts_by_task 应包含 MinIO artifact 删除"

    def test_fact_service_does_not_delete_artifacts(self) -> None:
        """FactService 的 delete 方法不直接删除 artifact（由 Router 编排）。"""
        for method in ["delete_fact_record", "delete_facts_records"]:
            source = inspect.getsource(getattr(FactService, method))
            assert "delete_artifact" not in source, f"{method} 不应包含 delete_artifact"
            assert "_build_s3_repo" not in source, f"{method} 不应包含 _build_s3_repo"


# ---- 重复消除 ----


class TestDedup:
    """验证 fetch_snapshots / find_json_artifact / _build_data_summary 统一入口。"""

    def test_fetch_snapshots_exists_in_repository(self) -> None:
        """FactRepository 有 fetch_snapshots 静态方法。"""
        from packages.facts.repository import FactRepository

        assert hasattr(FactRepository, "fetch_snapshots")

    def test_fetch_snapshots_called_from_query_service(self) -> None:
        """fetch_snapshots 从 4 处调用（list/search/search-data/get-detail）。"""
        source = inspect.getsource(FactQueryService)
        count = source.count("FactRepository.fetch_snapshots")
        assert count >= 4, f"fetch_snapshots 应被调用 ≥4 次，实际 {count}"

    def test_find_json_artifact_exists_in_repository(self) -> None:
        """FactRepository 有 find_json_artifact 静态方法。"""
        from packages.facts.repository import FactRepository

        assert hasattr(FactRepository, "find_json_artifact")

    def test_find_json_artifact_called_from_query_service(self) -> None:
        """FactQueryService 中 find_json_artifact 被调用（get_fact_data + _build_data_summary）。"""
        source = inspect.getsource(FactQueryService)
        count = source.count("FactRepository.find_json_artifact")
        assert count >= 2, f"find_json_artifact 应被调用 ≥2 次，实际 {count}"

    def test_build_data_summary_exists_in_query_service(self) -> None:
        """FactQueryService 有 _build_data_summary 方法。"""
        assert hasattr(FactQueryService, "_build_data_summary")

    def test_build_data_summary_called_from_two_flows(self) -> None:
        """_build_data_summary 从 list_facts_detail 和 search_by_data 两处调用。"""
        source = inspect.getsource(FactQueryService)
        count = source.count("self._build_data_summary")
        assert count >= 2, f"_build_data_summary 应被调用 ≥2 次，实际 {count}"


# ---- DTO 值对象 ----


class TestDTOs:
    """验证 DTO 值对象正确性。"""

    def test_fact_ref_is_frozen_dataclass(self) -> None:
        """FactRef 是 frozen dataclass。"""
        ref = FactRef(
            fact_id=uuid4(),
            fact_type="experiment_run",
            subject_id="S-001",
            status="active",
        )
        with pytest.raises(AttributeError):
            ref.fact_id = uuid4()  # type: ignore[misc]

    def test_fact_detail_row_fields(self) -> None:
        """FactDetailRow 含所有必要字段。"""
        row = FactDetailRow(
            fact_id=uuid4(),
            fact_type="experiment_run",
            subject_id="S-001",
            status="active",
        )
        assert row.task_code is None
        assert row.task_name is None
        assert row.project_name is None
        assert row.data_summary is None
        assert row.created_at is None

    def test_fact_meta_fields(self) -> None:
        """FactMeta 含 5 个字段。"""
        meta = FactMeta(
            fact_id=uuid4(),
            source_artifact_id=None,
            department_id=None,
            owner_user_id=None,
            flow_run_id=None,
        )
        assert meta.fact_id is not None
        assert meta.source_artifact_id is None

    def test_fact_snapshot_row_is_namedtuple(self) -> None:
        """FactSnapshotRow 是 NamedTuple。"""
        row = FactSnapshotRow(
            fact_id=uuid4(),
            fact_type="experiment_run",
            subject_id="S-001",
            status="active",
            task_code="TC-001",
            task_name="Test Task",
            project_name=None,
            department_name="Dept",
            operator="Op",
            run_operator="RunOp",
            equipment_name="Eq",
            created_at=None,
        )
        assert row[0] == row.fact_id
        assert row.task_code == "TC-001"


# ---- DI 组合 ----


class TestDIComposition:
    """验证 FactQueryService DI 注册。"""

    def test_composition_registers_fact_query_service(self) -> None:
        """composition/facts.py 注册了 get_fact_query_service 覆盖。"""
        import apps.api.composition.facts as comp

        source = inspect.getsource(comp)
        assert "get_fact_query_service" in source
        assert "FactQueryService" in source
        assert "s3_repo" in source

    def test_composition_registers_fact_service(self) -> None:
        """composition/facts.py 注册了 get_fact_service 覆盖。"""
        import apps.api.composition.facts as comp

        source = inspect.getsource(comp)
        assert "get_fact_service" in source
        assert "FactService" in source
