"""IRIP 备份领域包。

提供备份记录的 ORM 模型与业务服务：
- ``BackupRecord``: 备份元数据 ORM 模型（对应 backup_record 表）；
- ``BackupType`` / ``BackupStatus``: 备份类型与状态枚举；
- ``BackupRecordService``: 备份记录 CRUD + 保留策略清理。
"""

from packages.backups.entities import BackupRecord, BackupStatus, BackupType
from packages.backups.service import BackupRecordService

__all__ = [
    "BackupRecord",
    "BackupStatus",
    "BackupType",
    "BackupRecordService",
]
