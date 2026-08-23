"""Unit tests for apps.worker.tasks.flows — Celery flow tasks.

Tests _execute_flow_async, _resume_flow_async, _mark_job_failed,
and the Celery task wrappers execute_flow_job / resume_flow_job.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import apps.worker.tasks.flows as flows_mod
from apps.worker.tasks.flows import execute_flow_job, resume_flow_job


@pytest.fixture(autouse=True)
def _mock_sysuser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock get_system_service_user_id to avoid DB access."""
    monkeypatch.setattr(
        "apps.worker.tasks.flows.get_system_service_user_id",
        AsyncMock(return_value=UUID("00000000-0000-0000-0000-000000000001")),
    )


def _get_flow_task():
    """Get the execute_flow_job task proxy."""
    return execute_flow_job


def _get_resume_task():
    """Get the resume_flow_job task proxy."""
    return resume_flow_job


class TestExecuteFlowJob:
    """Tests for the execute_flow_job Celery task wrapper."""

    def test_missing_run_id_returns_error(self) -> None:
        task = _get_flow_task()
        result = task.run("job-1", {})
        assert result["error"] == "payload missing run_id"
        assert result["job_id"] == "job-1"

    def test_success(self) -> None:
        task = _get_flow_task()
        expected = {"run_id": "abc", "status": "succeeded", "output_digest": "sha"}
        with patch.object(flows_mod, "asyncio") as mock_aio:
            mock_aio.run.return_value = expected
            result = task.run("job-1", {"run_id": "abc", "department_id": "d1"})
        assert result == expected

    def test_non_retryable_error_raises_and_marks_failed(self) -> None:
        task = _get_flow_task()
        with patch.object(flows_mod, "asyncio") as mock_aio:
            mock_aio.run.side_effect = [ValueError("bad flow"), None]
            with pytest.raises(ValueError, match="bad flow"):
                task.run("job-1", {"run_id": "abc", "department_id": "d1"})
        assert mock_aio.run.call_count == 2

    def test_retryable_error_triggers_retry(self) -> None:
        task = _get_flow_task()
        with (
            patch.object(flows_mod, "asyncio") as mock_aio,
            patch.object(task, "retry", side_effect=Exception("retry triggered")) as mock_retry,
        ):
            mock_aio.run.side_effect = [ConnectionError("transient"), None]
            with pytest.raises(Exception, match="retry triggered"):
                task.run("job-1", {"run_id": "abc", "department_id": "d1"})
        mock_retry.assert_called_once()


class TestResumeFlowJob:
    """Tests for the resume_flow_job Celery task wrapper."""

    def test_missing_run_id_returns_error(self) -> None:
        task = _get_resume_task()
        result = task.run("job-1", {})
        assert result["error"] == "payload missing run_id"

    def test_success(self) -> None:
        task = _get_resume_task()
        expected = {"run_id": "abc", "status": "succeeded", "output_digest": "sha"}
        with patch.object(flows_mod, "asyncio") as mock_aio:
            mock_aio.run.return_value = expected
            result = task.run("job-1", {"run_id": "abc", "department_id": "d1"})
        assert result == expected

    def test_retryable_error_triggers_retry(self) -> None:
        task = _get_resume_task()
        with (
            patch.object(flows_mod, "asyncio") as mock_aio,
            patch.object(task, "retry", side_effect=Exception("retry triggered")) as mock_retry,
        ):
            mock_aio.run.side_effect = [TimeoutError("timeout"), None]
            with pytest.raises(Exception, match="retry triggered"):
                task.run("job-1", {"run_id": "abc", "department_id": "d1"})
        mock_retry.assert_called_once()


class TestExecuteFlowAsync:
    """Tests for _execute_flow_async internal function."""

    async def test_run_not_found(self) -> None:
        """When FlowRun is not found, returns error dict."""
        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_service = AsyncMock()

        with (
            patch("packages.common.database.build_session_factory", return_value=mock_factory),
            patch(
                "packages.common.database.get_database_url", return_value="postgresql+psycopg://x"
            ),
            patch("packages.common.database.session_scope", _fake_session_scope(mock_session)),
            patch(
                "packages.components.flow.flow_runtime.FlowRuntimeService",
                return_value=mock_service,
            ),
            patch("packages.components.registry.ComponentRegistryService"),
            patch("packages.components.runner.PythonComponentRunner"),
            patch("packages.components.builtin.register_builtin_components"),
            patch("packages.jobs.service.JobService"),
            patch("packages.common.artifacts.ArtifactService"),
            patch("packages.common.s3_repository.S3Repository"),
            patch("packages.ai.yaml_config.async_provider_wrapper"),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
        ):
            result = await flows_mod._execute_flow_async(
                "00000000-0000-0000-0000-000000000001",
                {"department_id": "00000000-0000-0000-0000-000000000001"},
            )
        assert result["error"] == "run not found"
        mock_service.execute.assert_called_once()


class TestMarkJobFailed:
    """Tests for _mark_job_failed."""

    async def test_mark_job_failed_calls_update(self) -> None:
        mock_session = AsyncMock()
        with (
            patch("packages.common.database.build_session_factory", return_value=MagicMock()),
            patch(
                "packages.common.database.get_database_url", return_value="postgresql+psycopg://x"
            ),
            patch("packages.common.database.session_scope", _fake_session_scope(mock_session)),
            patch("apps.worker.tasks.get_system_guc", return_value=(None, None)),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
        ):
            await flows_mod._mark_job_failed("00000000-0000-0000-0000-000000000001", "some error")
        mock_session.execute.assert_called_once()


def _fake_session_scope(mock_session):
    """Build a fake session_scope contextmanager that yields mock_session."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake(_factory):
        yield mock_session

    return _fake
