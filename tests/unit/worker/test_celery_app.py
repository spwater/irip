"""Unit tests for apps.worker.celery_app — Celery app configuration.

Tests beat schedule, task registration, health check endpoint,
and RLS safety assertion.

IMPORTANT: Do NOT access celery_app.tasks — it triggers Celery finalization
which resolves PromiseProxy objects and breaks pre-existing tests that rely
on _get_current_object(). Use direct imports and __call__ instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import apps.worker.celery_app as celery_mod
from apps.worker.celery_app import (
    _assert_not_superuser,
    _HealthcheckHandler,
    celery_app,
    dispatch_outbox,
    execute_job,
    reap_expired_leases,
    retry_wait_jobs,
    run_worker_healthcheck_server,
    worker_heartbeat,
)


class TestCeleryAppConfig:
    """Tests for Celery app configuration."""

    def test_app_name(self) -> None:
        assert celery_app.main == "irip"

    def test_task_serializer_is_json(self) -> None:
        assert celery_app.conf.task_serializer == "json"

    def test_result_serializer_is_json(self) -> None:
        assert celery_app.conf.result_serializer == "json"

    def test_accept_content_json(self) -> None:
        assert "json" in celery_app.conf.accept_content

    def test_timezone_utc(self) -> None:
        assert celery_app.conf.timezone == "UTC"
        assert celery_app.conf.enable_utc is True

    def test_acks_late(self) -> None:
        assert celery_app.conf.task_acks_late is True

    def test_prefetch_one(self) -> None:
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_default_max_retries(self) -> None:
        assert celery_app.conf.task_default_max_retries == 3

    def test_beat_schedule_has_outbox_dispatch(self) -> None:
        assert "dispatch-outbox" in celery_app.conf.beat_schedule
        assert celery_app.conf.beat_schedule["dispatch-outbox"]["task"] == "outbox.dispatch"

    def test_beat_schedule_has_heartbeat(self) -> None:
        assert "worker-heartbeat" in celery_app.conf.beat_schedule

    def test_beat_schedule_has_reap_expired(self) -> None:
        assert "reap-expired-leases" in celery_app.conf.beat_schedule

    def test_beat_schedule_has_retry_wait(self) -> None:
        assert "retry-wait-jobs" in celery_app.conf.beat_schedule

    def test_beat_schedule_has_daily_backup(self) -> None:
        assert "daily-backup" in celery_app.conf.beat_schedule

    def test_beat_schedule_has_retention_cleanup(self) -> None:
        assert "backup-retention-cleanup" in celery_app.conf.beat_schedule

    def test_beat_schedule_has_audit_retention(self) -> None:
        assert "audit-retention-cleanup" in celery_app.conf.beat_schedule

    def test_task_routes_normal(self) -> None:
        assert celery_app.conf.task_routes["jobs.execute"]["queue"] == "irip-normal"

    def test_task_routes_research(self) -> None:
        assert celery_app.conf.task_routes["research.run.execute"]["queue"] == "irip-research"

    def test_task_routes_ops(self) -> None:
        assert celery_app.conf.task_routes["backup.daily"]["queue"] == "irip-ops"

    def test_include_list(self) -> None:
        assert "apps.worker.tasks" in celery_app.conf.include


class TestExecuteJobTask:
    """Tests for the execute_job Celery task."""

    def test_delegates_to_do_execute_job(self) -> None:
        with patch("apps.worker.tasks._do_execute_job", return_value="job-123") as mock_do:
            # execute_job has bind=True, so calling via __call__ auto-binds self
            result = execute_job("job-123")
        assert result == "job-123"
        mock_do.assert_called_once_with("job-123")


class TestWorkerHeartbeat:
    """Tests for worker_heartbeat task."""

    def test_returns_ok(self) -> None:
        result = worker_heartbeat()
        assert result == "heartbeat-ok"

    def test_redis_error_silenced(self) -> None:
        """Even if Redis fails, heartbeat returns ok."""
        with patch("apps.worker.celery_app.get_redis_url", return_value="redis://invalid:1/0"):
            result = worker_heartbeat()
        assert result == "heartbeat-ok"


class TestDispatchOutbox:
    """Tests for dispatch_outbox task."""

    def test_delegates_to_run_dispatch(self) -> None:
        with patch("packages.jobs.dispatcher.run_dispatch", return_value=5) as mock_dispatch:
            result = dispatch_outbox()
        assert result == 5
        mock_dispatch.assert_called_once()


class TestReapExpiredLeases:
    """Tests for reap_expired_leases task."""

    def test_returns_count(self) -> None:
        with (
            patch("apps.worker.celery_app.get_database_url", return_value="postgresql+psycopg://x"),
            patch("packages.common.database.build_session_factory", return_value=MagicMock()),
            patch("apps.worker.tasks.get_system_guc", return_value=(None, None)),
            patch("packages.jobs.worker.WorkerLeaseManager") as mock_lm_cls,
        ):
            mock_lm = MagicMock()
            mock_lm.reap_expired = AsyncMock(return_value=[1, 2, 3])
            mock_lm_cls.return_value = mock_lm
            result = reap_expired_leases()
        assert result == 3


class TestRetryWaitJobs:
    """Tests for retry_wait_jobs task."""

    def test_returns_count(self) -> None:
        with (
            patch("apps.worker.celery_app.get_database_url", return_value="postgresql+psycopg://x"),
            patch("packages.common.database.build_session_factory", return_value=MagicMock()),
            patch("packages.common.database.session_scope") as mock_ss,
            patch("apps.worker.tasks.get_system_guc", return_value=(None, None)),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute.return_value = mock_result

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def fake_ss(_factory):
                yield mock_session

            mock_ss.side_effect = fake_ss
            result = retry_wait_jobs()
        assert result == 0


class TestHealthcheckHandler:
    """Tests for _HealthcheckHandler."""

    def test_do_GET_health(self) -> None:
        handler = _HealthcheckHandler.__new__(_HealthcheckHandler)
        handler.path = "/health"
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once_with(b'{"status": "ok"}')

    def test_do_GET_other_path(self) -> None:
        handler = _HealthcheckHandler.__new__(_HealthcheckHandler)
        handler.path = "/other"
        handler.send_response = MagicMock()
        handler.end_headers = MagicMock()
        handler.do_GET()
        handler.send_response.assert_called_once_with(404)

    def test_log_message_silent(self) -> None:
        handler = _HealthcheckHandler.__new__(_HealthcheckHandler)
        # Should not raise
        handler.log_message("test %s", "arg")


class TestRunWorkerHealthcheckServer:
    """Tests for run_worker_healthcheck_server."""

    def test_non_blocking_returns_server(self) -> None:
        server = run_worker_healthcheck_server(port=0, block=False)
        assert server is not None
        server.shutdown()

    def test_custom_port(self) -> None:
        # Port 0 lets OS pick a free port
        server = run_worker_healthcheck_server(port=0)
        assert server is not None
        server.shutdown()


class TestAssertNotSuperuser:
    """Tests for _assert_not_superuser."""

    def test_no_db_url_returns_silently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(celery_mod, "get_database_url", lambda: "")
        # Should not raise
        _assert_not_superuser()

    def test_non_superuser_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            celery_mod, "get_database_url", lambda: "postgresql+psycopg://user@host/db"
        )
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (False, False, "irip_app")
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=None)

        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            _assert_not_superuser()
        mock_engine.dispose.assert_called_once()

    def test_superuser_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            celery_mod, "get_database_url", lambda: "postgresql+psycopg://super@host/db"
        )
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (True, False, "super")
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=None)

        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            with pytest.raises(RuntimeError, match="安全断言失败"):
                _assert_not_superuser()
