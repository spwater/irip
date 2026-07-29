"""内置组件单元测试共享 fixtures。

提供构建 ComponentContext 的辅助函数与 fixture，
使组件测试无需数据库/MinIO/Redis 即可运行。
"""

import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from packages.common.clock import FixedClock
from packages.components.sdk import ComponentContext


def make_test_context(
    secrets: dict[str, str] | None = None,
    artifact_service=None,
    workdir: Path | None = None,
) -> ComponentContext:
    """构建测试用 ComponentContext。

    Args:
        secrets: 密钥字典（可选）。
        artifact_service: 工件服务（可选）。
        workdir: 临时工作目录（可选，默认自动创建）。

    Returns:
        ComponentContext: 测试上下文。
    """
    return ComponentContext(
        organization_id=uuid4(),
        user_id=uuid4(),
        clock=FixedClock(
            __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").UTC)
        ),  # noqa: E501
        artifact_service=artifact_service,
        job_id=uuid4(),
        cancel_event=asyncio.Event(),
        secrets=secrets or {},
        workdir=workdir or Path(tempfile.mkdtemp()),
    )


@pytest.fixture
def test_context() -> ComponentContext:
    """提供测试用 ComponentContext。"""
    return make_test_context()


@pytest.fixture
def test_context_with_secrets() -> ComponentContext:
    """提供带密钥的测试用 ComponentContext。"""
    return make_test_context(
        secrets={
            "host": "localhost",
            "port": "5432",
            "database": "testdb",
            "user": "testuser",
            "password": "s3cret_passw0rd",
        }
    )
