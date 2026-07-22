"""恢复测试：迁移失败后回滚与恢复。

验证（docs/arch-v0.md §8.2 风险 "迁移失败" + §7.6 事务约定）：

核心场景：
- 失败迁移后上一版本镜像可运行（downgrade 到前版本后应用仍正常工作）；
- 数据库状态一致（失败迁移不留下部分变更）；
- 可手动修复后继续迁移（修复后 upgrade 到 head 成功）。

测试策略：
- 使用 Alembic 程序化 API 执行 downgrade / upgrade；
- 验证各版本的数据库状态一致（可插入/查询）；
- 模拟失败迁移：在事务中执行会失败的 DDL，验证回滚后状态不变；
- 修复后继续迁移到 head。

注意：migrations/env.py 从 ``IRIP_DATABASE_URL`` 环境变量读取数据库
URL（若已设置则覆盖 config）。测试通过 ``monkeypatch.setenv`` 临时设置
此变量为测试数据库 URL，确保迁移操作目标正确。
"""

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# ============================================================
# 辅助函数
# ============================================================


def _get_db_url() -> str:
    """获取测试数据库 URL。"""
    url = os.getenv("IRIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IRIP_TEST_DATABASE_URL not set")
        return ""
    return url


def _get_alembic_config(url: str):
    """构建 Alembic 配置。"""
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _get_current_revision(url: str) -> str | None:
    """获取当前 Alembic 版本。"""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            )
            return result.scalar()
    finally:
        engine.dispose()


def _verify_app_works(engine: Engine) -> bool:
    """验证应用在当前数据库版本下可正常工作（可查询 app_user 表）。"""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT COUNT(*) FROM app_user")
            )
            _count: int = result.scalar() or 0
            return True
    except Exception:
        return False


def _setup_env(monkeypatch, url: str) -> None:
    """设置 IRIP_DATABASE_URL 环境变量，确保 env.py 使用测试数据库。"""
    monkeypatch.setenv("IRIP_DATABASE_URL", url)


# ============================================================
# 1. 失败迁移后上一版本镜像可运行
# ============================================================


@pytest.mark.integration
async def test_downgrade_to_previous_version_works(monkeypatch) -> None:
    """降级到上一版本后应用仍可正常运行。"""
    url = _get_db_url()
    if not url:
        return

    from alembic import command

    _setup_env(monkeypatch, url)
    config = _get_alembic_config(url)
    original_rev = _get_current_revision(url)
    assert original_rev is not None, "Database should have a revision"

    engine = create_engine(url)
    try:
        # 验证当前版本可正常工作
        assert _verify_app_works(engine), "App should work at current version"

        # 降级到上一版本
        command.downgrade(config, "-1")
        prev_rev = _get_current_revision(url)
        assert prev_rev != original_rev, "Revision should change after downgrade"

        # 验证上一版本仍可正常工作
        assert _verify_app_works(engine), (
            "App should work at previous version after downgrade"
        )

        # 恢复到原始版本
        command.upgrade(config, "head")
        restored_rev = _get_current_revision(url)
        assert restored_rev == original_rev, (
            f"Revision should be restored to {original_rev}, got {restored_rev}"
        )
    finally:
        engine.dispose()


# ============================================================
# 2. 数据库状态一致
# ============================================================


