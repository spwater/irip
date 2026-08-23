"""Unit tests for apps.worker.tasks.__init__ — worker task dispatcher.

Tests _async_db_url, _get_session_factory caching, _validate_job_kind,
_register_handlers, _execute_job_async, _do_execute_job, beat helpers,
backup/audit_export handlers, and _resolve_backup_dir_by_id.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import apps.worker.tasks as tasks_mod
from packages.common.errors import AppError


class TestAsyncDbUrl:
    """Tests for _async_db_url."""

    def test_psycopg_to_async(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "IRIP_DATABASE_URL",
            "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test",
        )
        result = tasks_mod._async_db_url()
        assert "psycopg_async" in result

    def test_non_psycopg_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql+asyncpg://user@host/db")
        result = tasks_mod._async_db_url()
        assert result == "postgresql+asyncpg://user@host/db"


class TestGetSessionFactory:
    """Tests for _get_session_factory caching."""

    def test_caches_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._factory_cache = None
        monkeypatch.setattr(tasks_mod, "_async_db_url", lambda: "postgresql+psycopg_async://x")
        mock_factory = MagicMock()
        with patch.object(tasks_mod, "build_session_factory", return_value=mock_factory):
            first = tasks_mod._get_session_factory()
            second = tasks_mod._get_session_factory()
        assert first is mock_factory
        assert second is mock_factory
        # Cleanup
        tasks_mod._factory_cache = None


class TestValidateJobKind:
    """Tests for _validate_job_kind."""

    def test_valid_kind_passes(self) -> None:
        job = MagicMock()
        job.kind = "flow_execute"
        tasks_mod._validate_job_kind(job)  # should not raise

    def test_unknown_kind_raises(self) -> None:
        job = MagicMock()
        job.kind = "unknown_kind"
        with pytest.raises(AppError, match="未注册的作业类型"):
            tasks_mod._validate_job_kind(job)

    def test_empty_kind_raises(self) -> None:
        job = MagicMock()
        job.kind = ""
        with pytest.raises(AppError, match="未注册的作业类型"):
            tasks_mod._validate_job_kind(job)


class TestRegisterHandlers:
    """Tests for _register_handlers."""

    def test_all_handlers_registered(self) -> None:
        executor = MagicMock()
        tasks_mod._register_handlers(executor)
        registered_kinds = [call.args[0] for call in executor.register_handler.call_args_list]
        assert "flow_execute" in registered_kinds
        assert "flow_resume" in registered_kinds
        assert "model_train" in registered_kinds
        assert "model_predict" in registered_kinds
        assert "model_publish" in registered_kinds
        assert "backup" in registered_kinds
        assert "audit_export" in registered_kinds


class TestFlowExecuteAdapter:
    """Tests for the _flow_execute_adapter registered via _register_handlers."""

    async def test_missing_run_id_raises(self) -> None:
        executor = MagicMock()
        tasks_mod._register_handlers(executor)
        adapter = executor.register_handler.call_args_list[0].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000001")
        job.kind = "flow_execute"
        job.payload = {}

        with pytest.raises(AppError, match="payload missing run_id"):
            await adapter(job)

    async def test_adapter_executes_and_returns(self) -> None:
        executor = MagicMock()
        with patch(
            "apps.worker.tasks.flows._execute_flow_async",
            AsyncMock(return_value={"run_id": "ok"}),
        ):
            tasks_mod._register_handlers(executor)
        adapter = executor.register_handler.call_args_list[0].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000001")
        job.kind = "flow_execute"
        job.payload = {"run_id": "00000000-0000-0000-0000-000000000002"}

        result = await adapter(job)
        assert result == {"run_id": "ok"}

    async def test_adapter_unknown_kind_raises(self) -> None:
        executor = MagicMock()
        tasks_mod._register_handlers(executor)
        adapter = executor.register_handler.call_args_list[0].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000001")
        job.kind = "unknown"
        job.payload = {"run_id": "x"}

        with pytest.raises(AppError, match="未注册的作业类型"):
            await adapter(job)

    async def test_adapter_exception_marks_failed_and_reraises(self) -> None:
        executor = MagicMock()
        with (
            patch(
                "apps.worker.tasks.flows._execute_flow_async",
                AsyncMock(side_effect=RuntimeError("flow crashed")),
            ),
            patch(
                "apps.worker.tasks.flows._mark_job_failed",
                AsyncMock(),
            ),
        ):
            tasks_mod._register_handlers(executor)
        adapter = executor.register_handler.call_args_list[0].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000001")
        job.kind = "flow_execute"
        job.payload = {"run_id": "00000000-0000-0000-0000-000000000002"}

        with pytest.raises(RuntimeError, match="flow crashed"):
            await adapter(job)


class TestFlowResumeAdapter:
    """Tests for the _flow_resume_adapter."""

    async def test_adapter_executes(self) -> None:
        executor = MagicMock()
        with patch(
            "apps.worker.tasks.flows._resume_flow_async",
            AsyncMock(return_value={"run_id": "x", "status": "ok"}),
        ):
            tasks_mod._register_handlers(executor)
        adapter = executor.register_handler.call_args_list[1].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000003")
        job.kind = "flow_resume"
        job.payload = {"run_id": "x"}

        result = await adapter(job)
        assert result == {"run_id": "x", "status": "ok"}

    async def test_adapter_exception_reraises(self) -> None:
        executor = MagicMock()
        with (
            patch(
                "apps.worker.tasks.flows._resume_flow_async",
                AsyncMock(side_effect=ValueError("resume failed")),
            ),
            patch(
                "apps.worker.tasks.flows._mark_job_failed",
                AsyncMock(),
            ),
        ):
            tasks_mod._register_handlers(executor)
        adapter = executor.register_handler.call_args_list[1].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000003")
        job.kind = "flow_resume"
        job.payload = {"run_id": "x"}

        with pytest.raises(ValueError, match="resume failed"):
            await adapter(job)


class TestAdaptWrapper:
    """Tests for the _adapt generic handler adapter."""

    async def test_adapt_calls_handler_with_job_id_and_payload(self) -> None:
        executor = MagicMock()
        handler_result = {"status": "ok"}
        with patch.object(tasks_mod, "train_model_job", return_value=handler_result) as mock_train:
            tasks_mod._register_handlers(executor)
        model_train_adapter = executor.register_handler.call_args_list[2].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000010")
        job.kind = "model_train"
        job.payload = {"code": "m"}

        result = await model_train_adapter(job)

        assert result == handler_result
        mock_train.assert_called_once_with(str(job.id), {"code": "m"})

    async def test_adapt_unknown_kind_raises(self) -> None:
        executor = MagicMock()
        tasks_mod._register_handlers(executor)
        model_train_adapter = executor.register_handler.call_args_list[2].args[1]

        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000011")
        job.kind = "unknown"
        job.payload = {}

        with pytest.raises(AppError, match="未注册的作业类型"):
            await model_train_adapter(job)


class TestBackupHandler:
    """Tests for _backup_handler."""

    async def test_successful_backup(self) -> None:
        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000020")
        job.kind = "backup"
        job.department_id = UUID("00000000-0000-0000-0000-000000000099")
        job.payload = {"type": "daily", "backup_record_id": "00000000-0000-0000-0000-000000000030"}

        manifest = MagicMock()
        manifest.backup_id = "bid"
        manifest.database_sha256 = "sha256"
        manifest.object_count = 5
        manifest.migration_version = "001"
        manifest.application_version = "0.8.0"
        manifest.extra = {
            "backup_timestamp": "2026-01-01T00:00:00",
            "wal_start_lsn": "0/1",
            "wal_end_lsn": "0/2",
        }

        mock_service = AsyncMock()

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=MagicMock()),
            patch("packages.backups.service.BackupRecordService", return_value=mock_service),
            patch("deployments.compose.backup.run_backup", AsyncMock(return_value=manifest)),
        ):
            result = await tasks_mod._backup_handler(job)

        assert result["backup_id"] == "bid"
        assert result["database_sha256"] == "sha256"
        assert result["object_count"] == 5
        assert result["backup_type"] == "daily"
        mock_service.mark_succeeded.assert_called_once()

    async def test_backup_failure_marks_failed(self) -> None:
        job = MagicMock()
        job.id = UUID("00000000-0000-0000-0000-000000000021")
        job.kind = "backup"
        job.department_id = None
        job.payload = {"type": "daily", "backup_record_id": "00000000-0000-0000-0000-000000000031"}

        mock_service = AsyncMock()

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=MagicMock()),
            patch("packages.backups.service.BackupRecordService", return_value=mock_service),
            patch(
                "deployments.compose.backup.run_backup",
                AsyncMock(side_effect=RuntimeError("pg_dump failed")),
            ),
        ):
            with pytest.raises(RuntimeError, match="pg_dump failed"):
                await tasks_mod._backup_handler(job)

        mock_service.mark_failed.assert_called_once()

    async def test_backup_unknown_kind_raises(self) -> None:
        job = MagicMock()
        job.kind = "unknown"
        with pytest.raises(AppError, match="未注册的作业类型"):
            await tasks_mod._backup_handler(job)


class TestAuditExportHandler:
    """Tests for _audit_export_handler."""

    async def test_audit_export_with_filters(self) -> None:
        job = MagicMock()
        job.kind = "audit_export"
        job.payload = {
            "department_id": "00000000-0000-0000-0000-000000000040",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        }

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 42
        mock_session.execute.return_value = mock_result

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=MagicMock()),
            patch.object(tasks_mod, "session_scope", _fake_session_scope(mock_session)),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
            patch.object(tasks_mod, "get_system_guc", return_value=(None, None)),
        ):
            result = await tasks_mod._audit_export_handler(job)

        assert result["exported_count"] == 42
        assert result["status"] == "completed"

    async def test_audit_export_no_filters(self) -> None:
        job = MagicMock()
        job.kind = "audit_export"
        job.payload = {}

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute.return_value = mock_result

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=MagicMock()),
            patch.object(tasks_mod, "session_scope", _fake_session_scope(mock_session)),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
            patch.object(tasks_mod, "get_system_guc", return_value=(None, None)),
        ):
            result = await tasks_mod._audit_export_handler(job)

        assert result["exported_count"] == 0

    async def test_audit_export_unknown_kind_raises(self) -> None:
        job = MagicMock()
        job.kind = "unknown"
        with pytest.raises(AppError, match="未注册的作业类型"):
            await tasks_mod._audit_export_handler(job)


class TestResolveBackupDirById:
    """Tests for _resolve_backup_dir_by_id."""

    def test_dir_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tempfile

        non_existent = str(Path(tempfile.gettempdir()) / "irip-backup-nonexistent-xyz")
        monkeypatch.setenv("IRIP_BACKUP_OUTPUT_DIR", non_existent)
        with pytest.raises(AppError, match="备份目录不存在"):
            tasks_mod._resolve_backup_dir_by_id("00000000-0000-0000-0000-000000000050")

    def test_manifest_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIP_BACKUP_OUTPUT_DIR", str(tmp_path))
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "manifest.json").write_text(
            '{"backup_id": "other-id"}', encoding="utf-8"
        )
        with pytest.raises(AppError, match="未找到 backup_id"):
            tasks_mod._resolve_backup_dir_by_id("target-id")

    def test_manifest_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIP_BACKUP_OUTPUT_DIR", str(tmp_path))
        sub = tmp_path / "backup_dir"
        sub.mkdir()
        (sub / "manifest.json").write_text('{"backup_id": "target-id"}', encoding="utf-8")
        result = tasks_mod._resolve_backup_dir_by_id("target-id")
        assert result == sub

    def test_corrupt_manifest_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IRIP_BACKUP_OUTPUT_DIR", str(tmp_path))
        sub = tmp_path / "corrupt"
        sub.mkdir()
        (sub / "manifest.json").write_text("NOT JSON", encoding="utf-8")
        good = tmp_path / "good"
        good.mkdir()
        (good / "manifest.json").write_text('{"backup_id": "found"}', encoding="utf-8")
        result = tasks_mod._resolve_backup_dir_by_id("found")
        assert result == good


class TestDoExecuteJob:
    """Tests for _do_execute_job sync wrapper."""

    def test_delegates_to_asyncio_run(self) -> None:
        with patch.object(tasks_mod, "asyncio") as mock_aio:
            mock_aio.run.return_value = "job-result"
            result = tasks_mod._do_execute_job("job-1")
        assert result == "job-result"


class TestDoExecuteBeatTask:
    """Tests for _do_execute_beat_task sync wrapper."""

    def test_delegates_to_asyncio_run(self) -> None:
        with patch.object(tasks_mod, "asyncio") as mock_aio:
            mock_aio.run.return_value = "task-name"
            result = tasks_mod._do_execute_beat_task("task-name", "dept-1", None)
        assert result == "task-name"


class TestSentinelResolution:
    """Tests for get_root_dept_id, get_system_dept_id, get_system_service_user_id."""

    def test_root_dept_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._sentinel_id_cache.clear()
        monkeypatch.setenv("IRIP_ROOT_DEPT_ID", "00000000-0000-0000-0000-000000000060")
        assert tasks_mod.get_root_dept_id() == "00000000-0000-0000-0000-000000000060"
        tasks_mod._sentinel_id_cache.clear()

    def test_system_dept_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._sentinel_id_cache.clear()
        monkeypatch.setenv("IRIP_SYSTEM_DEPT_ID", "00000000-0000-0000-0000-000000000061")
        assert tasks_mod.get_system_dept_id() == "00000000-0000-0000-0000-000000000061"
        tasks_mod._sentinel_id_cache.clear()

    def test_system_service_user_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._sentinel_id_cache.clear()
        monkeypatch.setenv("IRIP_SYSTEM_SERVICE_USER_ID", "00000000-0000-0000-0000-000000000062")
        assert tasks_mod.get_system_service_user_id() == "00000000-0000-0000-0000-000000000062"
        tasks_mod._sentinel_id_cache.clear()

    def test_resolve_sentinel_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._sentinel_id_cache.clear()
        tasks_mod._sentinel_id_cache["root_dept"] = "cached-id"
        assert tasks_mod.get_root_dept_id() == "cached-id"
        tasks_mod._sentinel_id_cache.clear()

    def test_resolve_sentinel_db_failure_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tasks_mod._sentinel_id_cache.clear()
        monkeypatch.delenv("IRIP_ROOT_DEPT_ID", raising=False)
        monkeypatch.setattr(tasks_mod, "get_database_url", lambda *a, **kw: "")
        assert tasks_mod.get_root_dept_id() == ""
        tasks_mod._sentinel_id_cache.clear()


class TestParseUuidOrNone:
    """Tests for _parse_uuid_or_none."""

    def test_valid_uuid(self) -> None:
        uid = UUID("00000000-0000-0000-0000-000000000070")
        assert tasks_mod._parse_uuid_or_none(str(uid)) == uid

    def test_empty_string(self) -> None:
        assert tasks_mod._parse_uuid_or_none("") is None

    def test_invalid_string(self) -> None:
        assert tasks_mod._parse_uuid_or_none("not-a-uuid") is None


class TestGetSystemGuc:
    """Tests for get_system_guc."""

    def test_returns_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._sentinel_id_cache.clear()
        monkeypatch.setenv("IRIP_SYSTEM_DEPT_ID", "00000000-0000-0000-0000-000000000080")
        monkeypatch.setenv("IRIP_SYSTEM_SERVICE_USER_ID", "00000000-0000-0000-0000-000000000081")
        dept, user = tasks_mod.get_system_guc()
        assert dept == UUID("00000000-0000-0000-0000-000000000080")
        assert user == UUID("00000000-0000-0000-0000-000000000081")
        tasks_mod._sentinel_id_cache.clear()

    def test_returns_none_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tasks_mod._sentinel_id_cache.clear()
        monkeypatch.delenv("IRIP_SYSTEM_DEPT_ID", raising=False)
        monkeypatch.delenv("IRIP_SYSTEM_SERVICE_USER_ID", raising=False)
        monkeypatch.setattr(tasks_mod, "get_database_url", lambda *a, **kw: "")
        dept, user = tasks_mod.get_system_guc()
        assert dept is None
        assert user is None
        tasks_mod._sentinel_id_cache.clear()


class TestExecuteJobAsync:
    """Tests for _execute_job_async."""

    async def test_concurrent_limit_triggers_retry(self) -> None:
        job_id = "00000000-0000-0000-0000-000000000090"

        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_job.department_id = UUID("00000000-0000-0000-0000-000000000091")
        mock_session.scalar.return_value = mock_job

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.job_id = UUID(job_id)
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=mock_result)

        mock_limiter = MagicMock()
        mock_limiter.acquire.return_value = False  # Rate limited

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=mock_factory),
            patch.object(tasks_mod, "get_system_guc", return_value=(None, None)),
            patch.object(tasks_mod, "JobExecutor", return_value=mock_executor),
            patch.object(tasks_mod, "WorkerLeaseManager", return_value=MagicMock()),
            patch.object(tasks_mod, "_register_handlers"),
            patch.object(tasks_mod, "get_redis_url", return_value="redis://x"),
            patch(
                "packages.jobs.dept_concurrency.DeptConcurrencyLimiter", return_value=mock_limiter
            ),
            patch("redis.from_url", return_value=MagicMock()),
        ):
            with pytest.raises(AppError, match="部门并发上限"):
                await tasks_mod._execute_job_async(job_id)
        mock_limiter.acquire.assert_called_once()
        mock_limiter.release.assert_not_called()

    async def test_successful_execution(self) -> None:
        job_id = "00000000-0000-0000-0000-000000000092"

        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_job.department_id = UUID("00000000-0000-0000-0000-000000000093")
        mock_session.scalar.return_value = mock_job

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.job_id = UUID(job_id)
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=mock_result)

        mock_limiter = MagicMock()
        mock_limiter.acquire.return_value = True

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=mock_factory),
            patch.object(tasks_mod, "get_system_guc", return_value=(None, None)),
            patch.object(tasks_mod, "JobExecutor", return_value=mock_executor),
            patch.object(tasks_mod, "WorkerLeaseManager", return_value=MagicMock()),
            patch.object(tasks_mod, "_register_handlers"),
            patch.object(tasks_mod, "get_redis_url", return_value="redis://x"),
            patch(
                "packages.jobs.dept_concurrency.DeptConcurrencyLimiter", return_value=mock_limiter
            ),
            patch("redis.from_url", return_value=MagicMock()),
        ):
            result = await tasks_mod._execute_job_async(job_id)

        assert result == job_id
        mock_limiter.release.assert_called_once()

    async def test_no_department_skips_limiter(self) -> None:
        job_id = "00000000-0000-0000-0000-000000000094"

        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_job.department_id = None
        mock_session.scalar.return_value = mock_job

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.job_id = UUID(job_id)
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=mock_result)

        mock_limiter = MagicMock()

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=mock_factory),
            patch.object(tasks_mod, "get_system_guc", return_value=(None, None)),
            patch.object(tasks_mod, "JobExecutor", return_value=mock_executor),
            patch.object(tasks_mod, "WorkerLeaseManager", return_value=MagicMock()),
            patch.object(tasks_mod, "_register_handlers"),
            patch.object(tasks_mod, "get_redis_url", return_value="redis://x"),
            patch(
                "packages.jobs.dept_concurrency.DeptConcurrencyLimiter", return_value=mock_limiter
            ),
            patch("redis.from_url", return_value=MagicMock()),
        ):
            result = await tasks_mod._execute_job_async(job_id)

        assert result == job_id
        mock_limiter.acquire.assert_not_called()
        mock_limiter.release.assert_not_called()

    async def test_executor_returns_none(self) -> None:
        job_id = "00000000-0000-0000-0000-000000000095"

        mock_session = AsyncMock()
        mock_job = MagicMock()
        mock_job.department_id = UUID("00000000-0000-0000-0000-000000000096")
        mock_session.scalar.return_value = mock_job

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=None)  # No result

        mock_limiter = MagicMock()
        mock_limiter.acquire.return_value = True

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=mock_factory),
            patch.object(tasks_mod, "get_system_guc", return_value=(None, None)),
            patch.object(tasks_mod, "JobExecutor", return_value=mock_executor),
            patch.object(tasks_mod, "WorkerLeaseManager", return_value=MagicMock()),
            patch.object(tasks_mod, "_register_handlers"),
            patch.object(tasks_mod, "get_redis_url", return_value="redis://x"),
            patch(
                "packages.jobs.dept_concurrency.DeptConcurrencyLimiter", return_value=mock_limiter
            ),
            patch("redis.from_url", return_value=MagicMock()),
        ):
            result = await tasks_mod._execute_job_async(job_id)

        assert result == job_id  # Falls back to job_id when result is None
        mock_limiter.release.assert_called_once()


class TestExecuteBeatTaskAsync:
    """Tests for _execute_beat_task_async."""

    async def test_calls_handler(self) -> None:
        mock_session = AsyncMock()
        # Make begin() return an AsyncMock (supports async context manager)
        mock_session.begin = MagicMock(return_value=AsyncMock())
        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        handler = AsyncMock()

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=mock_factory),
            patch.object(tasks_mod, "_parse_uuid_or_none", return_value=None),
            patch.object(tasks_mod, "get_system_service_user_id", return_value=""),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
        ):
            result = await tasks_mod._execute_beat_task_async(
                "beat-task", "00000000-0000-0000-0000-0000000000b1", handler
            )

        assert result == "beat-task"
        handler.assert_called_once()

    async def test_none_handler_no_error(self) -> None:
        mock_session = AsyncMock()
        mock_session.begin = MagicMock(return_value=AsyncMock())
        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(tasks_mod, "_get_session_factory", return_value=mock_factory),
            patch.object(tasks_mod, "_parse_uuid_or_none", return_value=None),
            patch.object(tasks_mod, "get_system_service_user_id", return_value=""),
            patch("packages.common.tenant_guc.set_dept_guc", AsyncMock()),
            patch("packages.common.tenant_guc.set_user_guc", AsyncMock()),
        ):
            result = await tasks_mod._execute_beat_task_async(
                "beat-task", "00000000-0000-0000-0000-0000000000b2", None
            )

        assert result == "beat-task"


def _fake_session_scope(mock_session):
    """Build a fake session_scope contextmanager that yields mock_session."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake(_factory):
        yield mock_session

    return _fake
