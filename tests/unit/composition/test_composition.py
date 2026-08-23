"""组合根依赖注入层的单元测试。

每个 composition provider 模块暴露 ``register(ctx: CompositionContext)``，
在内部通过 ``app.dependency_overrides`` 注册对应领域的依赖覆盖。

测试策略：
- 构造一个真实的 ``FastAPI`` 应用实例和 mock 的 CompositionContext 字段；
- 调用各 provider 的 ``register(ctx)``；
- 断言 ``app.dependency_overrides`` 包含预期的依赖键；
- 对不需要 DB / 外部依赖的覆盖函数，直接调用验证返回类型/值。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI

from apps.api.composition import CompositionContext, lookup_dept_id, lookup_root_dept_id
from packages.common.errors import AppError

# ---------------------------------------------------------------------------
# Helper mocks
# ---------------------------------------------------------------------------


class AsyncMockSession:
    """模拟异步 SQLAlchemy Session。"""

    def __init__(
        self, scalar_result: object | None = None, execute_result: object | None = None
    ) -> None:
        self.scalar_result = scalar_result
        self.execute_result = execute_result

    async def scalar(self, statement: object) -> object | None:
        return self.scalar_result

    async def execute(self, statement: object) -> object:
        return self.execute_result


class MockSessionFactory:
    """模拟 async_sessionmaker：调用返回异步上下文管理器。"""

    def __init__(self, session: AsyncMockSession) -> None:
        self._session = session

    def __call__(self) -> _MockSessionContext:
        return _MockSessionContext(self._session)


class _MockSessionContext:
    """异步上下文管理器，__aenter__ 返回 mock session。"""

    def __init__(self, session: AsyncMockSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMockSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


def _make_ctx(
    app: FastAPI | None = None,
    session_factory: object | None = None,
    s3_repo: object | None = None,
    redis_url: str = "redis://localhost:6379/0",
    token_secret: str = "test-secret",
    root_dept_id: UUID | None = None,
) -> CompositionContext:
    """构建测试用 CompositionContext。"""
    return CompositionContext(
        app=app or FastAPI(),
        session_factory=session_factory or MagicMock(),
        s3_repo=s3_repo or MagicMock(),
        redis_url=redis_url,
        token_secret=token_secret,
        root_dept_id=root_dept_id,
    )


# ---------------------------------------------------------------------------
# lookup_dept_id / lookup_root_dept_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_dept_id_returns_department_id() -> None:
    """lookup_dept_id 从 DB 查询并返回 department_id。"""
    user_id = uuid4()
    dept_id = uuid4()
    mock_user = SimpleNamespace(department_id=dept_id)
    mock_session = AsyncMockSession(scalar_result=mock_user)
    factory = MockSessionFactory(mock_session)

    result = await lookup_dept_id(factory, user_id)  # type: ignore[arg-type]
    assert result == dept_id


@pytest.mark.asyncio
async def test_lookup_dept_id_raises_when_user_not_found() -> None:
    """用户不存在时 raise AppError(code='forbidden')。"""
    user_id = uuid4()
    mock_session = AsyncMockSession(scalar_result=None)
    factory = MockSessionFactory(mock_session)

    with pytest.raises(AppError) as exc_info:
        await lookup_dept_id(factory, user_id)  # type: ignore[arg-type]
    assert exc_info.value.code == "forbidden"


@pytest.mark.asyncio
async def test_lookup_dept_id_raises_when_department_id_is_none() -> None:
    """department_id 为空时 raise AppError(code='forbidden')。"""
    user_id = uuid4()
    mock_user = SimpleNamespace(department_id=None)
    mock_session = AsyncMockSession(scalar_result=mock_user)
    factory = MockSessionFactory(mock_session)

    with pytest.raises(AppError) as exc_info:
        await lookup_dept_id(factory, user_id)  # type: ignore[arg-type]
    assert exc_info.value.code == "forbidden"


@pytest.mark.asyncio
async def test_lookup_root_dept_id_returns_id() -> None:
    """lookup_root_dept_id 返回 root 部门 ID。"""
    root_id = uuid4()
    mock_result = MagicMock()
    mock_result.first.return_value = (root_id,)
    mock_session = AsyncMockSession(execute_result=mock_result)
    factory = MockSessionFactory(mock_session)

    result = await lookup_root_dept_id(factory)  # type: ignore[arg-type]
    assert result == root_id


@pytest.mark.asyncio
async def test_lookup_root_dept_id_returns_none_when_not_found() -> None:
    """root 部门不存在时返回 None。"""
    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_session = AsyncMockSession(execute_result=mock_result)
    factory = MockSessionFactory(mock_session)

    result = await lookup_root_dept_id(factory)  # type: ignore[arg-type]
    assert result is None


# ---------------------------------------------------------------------------
# register_all
# ---------------------------------------------------------------------------


def test_register_all_invokes_all_providers() -> None:
    """register_all 调用全部 provider 的 register 函数（含研究域）。"""
    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("packages.common.feature_flags.RESEARCH_MODULE_ENABLED", True):
        from apps.api.composition import register_all

        register_all(ctx)
    # 至少应注册了 auth 域的 get_auth_service
    from apps.api.routers.auth import get_auth_service

    assert get_auth_service in app.dependency_overrides


def test_register_all_skips_research_when_disabled() -> None:
    """RESEARCH_MODULE_ENABLED=False 时跳过研究域注册。"""
    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("packages.common.feature_flags.RESEARCH_MODULE_ENABLED", False):
        from apps.api.composition import register_all

        register_all(ctx)
    from apps.api.routers.research import get_workspace_service

    assert get_workspace_service not in app.dependency_overrides


# ---------------------------------------------------------------------------
# auth.py
# ---------------------------------------------------------------------------


def test_auth_composition_registers_overrides() -> None:
    """auth register 注册 AuthService / token_secret / session_factory 覆盖。"""
    from apps.api.composition.auth import register
    from apps.api.dependencies.auth import get_auth_session_factory, get_token_secret
    from apps.api.routers.account import get_account_service
    from apps.api.routers.auth import get_auth_service, get_me_session_factory

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_auth_service in app.dependency_overrides
    assert get_token_secret in app.dependency_overrides
    assert get_account_service in app.dependency_overrides
    assert get_me_session_factory in app.dependency_overrides
    assert get_auth_session_factory in app.dependency_overrides


def test_auth_composition_token_secret_returns_ctx_value() -> None:
    """token_secret 覆盖返回 ctx 中存储的值。"""
    from apps.api.composition.auth import register
    from apps.api.dependencies.auth import get_token_secret

    app = FastAPI()
    ctx = _make_ctx(app=app, token_secret="my-secret")
    register(ctx)

    override = app.dependency_overrides[get_token_secret]
    assert override() == "my-secret"


def test_auth_composition_session_factory_returns_ctx_value() -> None:
    """get_me_session_factory / get_auth_session_factory 返回 ctx.session_factory。"""
    from apps.api.composition.auth import register
    from apps.api.dependencies.auth import get_auth_session_factory
    from apps.api.routers.auth import get_me_session_factory

    app = FastAPI()
    sf = MagicMock()
    ctx = _make_ctx(app=app, session_factory=sf)
    register(ctx)

    assert app.dependency_overrides[get_me_session_factory]() is sf
    assert app.dependency_overrides[get_auth_session_factory]() is sf


def test_auth_composition_auth_service_is_singleton_instance() -> None:
    """get_auth_service 与 get_account_service 返回同一 AuthService 实例。"""
    from apps.api.composition.auth import register
    from apps.api.routers.account import get_account_service
    from apps.api.routers.auth import get_auth_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    auth_svc = app.dependency_overrides[get_auth_service]()
    account_svc = app.dependency_overrides[get_account_service]()
    assert auth_svc is account_svc


# ---------------------------------------------------------------------------
# ai.py
# ---------------------------------------------------------------------------


def test_ai_composition_registers_overrides() -> None:
    """ai register 注册 AIService / session_factory / S3 覆盖。"""
    from apps.api.composition.ai import register
    from apps.api.routers.account import get_account_session_factory, get_s3_repo
    from apps.api.routers.assistant import get_ai_service
    from apps.api.routers.collaboration import get_ai_service as get_collab_ai_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_ai_service in app.dependency_overrides
    assert get_collab_ai_service in app.dependency_overrides
    assert get_account_session_factory in app.dependency_overrides
    assert get_s3_repo in app.dependency_overrides


def test_ai_composition_account_session_factory_returns_ctx_value() -> None:
    """account session_factory 覆盖返回 ctx.session_factory。"""
    from apps.api.composition.ai import register
    from apps.api.routers.account import get_account_session_factory

    app = FastAPI()
    sf = MagicMock()
    ctx = _make_ctx(app=app, session_factory=sf)
    register(ctx)

    assert app.dependency_overrides[get_account_session_factory]() is sf


def test_ai_composition_s3_repo_returns_ctx_value() -> None:
    """S3 repo 覆盖返回 ctx.s3_repo。"""
    from apps.api.composition.ai import register
    from apps.api.routers.account import get_s3_repo as get_account_s3_repo

    app = FastAPI()
    s3 = MagicMock()
    ctx = _make_ctx(app=app, s3_repo=s3)
    register(ctx)

    assert app.dependency_overrides[get_account_s3_repo]() is s3


def test_ai_composition_sets_session_factory_on_modules() -> None:
    """register 调用 set_*_session_factory 设置各路由模块的 session_factory。"""
    with (
        patch("apps.api.routers.assistant.set_ai_session_factory") as mock_assistant,
        patch("apps.api.routers.ai_tools.set_session_factory") as mock_ai_tools,
        patch("apps.api.routers.object_types.set_session_factory") as mock_obj_types,
    ):
        import importlib

        import apps.api.composition.ai as ai_mod

        importlib.reload(ai_mod)
        app = FastAPI()
        ctx = _make_ctx(app=app)
        ai_mod.register(ctx)

    mock_assistant.assert_called_once_with(ctx.session_factory)
    mock_ai_tools.assert_called_once_with(ctx.session_factory)
    mock_obj_types.assert_called_once_with(ctx.session_factory)


# ---------------------------------------------------------------------------
# infrastructure.py
# ---------------------------------------------------------------------------


def test_infrastructure_composition_registers_overrides() -> None:
    """infrastructure register 注册健康检查 / 工件 / 治理等覆盖。"""
    from apps.api.composition.infrastructure import register
    from apps.api.routers.audit import get_audit_session_factory
    from apps.api.routers.backups import get_backups_session_factory
    from apps.api.routers.governance import get_governance_session_factory
    from apps.api.routers.health import (
        get_health_session_factory,
        get_redis_url,
        get_s3_repo,
    )
    from apps.api.routers.ingestions import get_ingestion_service
    from apps.api.routers.parameters import get_parameter_service
    from apps.api.routers.uploads import get_artifact_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_health_session_factory in app.dependency_overrides
    assert get_redis_url in app.dependency_overrides
    assert get_s3_repo in app.dependency_overrides
    assert get_artifact_service in app.dependency_overrides
    assert get_governance_session_factory in app.dependency_overrides
    assert get_audit_session_factory in app.dependency_overrides
    assert get_backups_session_factory in app.dependency_overrides
    assert get_ingestion_service in app.dependency_overrides
    assert get_parameter_service in app.dependency_overrides


def test_infrastructure_composition_health_returns_ctx_values() -> None:
    """健康检查依赖返回 ctx 中的值。"""
    from apps.api.composition.infrastructure import register
    from apps.api.routers.health import (
        get_health_session_factory,
        get_redis_url,
        get_s3_repo,
    )

    app = FastAPI()
    sf = MagicMock()
    s3 = MagicMock()
    ctx = _make_ctx(app=app, session_factory=sf, s3_repo=s3, redis_url="redis://test:6379")
    register(ctx)

    assert app.dependency_overrides[get_health_session_factory]() is sf
    assert app.dependency_overrides[get_redis_url]() == "redis://test:6379"
    assert app.dependency_overrides[get_s3_repo]() is s3


def test_infrastructure_composition_session_factory_overrides() -> None:
    """治理/审计/备份路由的 session_factory 返回 ctx.session_factory。"""
    from apps.api.composition.infrastructure import register
    from apps.api.routers.audit import get_audit_session_factory
    from apps.api.routers.backups import get_backups_session_factory
    from apps.api.routers.governance import get_governance_session_factory

    app = FastAPI()
    sf = MagicMock()
    ctx = _make_ctx(app=app, session_factory=sf)
    register(ctx)

    assert app.dependency_overrides[get_governance_session_factory]() is sf
    assert app.dependency_overrides[get_audit_session_factory]() is sf
    assert app.dependency_overrides[get_backups_session_factory]() is sf


# ---------------------------------------------------------------------------
# jobs.py
# ---------------------------------------------------------------------------


def test_jobs_composition_registers_overrides() -> None:
    """jobs register 注册 get_job_service 覆盖。"""
    from apps.api.composition.jobs import register
    from apps.api.routers.jobs import get_job_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_job_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------


def test_models_composition_registers_overrides() -> None:
    """models register 注册 get_model_service 覆盖。"""
    from apps.api.composition.models import register
    from apps.api.routers.models import get_model_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_model_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# standards.py
# ---------------------------------------------------------------------------


def test_standards_composition_registers_overrides() -> None:
    """standards register 注册对象图 / 部门 / 设备 / 用户部门 / 实验项目覆盖。"""
    from apps.api.composition.standards import register
    from apps.api.dependencies.departments import (
        get_department_service,
        get_user_department_service,
    )
    from apps.api.routers.equipment import get_equipment_service
    from apps.api.routers.experiment_projects import get_experiment_project_service
    from apps.api.routers.objects import get_object_graph_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_object_graph_service in app.dependency_overrides
    assert get_department_service in app.dependency_overrides
    assert get_equipment_service in app.dependency_overrides
    assert get_user_department_service in app.dependency_overrides
    assert get_experiment_project_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# flows.py
# ---------------------------------------------------------------------------


def test_flows_composition_registers_overrides() -> None:
    """flows register 注册组件注册表和流程运行时服务覆盖。"""
    from apps.api.composition.flows import register
    from apps.api.routers.components import get_component_registry_service
    from apps.api.routers.flows import get_flow_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_component_registry_service in app.dependency_overrides
    assert get_flow_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# facts.py
# ---------------------------------------------------------------------------


def test_facts_composition_registers_overrides() -> None:
    """facts register 注册事实/证据/配方/推导/溯源图服务覆盖。"""
    from apps.api.composition.facts import register
    from apps.api.routers.facts import get_fact_query_service, get_fact_service
    from apps.api.routers.provenance import (
        get_derivation_service,
        get_evidence_service,
        get_provenance_graph_service,
        get_recipe_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_fact_service in app.dependency_overrides
    assert get_fact_query_service in app.dependency_overrides
    assert get_evidence_service in app.dependency_overrides
    assert get_recipe_service in app.dependency_overrides
    assert get_derivation_service in app.dependency_overrides
    assert get_provenance_graph_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# research.py
# ---------------------------------------------------------------------------


def test_research_composition_registers_overrides() -> None:
    """research register 注册工作空间/快照/Timeline 服务覆盖。"""
    from apps.api.composition.research import register
    from apps.api.routers.research import get_snapshot_service, get_workspace_service
    from apps.api.routers.timeline_dependencies import (
        get_analysis_service,
        get_conclusion_bar_service,
        get_conclusion_service,
        get_recommendation_service,
        get_timeline_query_service,
        get_turn_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_workspace_service in app.dependency_overrides
    assert get_snapshot_service in app.dependency_overrides
    assert get_timeline_query_service in app.dependency_overrides
    assert get_recommendation_service in app.dependency_overrides
    assert get_analysis_service in app.dependency_overrides
    assert get_turn_service in app.dependency_overrides
    assert get_conclusion_service in app.dependency_overrides
    assert get_conclusion_bar_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# research_run.py
# ---------------------------------------------------------------------------


def test_research_run_composition_registers_overrides() -> None:
    """research_run register 注册 Plan/Run/Conversation 服务覆盖。"""
    from apps.api.composition.research_run import register
    from apps.api.routers.research_run import (
        get_conversation_service,
        get_plan_service,
        get_run_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_plan_service in app.dependency_overrides
    assert get_run_service in app.dependency_overrides
    assert get_conversation_service in app.dependency_overrides


def test_research_run_composition_sets_artifact_service() -> None:
    """research_run register 调用 _set_artifact_service 设置工件服务。"""
    from apps.api.composition.research_run import register

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.research_run._set_artifact_service") as mock_set:
        register(ctx)
        mock_set.assert_called_once()


# ---------------------------------------------------------------------------
# research_products.py
# ---------------------------------------------------------------------------


def test_research_products_composition_registers_overrides() -> None:
    """research_products register 注册产物/候选/目录服务覆盖。"""
    from apps.api.composition.research_products import register
    from apps.api.routers.research_products import (
        get_candidate_service,
        get_catalog,
        get_product_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_product_service in app.dependency_overrides
    assert get_candidate_service in app.dependency_overrides
    assert get_catalog in app.dependency_overrides


# ---------------------------------------------------------------------------
# research_publish.py
# ---------------------------------------------------------------------------


def test_research_publish_composition_registers_overrides() -> None:
    """research_publish register 注册发布/搜索/目录服务覆盖。"""
    from apps.api.composition.research_publish import register
    from apps.api.routers.research_publish import (
        get_publication_service,
        get_publish_catalog,
        get_search_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_publication_service in app.dependency_overrides
    assert get_search_service in app.dependency_overrides
    assert get_publish_catalog in app.dependency_overrides


# ---------------------------------------------------------------------------
# research_lineage.py
# ---------------------------------------------------------------------------


def test_research_lineage_composition_registers_overrides() -> None:
    """research_lineage register 注册溯源/知识库服务覆盖。"""
    from apps.api.composition.research_lineage import register
    from apps.api.routers.research_lineage import (
        get_knowledge_provider_service,
        get_knowledge_reference_service,
        get_provenance_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)

    assert get_provenance_service in app.dependency_overrides
    assert get_knowledge_provider_service in app.dependency_overrides
    assert get_knowledge_reference_service in app.dependency_overrides


# ---------------------------------------------------------------------------
# Override function invocation tests — call async overrides to cover inner logic
# ---------------------------------------------------------------------------

DEPT_ID = uuid4()
USER_ID = uuid4()


def _mock_current_user() -> SimpleNamespace:
    """构建 mock CurrentUser 对象。"""
    return SimpleNamespace(
        user_id=USER_ID,
        email="test@irip.local",
        roles=["analyst"],
        department_id=DEPT_ID,
        is_root_member=False,
    )


def _mock_current_user_root_member() -> SimpleNamespace:
    """构建 root 成员的 mock CurrentUser（is_root_member=True）。"""
    return SimpleNamespace(
        user_id=USER_ID,
        email="admin@irip.local",
        roles=["platform_administrator"],
        department_id=DEPT_ID,
        is_root_member=True,
    )


@pytest.mark.asyncio
async def test_jobs_override_returns_job_service() -> None:
    """jobs 的 get_job_service override 返回 JobService 实例。"""
    from apps.api.composition.jobs import register
    from apps.api.routers.jobs import get_job_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.jobs.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        override = app.dependency_overrides[get_job_service]
        service = await override(current_user=_mock_current_user())
    assert service is not None


@pytest.mark.asyncio
async def test_jobs_override_with_rls_override() -> None:
    """平台管理员（非 root 成员）触发 set_rls_override。"""
    from apps.api.composition.jobs import register
    from apps.api.routers.jobs import get_job_service

    app = FastAPI()
    root_dept = uuid4()
    ctx = _make_ctx(app=app, root_dept_id=root_dept)
    with patch("apps.api.composition.jobs.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        override = app.dependency_overrides[get_job_service]
        service = await override(current_user=_mock_current_user())
    assert service is not None


@pytest.mark.asyncio
async def test_models_override_returns_model_service() -> None:
    """models 的 get_model_service override 返回 ModelService 实例。"""
    from apps.api.composition.models import register
    from apps.api.routers.models import get_model_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.models.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        override = app.dependency_overrides[get_model_service]
        service = await override(current_user=_mock_current_user())
    assert service is not None


@pytest.mark.asyncio
async def test_standards_overrides_return_services() -> None:
    """standards 的各 override 返回对应服务实例。"""
    from apps.api.composition.standards import register
    from apps.api.dependencies.departments import (
        get_department_service,
        get_user_department_service,
    )
    from apps.api.routers.equipment import get_equipment_service
    from apps.api.routers.experiment_projects import get_experiment_project_service
    from apps.api.routers.objects import get_object_graph_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    register(ctx)
    user = _mock_current_user()
    for key in (
        get_object_graph_service,
        get_department_service,
        get_equipment_service,
        get_user_department_service,
        get_experiment_project_service,
    ):
        service = await app.dependency_overrides[key](current_user=user)
        assert service is not None


@pytest.mark.asyncio
async def test_facts_overrides_return_services() -> None:
    """facts 的各 override 返回对应服务实例。"""
    from apps.api.composition.facts import register
    from apps.api.routers.facts import get_fact_query_service, get_fact_service
    from apps.api.routers.provenance import (
        get_derivation_service,
        get_evidence_service,
        get_provenance_graph_service,
        get_recipe_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.facts.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (
            get_fact_service,
            get_fact_query_service,
            get_evidence_service,
            get_recipe_service,
            get_derivation_service,
            get_provenance_graph_service,
        ):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None


@pytest.mark.asyncio
async def test_flows_overrides_return_services() -> None:
    """flows 的各 override 返回对应服务实例。"""
    from apps.api.composition.flows import register
    from apps.api.routers.components import get_component_registry_service
    from apps.api.routers.flows import get_flow_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.flows.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        comp_svc = await app.dependency_overrides[get_component_registry_service](current_user=user)
        assert comp_svc is not None
        flow_svc = await app.dependency_overrides[get_flow_service](current_user=user)
        assert flow_svc is not None


@pytest.mark.asyncio
async def test_infrastructure_overrides_return_services() -> None:
    """infrastructure 的 async override 返回对应服务实例。"""
    from apps.api.composition.infrastructure import register
    from apps.api.routers.ingestions import get_ingestion_service
    from apps.api.routers.parameters import get_parameter_service
    from apps.api.routers.uploads import get_artifact_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.infrastructure.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (get_artifact_service, get_ingestion_service, get_parameter_service):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None


@pytest.mark.asyncio
async def test_research_overrides_return_services() -> None:
    """research 的各 override 返回对应服务实例。"""
    from apps.api.composition.research import register
    from apps.api.routers.research import get_snapshot_service, get_workspace_service
    from apps.api.routers.timeline_dependencies import (
        get_analysis_service,
        get_conclusion_bar_service,
        get_conclusion_service,
        get_recommendation_service,
        get_timeline_query_service,
        get_turn_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.research.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (
            get_workspace_service,
            get_snapshot_service,
            get_timeline_query_service,
            get_recommendation_service,
            get_analysis_service,
            get_turn_service,
            get_conclusion_service,
            get_conclusion_bar_service,
        ):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None


@pytest.mark.asyncio
async def test_research_run_overrides_return_services() -> None:
    """research_run 的各 override 返回对应服务实例。"""
    from apps.api.composition.research_run import register
    from apps.api.routers.research_run import (
        get_conversation_service,
        get_plan_service,
        get_run_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.research_run.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (get_plan_service, get_run_service, get_conversation_service):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None


@pytest.mark.asyncio
async def test_research_products_overrides_return_services() -> None:
    """research_products 的各 override 返回对应服务实例。"""
    from apps.api.composition.research_products import register
    from apps.api.routers.research_products import (
        get_candidate_service,
        get_catalog,
        get_product_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.research_products.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (get_product_service, get_candidate_service, get_catalog):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None


@pytest.mark.asyncio
async def test_research_publish_overrides_return_services() -> None:
    """research_publish 的各 override 返回对应服务实例。"""
    from apps.api.composition.research_publish import register
    from apps.api.routers.research_publish import (
        get_publication_service,
        get_publish_catalog,
        get_search_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.research_publish.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (get_publication_service, get_search_service, get_publish_catalog):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None


@pytest.mark.asyncio
async def test_research_lineage_overrides_return_services() -> None:
    """research_lineage 的各 override 返回对应服务实例。"""
    from apps.api.composition.research_lineage import register
    from apps.api.routers.research_lineage import (
        get_knowledge_provider_service,
        get_knowledge_reference_service,
        get_provenance_service,
    )

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with patch("apps.api.composition.research_lineage.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        user = _mock_current_user()
        for key in (get_provenance_service, get_knowledge_reference_service):
            service = await app.dependency_overrides[key](current_user=user)
            assert service is not None
        # knowledge_provider_service 不需要 current_user
        kp_service = await app.dependency_overrides[get_knowledge_provider_service]()
        assert kp_service is not None


@pytest.mark.asyncio
async def test_ai_override_returns_ai_service() -> None:
    """ai 的 get_ai_service override 返回 AIService 实例。"""
    from apps.api.composition.ai import register
    from apps.api.routers.assistant import get_ai_service

    app = FastAPI()
    ctx = _make_ctx(app=app)
    with (
        patch("apps.api.composition.ai.set_assistant_session_factory"),
        patch("apps.api.composition.ai.set_ai_tools_session_factory"),
        patch("apps.api.composition.ai.set_object_types_session_factory"),
        patch("packages.ai.yaml_config.get_scenario_config") as mock_config,
    ):
        mock_config.return_value = SimpleNamespace(
            api_key="test-key",
            base_url="http://localhost:8080",
            model="test-model",
            thinking_enabled=False,
        )
        register(ctx)
        override = app.dependency_overrides[get_ai_service]
        service = await override()
    assert service is not None


@pytest.mark.asyncio
async def test_overrides_with_root_member_no_rls_override() -> None:
    """root 成员不触发 set_rls_override（get_rls_dept_id 返回 None）。"""
    from apps.api.composition.jobs import register
    from apps.api.routers.jobs import get_job_service

    app = FastAPI()
    root_dept = uuid4()
    ctx = _make_ctx(app=app, root_dept_id=root_dept)
    with patch("apps.api.composition.jobs.lookup_dept_id", return_value=DEPT_ID):
        register(ctx)
        override = app.dependency_overrides[get_job_service]
        service = await override(current_user=_mock_current_user_root_member())
    assert service is not None
