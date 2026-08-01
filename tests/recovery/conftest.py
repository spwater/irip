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
    """自动给需要 pg_dump 的测试标记 skip。

    备份恢复测试需要 pg_dump + pg_restore + 独立 DB 容器，
    在完整 Docker compose 环境（backup/restore profile）中运行。
    本地 brew pg_dump 虽然可用，但测试还需要 pg_restore 到独立 DB，
    仅在 Docker 容器内才能满足完整链路。
    """
    _skip_backup = pytest.mark.skipif(
        not _has_pg_dump(),
        reason="pg_dump not found; install postgresql@16 or run inside Docker container",
    )
    # 即使 pg_dump 可用，backup_restore 需要完整 Docker 链路（pg_restore 到独立 DB）
    _skip_full_docker = pytest.mark.skip(
        reason="backup/restore tests require full Docker compose environment "
        "(pg_dump + pg_restore + isolated DB); run via 'docker compose --profile dangerous-ops run backup'"
    )
    for item in items:
        if "test_backup_restore" in item.nodeid:
            item.add_marker(_skip_full_docker)
        if "test_minio_outage" in item.nodeid:
            item.add_marker(_skip_backup)