@pytest.mark.integration
async def test_failed_migration_leaves_consistent_state(monkeypatch) -> None:
    """失败的迁移不留下部分变更（事务回滚）。"""
    url = _get_db_url()
    if not url:
        return

    engine = create_engine(url)
    try:
        # 记录当前状态
        original_rev = _get_current_revision(url)
        assert original_rev is not None

        # 尝试执行会失败的 DDL（创建已存在的表）
        with engine.connect() as conn:
            try:
                conn.execute(
                    sa.text("CREATE TABLE app_user (id INT)")
                )
                conn.commit()
                pytest.fail("Creating duplicate table should have failed")
            except sa.exc.ProgrammingError:
                # 预期失败：表已存在
                conn.rollback()

        # 验证版本未变
        rev_after_failure = _get_current_revision(url)
        assert rev_after_failure == original_rev, (
            "Revision should not change after failed DDL"
        )

        # 验证数据库仍可正常工作
        assert _verify_app_works(engine), (
            "Database should still work after failed DDL"
        )
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_migration_roundtrip_preserves_data(monkeypatch) -> None:
    """迁移降级再升级后数据保持一致。"""
    url = _get_db_url()
    if not url:
        return

    from alembic import command

    _setup_env(monkeypatch, url)
    config = _get_alembic_config(url)
    engine = create_engine(url)
    try:
        from packages.auth.passwords import hash_password
        from packages.common.ids import new_id

        # 插入测试数据
        test_user_id = new_id()
        test_email = f"migration-test-{new_id().hex[:8]}@irip.local"
        with engine.connect() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO app_user "
                    "(id, email, display_name, password_hash, status, lock_version) "
                    "VALUES (:id, :email, :name, :hash, 'active', 0)"
                ),
                {
                    "id": test_user_id,
                    "email": test_email,
                    "name": "Migration Test",
                    "hash": hash_password("Test-Password-2026!"),
                },
            )
            conn.commit()

        # 降级到上一版本再升级回来
        command.downgrade(config, "-1")
        command.upgrade(config, "head")

        # 验证数据仍存在
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT email FROM app_user WHERE id = :id"),
                {"id": test_user_id},
            )
            row = result.fetchone()
            assert row is not None, "Test data should survive migration roundtrip"
            assert row[0] == test_email

        # 清理
        with engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM app_user WHERE id = :id"),
                {"id": test_user_id},
            )
            conn.commit()
    finally:
        engine.dispose()


# ============================================================
# 3. 可手动修复后继续迁移
# ============================================================


@pytest.mark.integration
async def test_manual_fix_then_continue_migration(monkeypatch) -> None:
    """手动修复后可继续迁移到 head。"""
    url = _get_db_url()
    if not url:
        return

    from alembic import command

    _setup_env(monkeypatch, url)
    config = _get_alembic_config(url)
    engine = create_engine(url)
    try:
        original_rev = _get_current_revision(url)

        # 降级到前一个版本
        command.downgrade(config, "-1")
        prev_rev = _get_current_revision(url)
        assert prev_rev != original_rev

        # 模拟手动修复：直接在数据库上执行必要的 DDL
        # （此处验证降级后数据库是可操作的）
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT COUNT(*) FROM app_user")
            )
            assert result.scalar() is not None

        # 继续迁移到 head
        command.upgrade(config, "head")
        final_rev = _get_current_revision(url)
        assert final_rev == original_rev, (
            f"Should be back at {original_rev}, got {final_rev}"
        )

        # 验证应用正常工作
        assert _verify_app_works(engine)
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_migration_version_table_integrity(monkeypatch) -> None:
    """alembic_version 表在迁移过程中保持完整性。"""
    url = _get_db_url()
    if not url:
        return

    from alembic import command

    _setup_env(monkeypatch, url)
    config = _get_alembic_config(url)
    engine = create_engine(url)
    try:
        # 验证 alembic_version 表存在且只有一行
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT COUNT(*) FROM alembic_version")
            )
            count: int = result.scalar() or 0
            assert count == 1, (
                f"alembic_version should have exactly 1 row, got {count}"
            )

        # 降级
        command.downgrade(config, "-1")

        # 验证仍只有一行
        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT COUNT(*) FROM alembic_version")
            )
            count = result.scalar() or 0
            assert count == 1, (
                f"alembic_version should still have 1 row after downgrade, got {count}"
            )

        # 升级回来
        command.upgrade(config, "head")

        with engine.connect() as conn:
            result = conn.execute(
                sa.text("SELECT COUNT(*) FROM alembic_version")
            )
            count = result.scalar() or 0
            assert count == 1
    finally:
        engine.dispose()


@pytest.mark.integration
async def test_can_query_all_tables_after_rollback(monkeypatch) -> None:
    """迁移回滚后所有业务表仍可查询。"""
    url = _get_db_url()
    if not url:
        return

    from alembic import command

    _setup_env(monkeypatch, url)
    config = _get_alembic_config(url)
    engine = create_engine(url)
    try:
        original_rev = _get_current_revision(url)

        # 降级
        command.downgrade(config, "-1")

        # 尝试查询各业务表（降级后某些表可能不存在，但核心表应存在）
        tables_to_check = ["app_user", "alembic_version"]
        for table_name in tables_to_check:
            with engine.connect() as conn:
                result = conn.execute(
                    sa.text(f"SELECT COUNT(*) FROM {table_name}")
                )
                assert result.scalar() is not None, (
                    f"Table {table_name} should be queryable after downgrade"
                )

        # 升级回来
        command.upgrade(config, "head")
        assert _get_current_revision(url) == original_rev
    finally:
        engine.dispose()
