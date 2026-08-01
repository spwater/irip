"""BackupRecord ORM + BackupMethod 枚举单元测试（PITR 升级）。

验证 packages/backups/entities.py 的 PITR 升级变更：
- BackupMethod 枚举值（PITR / PG_DUMP）；
- BackupRecord 新增 5 个 Mapped 字段定义；
- backup_method 字段 server_default='pitr'。

对应 docs/arch-db-backup-pitr-upgrade.md §3.1 / §3.4。
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Mapped

from packages.backups.entities import BackupMethod, BackupRecord, BackupStatus, BackupType


# ============================================================
# BackupMethod 枚举
# ============================================================


class TestBackupMethodEnum:
    """BackupMethod 枚举值测试。"""

    def test_pitr_value(self) -> None:
        """BackupMethod.PITR 的值为 'pitr'。"""
        assert BackupMethod.PITR.value == "pitr"

    def test_pg_dump_value(self) -> None:
        """BackupMethod.PG_DUMP 的值为 'pg_dump'。"""
        assert BackupMethod.PG_DUMP.value == "pg_dump"

    def test_is_strenum(self) -> None:
        """BackupMethod 继承自 StrEnum（字符串比较兼容）。"""
        assert BackupMethod.PITR == "pitr"
        assert BackupMethod.PG_DUMP == "pg_dump"

    def test_two_members(self) -> None:
        """BackupMethod 恰好有 2 个成员。"""
        members = list(BackupMethod)
        assert len(members) == 2

    def test_values_set(self) -> None:
        """BackupMethod 的值集合为 {'pitr', 'pg_dump'}。"""
        values = {m.value for m in BackupMethod}
        assert values == {"pitr", "pg_dump"}


# ============================================================
# BackupRecord 新增字段
# ============================================================


class TestBackupRecordPitrFields:
    """BackupRecord 新增 5 个 PITR 字段定义测试。"""

    def test_backup_timestamp_field_exists(self) -> None:
        """BackupRecord 有 backup_timestamp 字段。"""
        assert hasattr(BackupRecord, "backup_timestamp")

    def test_wal_start_lsn_field_exists(self) -> None:
        """BackupRecord 有 wal_start_lsn 字段。"""
        assert hasattr(BackupRecord, "wal_start_lsn")

    def test_wal_end_lsn_field_exists(self) -> None:
        """BackupRecord 有 wal_end_lsn 字段。"""
        assert hasattr(BackupRecord, "wal_end_lsn")

    def test_recovery_target_time_field_exists(self) -> None:
        """BackupRecord 有 recovery_target_time 字段。"""
        assert hasattr(BackupRecord, "recovery_target_time")

    def test_backup_method_field_exists(self) -> None:
        """BackupRecord 有 backup_method 字段。"""
        assert hasattr(BackupRecord, "backup_method")

    def test_backup_method_server_default_pitr(self) -> None:
        """backup_method 字段的 server_default 为 'pitr'。"""
        col = BackupRecord.__table__.columns.get("backup_method")
        assert col is not None
        assert col.server_default is not None
        # server_default 可能是 text('pitr') 或 'pitr'
        default_text = str(col.server_default.arg) if hasattr(col.server_default, "arg") else str(col.server_default)
        assert "pitr" in default_text

    def test_backup_method_not_nullable(self) -> None:
        """backup_method 字段为 NOT NULL。"""
        col = BackupRecord.__table__.columns.get("backup_method")
        assert col is not None
        assert col.nullable is False

    def test_backup_timestamp_nullable(self) -> None:
        """backup_timestamp 字段为 nullable。"""
        col = BackupRecord.__table__.columns.get("backup_timestamp")
        assert col is not None
        assert col.nullable is True

    def test_wal_start_lsn_nullable(self) -> None:
        """wal_start_lsn 字段为 nullable。"""
        col = BackupRecord.__table__.columns.get("wal_start_lsn")
        assert col is not None
        assert col.nullable is True

    def test_wal_end_lsn_nullable(self) -> None:
        """wal_end_lsn 字段为 nullable。"""
        col = BackupRecord.__table__.columns.get("wal_end_lsn")
        assert col is not None
        assert col.nullable is True

    def test_recovery_target_time_nullable(self) -> None:
        """recovery_target_time 字段为 nullable。"""
        col = BackupRecord.__table__.columns.get("recovery_target_time")
        assert col is not None
        assert col.nullable is True


# ============================================================
# BackupRecord 字段类型验证
# ============================================================


class TestBackupRecordFieldTypes:
    """BackupRecord 新增字段类型验证。"""

    def test_wal_lsn_is_string(self) -> None:
        """wal_start_lsn 和 wal_end_lsn 为 String 类型。"""
        start_col = BackupRecord.__table__.columns.get("wal_start_lsn")
        end_col = BackupRecord.__table__.columns.get("wal_end_lsn")
        assert isinstance(start_col.type, sa.String)
        assert isinstance(end_col.type, sa.String)

    def test_backup_method_is_string(self) -> None:
        """backup_method 为 String 类型。"""
        col = BackupRecord.__table__.columns.get("backup_method")
        assert isinstance(col.type, sa.String)

    def test_backup_timestamp_is_datetime(self) -> None:
        """backup_timestamp 为 datetime 类型（UTCDateTime）。"""
        col = BackupRecord.__table__.columns.get("backup_timestamp")
        # UTCDateTime 继承自 TIMESTAMP(timezone=True)
        assert col is not None
        assert "TIMESTAMP" in str(type(col.type)) or "DateTime" in str(type(col.type))

    def test_recovery_target_time_is_datetime(self) -> None:
        """recovery_target_time 为 datetime 类型。"""
        col = BackupRecord.__table__.columns.get("recovery_target_time")
        assert col is not None
        assert "TIMESTAMP" in str(type(col.type)) or "DateTime" in str(type(col.type))


# ============================================================
# 现有枚举不受影响（回归）
# ============================================================


class TestExistingEnumsRegression:
    """现有枚举不受 PITR 升级影响。"""

    def test_backup_type_unchanged(self) -> None:
        """BackupType 枚举值不变。"""
        assert BackupType.DAILY.value == "daily"
        assert BackupType.MILESTONE.value == "milestone"
        assert BackupType.PRE_RESTORE.value == "pre_restore"

    def test_backup_status_unchanged(self) -> None:
        """BackupStatus 枚举值不变。"""
        assert BackupStatus.PENDING.value == "pending"
        assert BackupStatus.SUCCEEDED.value == "succeeded"
        assert BackupStatus.FAILED.value == "failed"

    def test_existing_fields_unchanged(self) -> None:
        """现有字段定义不受影响。"""
        assert hasattr(BackupRecord, "id")
        assert hasattr(BackupRecord, "backup_type")
        assert hasattr(BackupRecord, "status")
        assert hasattr(BackupRecord, "file_path")
        assert hasattr(BackupRecord, "sha256")
        assert hasattr(BackupRecord, "created_at")
        assert hasattr(BackupRecord, "expires_at")
