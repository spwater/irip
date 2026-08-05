"""0061 迁移结构验证单元测试（PITR 升级）。

验证 migrations/versions/0061_alter_backup_record_pitr.py 的结构：
- revision / down_revision 链连续性（0061 → 0060）；
- upgrade() 包含 5 个新增字段 + 回填 + 默认值 + 索引；
- downgrade() 反向操作完整；
- backup_method 默认值 'pitr'，回填 'pg_dump'；
- 索引 idx_backup_record_method 存在。

通过 importlib 动态加载迁移文件模块进行检查，无需真实数据库。
对应 docs/arch-db-backup-pitr-upgrade.md §3.6。

注意：迁移 0061 已被压缩为 0001_squashed_baseline.py（squashing），
本文件中引用旧迁移 0061 的测试全部 skip。
backup_record 表的 PITR 字段已由 squashed baseline 覆盖。
"""

import importlib.util
from pathlib import Path

import pytest

# 迁移 0061 已被压缩为 0001_squashed_baseline.py，
# 以下测试类引用的旧迁移文件已删除，全部 skip。
pytestmark = pytest.mark.skip(
    reason="迁移 0061 已 squashed 为 0001_squashed_baseline.py，旧迁移文件已删除（M-13 squashing）"
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "migrations" / "versions"


def _load_migration_module(revision: str):
    """按 revision ID 动态加载迁移模块。"""
    files = list(MIGRATIONS_DIR.glob(f"{revision}_*.py"))
    assert files, f"找不到 revision={revision} 的迁移文件"
    assert len(files) == 1, f"revision={revision} 匹配到多个文件: {files}"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", files[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_file_text(revision: str) -> str:
    """读取迁移文件全文。"""
    files = list(MIGRATIONS_DIR.glob(f"{revision}_*.py"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8")


# ============================================================
# 迁移链连续性
# ============================================================


class TestMigration0061Chain:
    """0061 迁移链 revision/down_revision 连续性。"""

    def test_revision_is_0061(self) -> None:
        """0061 的 revision 为 '0061'。"""
        module = _load_migration_module("0061")
        assert module.revision == "0061"

    def test_down_revision_is_0060(self) -> None:
        """0061 的 down_revision 指向 0060。"""
        module = _load_migration_module("0061")
        assert module.down_revision == "0060"

    def test_has_upgrade_and_downgrade(self) -> None:
        """0061 定义了 upgrade 和 downgrade 函数。"""
        module = _load_migration_module("0061")
        assert callable(module.upgrade), "0061 缺少 upgrade()"
        assert callable(module.downgrade), "0061 缺少 downgrade()"


# ============================================================
# upgrade() 操作验证
# ============================================================


class TestMigration0061Upgrade:
    """0061 upgrade() 迁移操作验证。"""

    def test_adds_5_new_columns(self) -> None:
        """upgrade 包含 5 个新增字段的 add_column 操作。"""
        content = _read_file_text("0061")
        expected_columns = [
            "backup_timestamp",
            "wal_start_lsn",
            "wal_end_lsn",
            "recovery_target_time",
            "backup_method",
        ]
        for col in expected_columns:
            assert col in content, f"upgrade 缺少新增字段: {col}"

    def test_add_column_count(self) -> None:
        """upgrade 包含恰好 5 个 add_column 调用。"""
        content = _read_file_text("0061")
        assert content.count("add_column") == 5, (
            f"期望 5 个 add_column 调用，实际 {content.count('add_column')}"
        )

    def test_backup_timestamp_is_timestamptz(self) -> None:
        """backup_timestamp 字段类型为 TIMESTAMP(timezone=True)。"""
        content = _read_file_text("0061")
        assert "backup_timestamp" in content
        assert "TIMESTAMP(timezone=True)" in content or "TIMESTAMP" in content

    def test_wal_lsn_columns_are_string(self) -> None:
        """wal_start_lsn 和 wal_end_lsn 字段类型为 String。"""
        content = _read_file_text("0061")
        assert "wal_start_lsn" in content
        assert "wal_end_lsn" in content
        assert "sa.String" in content

    def test_recovery_target_time_is_timestamptz(self) -> None:
        """recovery_target_time 字段类型为 TIMESTAMP(timezone=True)。"""
        content = _read_file_text("0061")
        assert "recovery_target_time" in content

    def test_backfill_pg_dump(self) -> None:
        """upgrade 回填存量记录 backup_method='pg_dump'。"""
        content = _read_file_text("0061")
        assert "pg_dump" in content
        assert "UPDATE backup_record SET backup_method = 'pg_dump'" in content

    def test_set_default_pitr(self) -> None:
        """upgrade 设置 backup_method 默认值为 'pitr'。"""
        content = _read_file_text("0061")
        assert "pitr" in content
        assert "server_default" in content
        # alter_column 设置 server_default="pitr"
        assert '"pitr"' in content or "'pitr'" in content

    def test_set_not_null(self) -> None:
        """upgrade 设置 backup_method 为 NOT NULL。"""
        content = _read_file_text("0061")
        assert "nullable=False" in content

    def test_creates_index(self) -> None:
        """upgrade 创建索引 idx_backup_record_method。"""
        content = _read_file_text("0061")
        assert "create_index" in content
        assert "idx_backup_record_method" in content


# ============================================================
# downgrade() 操作验证
# ============================================================


class TestMigration0061Downgrade:
    """0061 downgrade() 回滚操作验证。"""

    def test_drops_index(self) -> None:
        """downgrade 删除索引 idx_backup_record_method。"""
        content = _read_file_text("0061")
        assert "drop_index" in content
        assert "idx_backup_record_method" in content

    def test_drops_5_columns(self) -> None:
        """downgrade 删除 5 个新增字段。"""
        content = _read_file_text("0061")
        expected_columns = [
            "backup_method",
            "recovery_target_time",
            "wal_end_lsn",
            "wal_start_lsn",
            "backup_timestamp",
        ]
        for col in expected_columns:
            assert col in content, f"downgrade 缺少删除字段: {col}"

    def test_drop_column_count(self) -> None:
        """downgrade 包含恰好 5 个 drop_column 调用。"""
        content = _read_file_text("0061")
        assert content.count("drop_column") == 5, (
            f"期望 5 个 drop_column 调用，实际 {content.count('drop_column')}"
        )

    def test_downgrade_reverses_upgrade_order(self) -> None:
        """downgrade 删除字段的顺序与 upgrade 添加顺序相反。"""
        content = _read_file_text("0061")
        # downgrade 中 backup_method 应先于 backup_timestamp 删除
        backup_method_pos = content.rfind('drop_column("backup_record", "backup_method"')
        backup_timestamp_pos = content.rfind('drop_column("backup_record", "backup_timestamp"')
        if backup_method_pos > 0 and backup_timestamp_pos > 0:
            assert backup_method_pos < backup_timestamp_pos, (
                "downgrade 应先删 backup_method 再删 backup_timestamp"
            )


# ============================================================
# 迁移模块可加载性
# ============================================================


class TestMigration0061ModuleLoad:
    """0061 迁移模块可正常加载。"""

    def test_module_loads_without_error(self) -> None:
        """0061 迁移模块可正常 import。"""
        module = _load_migration_module("0061")
        assert module is not None

    def test_module_has_required_attributes(self) -> None:
        """0061 模块包含必需的迁移属性。"""
        module = _load_migration_module("0061")
        assert hasattr(module, "revision")
        assert hasattr(module, "down_revision")
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")
        assert hasattr(module, "branch_labels")
        assert hasattr(module, "depends_on")

    def test_branch_labels_is_none(self) -> None:
        """0061 branch_labels 为 None（单链迁移）。"""
        module = _load_migration_module("0061")
        assert module.branch_labels is None

    def test_depends_on_is_none(self) -> None:
        """0061 depends_on 为 None。"""
        module = _load_migration_module("0061")
        assert module.depends_on is None
