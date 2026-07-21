"""数据库迁移引导测试（实施计划 Task 3 Step 1）。

验证 0001_platform_base 迁移：
- pgcrypto / vector 扩展已启用；
- alembic_version 表有且仅有 1 行（一次迁移）。

前置：测试数据库已启动且 ``alembic upgrade head`` 已执行。
"""

import sqlalchemy as sa


def test_migrations_enable_required_extensions(sync_engine) -> None:
    """迁移后 pgcrypto + vector 扩展存在，alembic_version 恰好 1 行。"""
    with sync_engine.connect() as conn:
        names = set(conn.execute(sa.text("select extname from pg_extension")).scalars())
        assert {"pgcrypto", "vector"}.issubset(names)
        assert conn.execute(sa.text("select count(*) from alembic_version")).scalar_one() == 1
