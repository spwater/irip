"""research_run_router API 测试：计划生成 / Run 提交 / 对话 / 工件。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_plan_service、get_run_service、get_conversation_service
- Mock PlanService / AnalysisRunService / AIConversationService
- 验证 HTTP 状态码、响应体字段、错误码（404/422/409）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_run import (
    research_run_router,
)
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 research:use 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_plan_ref(
    plan_id: UUID | None = None,
    workspace_id: UUID | None = None,
    version_number: int = 1,
    status: str = "draft",
    step_count: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        plan_id=plan_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        version_number=version_number,
        status=status,
        step_count=step_count,
    )


def _make_plan_detail(
    plan_id: UUID | None = None,
    workspace_id: UUID | None = None,
    version_number: int = 1,
    status: str = "draft",
) -> SimpleNamespace:
    return SimpleNamespace(
        plan_id=plan_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        version_number=version_number,
        status=status,
        dag_structure={"nodes": [], "edges": []},
        coverage_declaration=None,
        created_at=datetime.now(UTC),
        confirmed_at=None,
    )


def _make_run_ref(
    run_id: UUID | None = None,
    workspace_id: UUID | None = None,
    run_number: int = 1,
    status: str = "queued",
    queue_position: int | None = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        run_number=run_number,
        status=status,
        queue_position=queue_position,
    )


def _make_run_progress(
    run_id: UUID | None = None,
    status: str = "running",
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id or uuid4(),
        status=status,
        total_steps=3,
        completed_steps=1,
        steps=[
            SimpleNamespace(
                step_id=uuid4(),
                step_key="analyze_0",
                step_index=0,
                status="completed",
                method="llm_analyze",
                analysis_mode="sampled",
                coverage_rate=0.8,
                llm_read_rate=0.1,
                is_sampled=True,
                attempt_count=1,
                error_message=None,
            )
        ],
        coverage_declaration=None,
        started_at=datetime.now(UTC),
        completed_at=None,
    )


def _make_artifact_ref(
    artifact_id: UUID | None = None,
    run_id: UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact_id or uuid4(),
        run_id=run_id or uuid4(),
        step_id=uuid4(),
        artifact_type="dataset",
        artifact_key="result_0",
        storage_path="minio://bucket/result_0.parquet",
        content_hash="abc123",
        size_bytes=1024,
        is_publishable=True,
        created_at=datetime.now(UTC),
    )


def _make_queue_status(
    position: int = 0,
    ahead_count: int = 0,
    estimated_wait_seconds: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        position=position,
        ahead_count=ahead_count,
        estimated_wait_seconds=estimated_wait_seconds,
    )


def _make_conversation_message(
    message_id: UUID | None = None,
    workspace_id: UUID | None = None,
    role: str = "user",
) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        role=role,
        content={"text": "hello"},
        run_id=None,
        created_at=datetime.now(UTC),
    )


def _make_mock_plan_service() -> MagicMock:
    service = MagicMock()
    service.generate_plan = AsyncMock()
    service.list_plans = AsyncMock()
    service.get_plan = AsyncMock()
    service.confirm_plan = AsyncMock()
    service.revise_plan = AsyncMock()
    service.analyze_data = AsyncMock()
    service.extract_insight = AsyncMock()
    return service


def _make_mock_run_service() -> MagicMock:
    service = MagicMock()
    service.submit_run = AsyncMock()
    service.list_runs = AsyncMock()
    service.get_run_progress = AsyncMock()
    service.cancel_run = AsyncMock()
    service.get_queue_position = AsyncMock()
    return service


def _make_mock_conversation_service() -> MagicMock:
    service = MagicMock()
    service.send_message = AsyncMock()
    service.list_messages = AsyncMock()
    return service


def _make_app(
    plan_service: MagicMock | None = None,
    run_service: MagicMock | None = None,
    conversation_service: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(research_run_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    from apps.api.routers.research_run import (
        get_conversation_service,
        get_plan_service,
        get_run_service,
    )

    if plan_service is not None:
        app.dependency_overrides[get_plan_service] = lambda: plan_service
    if run_service is not None:
        app.dependency_overrides[get_run_service] = lambda: run_service
    if conversation_service is not None:
        app.dependency_overrides[get_conversation_service] = lambda: conversation_service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST 生成计划
# ===========================================================================


class TestGeneratePlan:
    """POST /api/v1/research/workspaces/{id}/plans — 生成分析计划。"""

    def test_generate_plan_201(self):
        plan_service = _make_mock_plan_service()
        ref = _make_plan_ref()
        plan_service.generate_plan = AsyncMock(return_value=ref)

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{ref.workspace_id}/plans",
            json={"snapshot_id": str(uuid4())},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["plan_id"] == str(ref.plan_id)
        assert data["status"] == "draft"
        assert data["step_count"] == 3

    def test_generate_plan_missing_snapshot_422(self):
        plan_service = _make_mock_plan_service()
        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/plans",
            json={},
        )

        assert response.status_code == 422

    def test_generate_plan_not_found_404(self):
        plan_service = _make_mock_plan_service()
        plan_service.generate_plan = AsyncMock(
            side_effect=AppError(code="not_found", message="快照不存在", retryable=False, fields={})
        )

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/plans",
            json={"snapshot_id": str(uuid4())},
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# ===========================================================================
# 2. GET 计划列表 + 详情 + 确认 + 修订
# ===========================================================================


class TestPlanList:
    """GET /api/v1/research/workspaces/{id}/plans — 列出计划。"""

    def test_list_plans_200(self):
        plan_service = _make_mock_plan_service()
        refs = [_make_plan_ref(version_number=i) for i in range(2)]
        plan_service.list_plans = AsyncMock(return_value=refs)

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/plans")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["version_number"] == 0

    def test_get_plan_detail_200(self):
        plan_service = _make_mock_plan_service()
        detail = _make_plan_detail()
        plan_service.get_plan = AsyncMock(return_value=detail)

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{detail.workspace_id}/plans/{detail.plan_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["plan_id"] == str(detail.plan_id)
        assert data["dag_structure"] == {"nodes": [], "edges": []}

    def test_get_plan_not_found_404(self):
        plan_service = _make_mock_plan_service()
        plan_service.get_plan = AsyncMock(
            side_effect=AppError(code="not_found", message="计划不存在", retryable=False, fields={})
        )

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/plans/{uuid4()}")

        assert response.status_code == 404


class TestConfirmAndRevisePlan:
    """POST confirm / PUT revise。"""

    def test_confirm_plan_200(self):
        plan_service = _make_mock_plan_service()
        ref = _make_plan_ref(status="confirmed")
        plan_service.confirm_plan = AsyncMock(return_value=ref)

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{ref.workspace_id}/plans/{ref.plan_id}/confirm"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "confirmed"

    def test_revise_plan_200(self):
        plan_service = _make_mock_plan_service()
        ref = _make_plan_ref(version_number=2)
        plan_service.revise_plan = AsyncMock(return_value=ref)

        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.put(
            f"/api/v1/research/workspaces/{ref.workspace_id}/plans/{ref.plan_id}",
            json={"steps": [{"key": "step1"}]},
        )

        assert response.status_code == 200
        assert response.json()["version_number"] == 2

    def test_revise_plan_missing_steps_422(self):
        plan_service = _make_mock_plan_service()
        app = _make_app(plan_service=plan_service)
        client = TestClient(app)

        response = client.put(
            f"/api/v1/research/workspaces/{uuid4()}/plans/{uuid4()}",
            json={},
        )

        assert response.status_code == 422


# ===========================================================================
# 3. Run 端点
# ===========================================================================


class TestSubmitRun:
    """POST /api/v1/research/workspaces/{id}/runs — 提交 Run。"""

    def test_submit_run_201(self):
        run_service = _make_mock_run_service()
        ref = _make_run_ref()
        run_service.submit_run = AsyncMock(return_value=ref)

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{ref.workspace_id}/runs",
            json={"plan_version_id": str(uuid4()), "snapshot_id": str(uuid4())},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["run_id"] == str(ref.run_id)
        assert data["status"] == "queued"

    def test_submit_run_missing_field_422(self):
        run_service = _make_mock_run_service()
        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/runs",
            json={"plan_version_id": str(uuid4())},
        )

        assert response.status_code == 422

    def test_submit_run_conflict_409(self):
        run_service = _make_mock_run_service()
        run_service.submit_run = AsyncMock(
            side_effect=AppError(code="conflict", message="Run 已存在", retryable=False, fields={})
        )

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/runs",
            json={"plan_version_id": str(uuid4()), "snapshot_id": str(uuid4())},
        )

        assert response.status_code == 409


class TestListAndGetRun:
    """GET runs / GET run detail。"""

    def test_list_runs_200(self):
        run_service = _make_mock_run_service()
        refs = [_make_run_ref(run_number=i) for i in range(2)]
        run_service.list_runs = AsyncMock(return_value=refs)

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/runs")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_get_run_200(self):
        run_service = _make_mock_run_service()
        progress = _make_run_progress()
        run_service.get_run_progress = AsyncMock(return_value=progress)

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/runs/{progress.run_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["total_steps"] == 3
        assert data["completed_steps"] == 1
        assert len(data["steps"]) == 1

    def test_get_run_not_found_404(self):
        run_service = _make_mock_run_service()
        run_service.get_run_progress = AsyncMock(
            side_effect=AppError(code="not_found", message="Run 不存在", retryable=False, fields={})
        )

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}")

        assert response.status_code == 404


class TestCancelRun:
    """POST cancel run。"""

    def test_cancel_run_204(self):
        run_service = _make_mock_run_service()
        run_service.cancel_run = AsyncMock(return_value=None)

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/cancel")

        assert response.status_code == 204

    def test_cancel_run_not_found_404(self):
        run_service = _make_mock_run_service()
        run_service.cancel_run = AsyncMock(
            side_effect=AppError(code="not_found", message="Run 不存在", retryable=False, fields={})
        )

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/cancel")

        assert response.status_code == 404


# ===========================================================================
# 4. 排队状态
# ===========================================================================


class TestQueueStatus:
    """GET queue-status。"""

    def test_queue_status_200(self):
        run_service = _make_mock_run_service()
        run_service.get_queue_position = AsyncMock(
            return_value=_make_queue_status(position=2, ahead_count=1, estimated_wait_seconds=30)
        )

        app = _make_app(run_service=run_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/queue-status")

        assert response.status_code == 200
        data = response.json()
        assert data["position"] == 2
        assert data["ahead_count"] == 1


# ===========================================================================
# 5. 工件
# ===========================================================================


class TestArtifacts:
    """GET artifacts。"""

    def test_list_artifacts_200(self):
        artifact_service = MagicMock()
        artifact_service.list_artifacts = AsyncMock(
            return_value=[_make_artifact_ref() for _ in range(2)]
        )

        with patch(
            "apps.api.routers.research_run._get_artifact_service",
            return_value=artifact_service,
        ):
            app = _make_app()
            client = TestClient(app)

            response = client.get(f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/artifacts")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_list_artifacts_with_type_filter(self):
        artifact_service = MagicMock()
        artifact_service.list_artifacts = AsyncMock(return_value=[])

        with patch(
            "apps.api.routers.research_run._get_artifact_service",
            return_value=artifact_service,
        ):
            app = _make_app()
            client = TestClient(app)

            response = client.get(
                f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/artifacts?artifact_type=dataset"
            )

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_get_artifact_200(self):
        content = SimpleNamespace(
            artifact_id=uuid4(),
            artifact_type="dataset",
            artifact_key="result_0",
            content_hash="abc123",
            content=b"data" * 100,
        )
        artifact_service = MagicMock()
        artifact_service.get_artifact = AsyncMock(return_value=content)

        with patch(
            "apps.api.routers.research_run._get_artifact_service",
            return_value=artifact_service,
        ):
            app = _make_app()
            client = TestClient(app)

            response = client.get(
                f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/artifacts/{content.artifact_id}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["artifact_id"] == str(content.artifact_id)
        assert data["size"] == len(content.content)

    def test_get_artifact_not_found(self):
        artifact_service = MagicMock()
        artifact_service.get_artifact = AsyncMock(return_value=None)

        with patch(
            "apps.api.routers.research_run._get_artifact_service",
            return_value=artifact_service,
        ):
            app = _make_app()
            client = TestClient(app)

            response = client.get(
                f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/artifacts/{uuid4()}"
            )

        assert response.status_code == 200
        assert response.json()["error"]["code"] == "not_found"


# ===========================================================================
# 6. 对话
# ===========================================================================


class TestConversation:
    """POST / GET conversation。"""

    def test_send_message_200(self):
        conversation_service = _make_mock_conversation_service()
        msg = _make_conversation_message(role="assistant")
        conversation_service.send_message = AsyncMock(return_value=msg)

        app = _make_app(conversation_service=conversation_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{msg.workspace_id}/conversation",
            json={"message": "分析结果如何？"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == str(msg.message_id)
        assert data["role"] == "assistant"

    def test_send_message_empty_422(self):
        conversation_service = _make_mock_conversation_service()
        app = _make_app(conversation_service=conversation_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conversation",
            json={"message": ""},
        )

        assert response.status_code == 422

    def test_list_messages_200(self):
        conversation_service = _make_mock_conversation_service()
        msgs = [_make_conversation_message() for _ in range(3)]
        conversation_service.list_messages = AsyncMock(return_value=msgs)

        app = _make_app(conversation_service=conversation_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/conversation")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 3

    def test_list_messages_with_limit(self):
        conversation_service = _make_mock_conversation_service()
        conversation_service.list_messages = AsyncMock(return_value=[])

        app = _make_app(conversation_service=conversation_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/conversation?limit=10")

        assert response.status_code == 200
        call_kwargs = conversation_service.list_messages.call_args.kwargs
        assert call_kwargs["limit"] == 10


# ===========================================================================
# 7. 模型验证
# ===========================================================================


class TestRequestModels:
    """验证 Pydantic 请求模型。"""

    def test_send_message_request_validation(self):
        from apps.api.routers.research_run import SendMessageRequest

        body = SendMessageRequest(message="hello")
        assert body.message == "hello"
        assert body.run_id is None

    def test_submit_run_request_validation(self):
        from apps.api.routers.research_run import SubmitRunRequest

        pid = uuid4()
        sid = uuid4()
        body = SubmitRunRequest(plan_version_id=pid, snapshot_id=sid)
        assert body.plan_version_id == pid
        assert body.snapshot_id == sid

    def test_generate_plan_request_validation(self):
        from apps.api.routers.research_run import GeneratePlanRequest

        sid = uuid4()
        body = GeneratePlanRequest(snapshot_id=sid)
        assert body.snapshot_id == sid
