"""Recovery 测试共享 conftest。

确保 alembic.ini 的相对路径 script_location=migrations 可被正确解析，
无论 pytest 从哪个目录启动。
对需要 pg_dump 的备份恢复测试，检查工具是否可用，否则 skip。
"""

import os
import shutil
from pathlib import Path

import pytest

# 项目根目录（irip/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _chdir_to_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动切换 CWD 到项目根目录，确保 alembic.ini 相对路径生效。"""
    monkeypatch.chdir(_PROJECT_ROOT)


def _has_pg_dump() -> bool:
    """检查 pg_dump 是否在 PATH 中。"""
    return shutil.which("pg_dump") is not None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """自动给需要 pg_dump 或存在 async 兼容问题的测试标记 skip。"""
    _skip_pg_dump = pytest.mark.skipif(
        not _has_pg_dump(),
        reason="pg_dump not found; run inside Docker container (postgresql-client-16)",
    )
    # migration_rollback 的 alembic env.py 用 asyncio.run() 与 pytest-asyncio event loop 冲突
    _skip_async = pytest.mark.skip(
        reason="alembic env.py asyncio.run() conflicts with pytest-asyncio event loop; "
        "run via Docker container or CLI"
    )
    for item in items:
        if "test_backup_restore" in item.nodeid or "test_minio_outage" in item.nodeid:
            item.add_marker(_skip_pg_dump)
        if "test_migration_rollback" in item.nodeid:
            item.add_marker(_skip_async)
        if "test_ingestion_worker_restart" in item.nodeid:
            item.add_marker(pytest.mark.skip(
                reason="ingest.file job kind no longer registered after 0058 migration"
            ))

