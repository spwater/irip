"""Recovery 测试共享 conftest。

确保 alembic.ini 的相对路径 script_location=migrations 可被正确解析，
无论 pytest 从哪个目录启动。
对需要 pg_dump 的备份恢复测试，检查工具是否可用，否则 skip。
"""

import shutil
import subprocess
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


def _docker_daemon_running() -> bool:
    """检查 Docker daemon 是否正在运行。

    通过 ``docker info`` 命令判断 daemon 是否可用。
    docker CLI 不存在或 daemon 未启动时返回 False。
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _has_docker_compose() -> bool:
    """检查是否具备运行 backup/restore 测试的完整 Docker 环境。

    需要满足：
    1. docker CLI 可用；
    2. Docker daemon 正在运行；
    3. pg_dump 可用（备份子进程需要）。

    满足全部条件时返回 True，使条件 skip 不触发（测试实际运行）。
    缺少任一条件时返回 False，测试被条件 skip。
    """
    return _docker_daemon_running() and _has_pg_dump()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """自动给需要 pg_dump / Docker 的测试标记条件 skip。

    备份恢复测试需要 pg_dump + pg_restore + 独立 DB 容器，
    在完整 Docker compose 环境（backup/restore profile）中运行。

    - test_backup_restore: 需要 Docker daemon + pg_dump（条件 skip）；
    - test_minio_outage: 仅需 pg_dump（条件 skip）。
    """
    _skip_backup = pytest.mark.skipif(
        not _has_pg_dump(),
        reason="pg_dump not found; install postgresql@16 or run inside Docker container",
    )
    # 条件 skip：仅当 Docker 环境 + pg_dump 不可用时才跳过
    _skip_full_docker = pytest.mark.skipif(
        not _has_docker_compose(),
        reason="backup/restore tests require Docker compose environment "
        "(pg_dump + pg_restore + isolated DB); "
        "run via 'docker compose --profile dangerous-ops run backup'",
    )
    for item in items:
        if "test_backup_restore" in item.nodeid:
            item.add_marker(_skip_full_docker)
        if "test_minio_outage" in item.nodeid:
            item.add_marker(_skip_backup)
