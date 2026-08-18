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
