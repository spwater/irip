"""实验室集成测试共享 fixtures。

自动清理前次测试运行残留的测试实验室（code 前缀 lab_），
避免重复编码冲突导致测试失败。
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture(autouse=True)
async def _cleanup_test_departments(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """每个测试前后清理残留的测试实验室（code 前缀 lab_）。"""
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text(
                    "DELETE FROM app_user_department WHERE department_id IN ("
                    "SELECT id FROM department WHERE parent_id IS NULL "
                    "AND code LIKE 'lab_%')"
                ),
            )
            await session.execute(
                sa.text("DELETE FROM department WHERE parent_id IS NULL AND code LIKE 'lab_%'"),
            )
    yield
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text(
                    "DELETE FROM app_user_department WHERE department_id IN ("
                    "SELECT id FROM department WHERE parent_id IS NULL "
                    "AND code LIKE 'lab_%')"
                ),
            )
            await session.execute(
                sa.text("DELETE FROM department WHERE parent_id IS NULL AND code LIKE 'lab_%'"),
            )
