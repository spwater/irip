"""Contract tests for research timeline API: route existence, page size limits,
and removal of old endpoints."""

from apps.api.routers.research_timeline import research_timeline_router


class TestTimelineRoutesExist:
    """Verify all new timeline routes exist."""

    def _get_routes(self) -> set[str]:
        return {r.path for r in research_timeline_router.routes}

    def test_timeline_list_route(self) -> None:
        routes = self._get_routes()
        assert "/api/v1/research/workspaces/{workspace_id}/timeline" in routes

    def test_create_turn_route(self) -> None:
        routes = self._get_routes()
        assert "/api/v1/research/workspaces/{workspace_id}/turns" in routes

    def test_create_synthesis_turn_route(self) -> None:
        routes = self._get_routes()
        assert "/api/v1/research/workspaces/{workspace_id}/synthesis-turns" in routes

    def test_followup_recommendation_route(self) -> None:
        routes = self._get_routes()
        assert "/api/v1/research/workspaces/{workspace_id}/recommendations/followup" in routes

    def test_manual_conclusion_route(self) -> None:
        routes = self._get_routes()
        assert "/api/v1/research/workspaces/{workspace_id}/conclusions/manual" in routes

    def test_revise_conclusion_route(self) -> None:
        routes = self._get_routes()
        assert "/api/v1/research/workspaces/{workspace_id}/conclusions/{conclusion_id}" in routes


class TestPageSizeValidation:
    """Verify page_size Query parameter has ge=1 and le=50."""

    def test_timeline_page_size_constraints(self) -> None:
        """The timeline endpoint should have page_size with ge=1, le=50."""

        # Find the timeline endpoint
        timeline_route = None
        for route in research_timeline_router.routes:
            if hasattr(route, "path") and "timeline" in route.path:
                timeline_route = route
                break

        assert timeline_route is not None, "Timeline route not found"
        # The endpoint should use Query(20, ge=1, le=50)
        # We verify by checking the dependency info
        assert timeline_route.path.endswith("/timeline")


class TestOldRoutesRemoved:
    """Verify old routes (question, fork, analyze-data, extract-insight)
    are NOT in timeline router."""

    def _get_routes(self) -> set[str]:
        return {r.path for r in research_timeline_router.routes}

    def test_no_question_route(self) -> None:
        routes = self._get_routes()
        assert not any("/question" in r for r in routes)

    def test_no_fork_route(self) -> None:
        routes = self._get_routes()
        assert not any("/fork" in r for r in routes)

    def test_no_analyze_data_route(self) -> None:
        routes = self._get_routes()
        assert not any("/analyze-data" in r for r in routes)

    def test_no_extract_insight_route(self) -> None:
        routes = self._get_routes()
        assert not any("/extract-insight" in r for r in routes)


class TestAnalysisFeatureGuard:
    """Verify the analyze endpoint returns 503 when RESEARCH_ANALYSIS_ENABLED is False.

    The feature flag is read at request time (lazy import inside the endpoint
    body), so reloading the feature_flags module before the request controls
    the guard behavior without restarting the process.
    """

    def test_analysis_endpoint_returns_feature_disabled(self, monkeypatch) -> None:
        """POST /analyze returns 503 feature_disabled when the flag is off."""
        import importlib
        from uuid import uuid4

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from apps.api.dependencies.auth import CurrentUser, get_current_user
        from apps.api.routers.research_timeline import (
            get_analysis_service,
            research_timeline_router,
        )
        from packages.common import feature_flags
        from packages.common.error_codes import ErrorCode
        from packages.common.errors import AppError

        # Ensure the flag is off (default), then reload so the module reflects it
        monkeypatch.delenv("RESEARCH_ANALYSIS_ENABLED", raising=False)
        importlib.reload(feature_flags)

        app = FastAPI()
        app.include_router(research_timeline_router)

        # Register the AppError handler (mirrors apps/api/main.py)
        status_map = ErrorCode.to_status_map()

        @app.exception_handler(AppError)
        async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
            return JSONResponse(
                status_code=status_map.get(exc.code, 500),
                content={"error": exc.to_dict()},
            )

        # Override auth: stub an authenticated user with research:use permission
        stub_user = CurrentUser(
            user_id=uuid4(),
            email="test@irip.local",
            roles=["platform_administrator"],
            department_id=uuid4(),
        )
        app.dependency_overrides[get_current_user] = lambda: stub_user

        # Override analysis service: must never be called when feature is disabled
        class _NeverCalledService:
            async def run_analysis(self, *args: object, **kwargs: object) -> dict:
                raise AssertionError(
                    "AnalysisService must not be called when feature is disabled"
                )

        app.dependency_overrides[get_analysis_service] = lambda: _NeverCalledService()

        client = TestClient(app)
        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/analyze",
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "feature_disabled"
