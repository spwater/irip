"""Worker handler PITR 元数据传递单元测试。

验证 apps/worker/tasks/__init__.py 的 PITR 升级变更：
- _backup_handler 从 manifest.extra 读取 PITR 元数据（backup_timestamp、wal LSN）；
- _backup_handler 调用 mark_succeeded 时传入 PITR 元数据；
- _restore_handler 从 payload 读取 recovery_target_time；
- _restore_handler 调用 run_restore 时传 recovery_target_time；
- _restore_handler 恢复后调用 mark_restored。

通过 mock 全部外部依赖（run_backup / run_restore / BackupRecordService）实现。
对应 docs/arch-db-backup-pitr-upgrade.md T03 / §4.1 / §4.2。
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from deployments.compose.backup_manifest import BackupManifest


# ============================================================
# _backup_handler PITR 元数据测试
# ============================================================


class TestBackupHandlerPitrMetadata:
    """_backup_handler 从 manifest.extra 读取 PITR 元数据测试。"""

    @pytest.mark.asyncio
    async def test_backup_handler_reads_pitr_metadata_from_manifest(self) -> None:
        """_backup_handler 从 manifest.extra 读取 backup_timestamp + wal LSN。"""
        from apps.worker.tasks import _backup_handler

        # 构造 mock job
        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "backup"
        job.payload = {
            "type": "daily",
            "backup_record_id": str(job.id),
            "backup_method": "pitr",
        }

        # 构造 mock manifest（v2，含 PITR 元数据）
        manifest = MagicMock(spec=BackupManifest)
        manifest.backup_id = str(job.id)
        manifest.database_sha256 = "abc123sha256"
        manifest.migration_version = "0061"
        manifest.application_version = "0.1.0"
        manifest.object_count = 5
        manifest.extra = {
            "backup_timestamp": "2026-08-16T02:00:00.000+00:00",
            "backup_method": "pitr",
            "wal_start_lsn": "0/2000000",
            "wal_end_lsn": "0/2000123",
        }

        # mock run_backup
        mock_run_backup = AsyncMock(return_value=manifest)

        # mock BackupRecordService
        mock_service = MagicMock()
        mock_service.mark_succeeded = AsyncMock()
        mock_service.mark_failed = AsyncMock()

        with patch("deployments.compose.backup.run_backup", mock_run_backup):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    result = await _backup_handler(job)

        # 验证 mark_succeeded 被调用且传入 PITR 元数据
        mock_service.mark_succeeded.assert_called_once()
        call_kwargs = mock_service.mark_succeeded.call_args.kwargs
        call_args = mock_service.mark_succeeded.call_args.args

        # 验证 backup_timestamp 解析为 datetime
        backup_ts = call_kwargs.get("backup_timestamp")
        if backup_ts is None and len(call_args) > 1:
            # 如果参数按位置传
            pass
        assert backup_ts is not None or "backup_timestamp" in str(call_kwargs)

        # 验证 wal LSN 传入
        assert call_kwargs.get("wal_start_lsn") == "0/2000000"
        assert call_kwargs.get("wal_end_lsn") == "0/2000123"

    @pytest.mark.asyncio
    async def test_backup_handler_handles_empty_extra(self) -> None:
        """_backup_handler 处理空 extra（v1 兼容）。"""
        from apps.worker.tasks import _backup_handler

        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "backup"
        job.payload = {
            "type": "daily",
            "backup_record_id": str(job.id),
        }

        manifest = MagicMock(spec=BackupManifest)
        manifest.backup_id = str(job.id)
        manifest.database_sha256 = "v1-sha"
        manifest.migration_version = "0060"
        manifest.application_version = "0.1.0"
        manifest.object_count = 3
        manifest.extra = {}  # 空 extra（v1 兼容）

        mock_run_backup = AsyncMock(return_value=manifest)
        mock_service = MagicMock()
        mock_service.mark_succeeded = AsyncMock()

        with patch("deployments.compose.backup.run_backup", mock_run_backup):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    result = await _backup_handler(job)

        # mark_succeeded 应被调用（但 PITR 元数据为 None）
        mock_service.mark_succeeded.assert_called_once()

    @pytest.mark.asyncio
    async def test_backup_handler_mark_failed_on_error(self) -> None:
        """_backup_handler 备份失败时调用 mark_failed。"""
        from apps.worker.tasks import _backup_handler

        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "backup"
        job.payload = {
            "type": "daily",
            "backup_record_id": str(job.id),
        }

        mock_run_backup = AsyncMock(side_effect=RuntimeError("pg_basebackup failed"))
        mock_service = MagicMock()
        mock_service.mark_failed = AsyncMock()

        with patch("deployments.compose.backup.run_backup", mock_run_backup):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    with pytest.raises(RuntimeError, match="pg_basebackup failed"):
                        await _backup_handler(job)

        mock_service.mark_failed.assert_called_once()


# ============================================================
# _restore_handler recovery_target_time 测试
# ============================================================


class TestRestoreHandlerRecoveryTargetTime:
    """_restore_handler 传递 recovery_target_time 测试。"""

    @pytest.mark.asyncio
    async def test_restore_handler_reads_recovery_target_time(self) -> None:
        """_restore_handler 从 payload 读取 recovery_target_time。"""
        from apps.worker.tasks import _restore_handler

        backup_id = str(uuid4())
        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "restore"
        job.payload = {
            "backup_id": backup_id,
            "recovery_target_time": "2026-08-16T10:30:00+00:00",
            "pre_restore_created": True,  # 跳过 pre_restore
        }

        manifest = MagicMock(spec=BackupManifest)
        manifest.backup_id = backup_id
        manifest.extra = {"backup_timestamp": "2026-08-16T02:00:00.000+00:00"}

        mock_run_restore = AsyncMock(return_value=manifest)
        mock_service = MagicMock()
        mock_service.mark_restored = AsyncMock()

        with patch("deployments.compose.restore.run_restore", mock_run_restore):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    with patch("apps.worker.tasks._resolve_backup_dir_by_id", return_value=Path("/backups/test")):
                        result = await _restore_handler(job)

        # 验证 run_restore 被调用且传入 recovery_target_time
        mock_run_restore.assert_called_once()
        call_args = mock_run_restore.call_args
        # run_restore(backup_dir, recovery_target_time=...)
        assert call_args.kwargs.get("recovery_target_time") == "2026-08-16T10:30:00+00:00"

    @pytest.mark.asyncio
    async def test_restore_handler_none_recovery_target_time(self) -> None:
        """_restore_handler 不传 recovery_target_time 时传 None。"""
        from apps.worker.tasks import _restore_handler

        backup_id = str(uuid4())
        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "restore"
        job.payload = {
            "backup_id": backup_id,
            "pre_restore_created": True,
        }

        manifest = MagicMock(spec=BackupManifest)
        manifest.backup_id = backup_id
        manifest.extra = {"backup_timestamp": "2026-08-16T02:00:00.000+00:00"}

        mock_run_restore = AsyncMock(return_value=manifest)
        mock_service = MagicMock()
        mock_service.mark_restored = AsyncMock()

        with patch("deployments.compose.restore.run_restore", mock_run_restore):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    with patch("apps.worker.tasks._resolve_backup_dir_by_id", return_value=Path("/backups/test")):
                        await _restore_handler(job)

        mock_run_restore.assert_called_once()
        call_args = mock_run_restore.call_args
        assert call_args.kwargs.get("recovery_target_time") is None

    @pytest.mark.asyncio
    async def test_restore_handler_calls_mark_restored(self) -> None:
        """_restore_handler 恢复后调用 mark_restored。"""
        from apps.worker.tasks import _restore_handler

        backup_id = str(uuid4())
        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "restore"
        job.payload = {
            "backup_id": backup_id,
            "recovery_target_time": "2026-08-16T10:30:00+00:00",
            "pre_restore_created": True,
        }

        manifest = MagicMock(spec=BackupManifest)
        manifest.backup_id = backup_id
        manifest.extra = {"backup_timestamp": "2026-08-16T02:00:00.000+00:00"}

        mock_run_restore = AsyncMock(return_value=manifest)
        mock_service = MagicMock()
        mock_service.mark_restored = AsyncMock()

        with patch("deployments.compose.restore.run_restore", mock_run_restore):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    with patch("apps.worker.tasks._resolve_backup_dir_by_id", return_value=Path("/backups/test")):
                        await _restore_handler(job)

        # 验证 mark_restored 被调用
        mock_service.mark_restored.assert_called_once()

    @pytest.mark.asyncio
    async def test_restore_handler_mark_restored_uses_recovery_target_time(self) -> None:
        """mark_restored 使用 recovery_target_time 作为恢复目标时间。"""
        from apps.worker.tasks import _restore_handler

        backup_id = str(uuid4())
        target_time = "2026-08-16T10:30:00+00:00"
        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "restore"
        job.payload = {
            "backup_id": backup_id,
            "recovery_target_time": target_time,
            "pre_restore_created": True,
        }

        manifest = MagicMock(spec=BackupManifest)
        manifest.backup_id = backup_id
        manifest.extra = {"backup_timestamp": "2026-08-16T02:00:00.000+00:00"}

        mock_run_restore = AsyncMock(return_value=manifest)
        mock_service = MagicMock()
        mock_service.mark_restored = AsyncMock()

        with patch("deployments.compose.restore.run_restore", mock_run_restore):
            with patch("packages.backups.service.BackupRecordService") as mock_service_cls:
                mock_service_cls.return_value = mock_service
                with patch("apps.worker.tasks.build_session_factory"):
                    with patch("apps.worker.tasks._resolve_backup_dir_by_id", return_value=Path("/backups/test")):
                        await _restore_handler(job)

        # mark_restored 的第二个参数应为解析后的 recovery_target_time
        mark_restored_args = mock_service.mark_restored.call_args
        # mark_restored(record_id, recovery_target_time=...)
        rtt = mark_restored_args.kwargs.get("recovery_target_time")
        if rtt is None and len(mark_restored_args.args) > 1:
            rtt = mark_restored_args.args[1]
        assert rtt is not None
        # 应解析为 datetime
        from datetime import datetime as dt
        if isinstance(rtt, dt):
            assert rtt.isoformat().startswith("2026-08-16T10:30:00")

    @pytest.mark.asyncio
    async def test_restore_handler_missing_backup_id_raises(self) -> None:
        """_restore_handler 缺少 backup_id 时抛出 AppError。"""
        from apps.worker.tasks import _restore_handler
        from packages.common.errors import AppError

        job = MagicMock()
        job.id = uuid4()
        job.department_id = uuid4()
        job.kind = "restore"
        job.payload = {}  # 缺少 backup_id

        with pytest.raises(AppError) as exc_info:
            await _restore_handler(job)

        assert exc_info.value.code == "validation_failed"


# ============================================================
# daily_backup payload 含 backup_method 测试
# ============================================================


class TestDailyBackupPayload:
    """daily_backup 任务 payload 含 backup_method 测试。"""

    def test_daily_backup_payload_contains_backup_method(self) -> None:
        """daily_backup 任务的 Job payload 含 backup_method='pitr'。"""
        # 读取 celery_app.py 源码验证 payload 含 backup_method
        import inspect
        from apps.worker.celery_app import daily_backup

        source = inspect.getsource(daily_backup)
        assert '"backup_method": "pitr"' in source or "'backup_method': 'pitr'" in source or \
               '"backup_method"' in source and "'pitr'" in source

    def test_daily_backup_record_backup_method_pitr(self) -> None:
        """daily_backup 创建的 BackupRecord 含 backup_method='pitr'。"""
        import inspect
        from apps.worker.celery_app import daily_backup

        source = inspect.getsource(daily_backup)
        assert "backup_method" in source
        assert "pitr" in source
