"""迁移文件结构验证单元测试（T03）。

验证 T03 新建和修改的迁移文件：
- 迁移链连续性（revision / down_revision）；
- 不可变表列表（_IMMUTABLE_TABLES）修正：移除 flow_node_execution 和
  evidence_set，添加 evidence_set_version；
- 0049 迁移操作：DROP 错误触发器 + CREATE 正确触发器。

通过 importlib 动态加载迁移文件模块进行检查，无需真实数据库。
"""

import importlib
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).parents[2] / "migrations" / "versions"


def _load_migration_module(revision: str):
    """按 revision ID 动态加载迁移模块。"""
    # 迁移文件名格式：0047_fix_db_roles_order.py
    files = list(MIGRATIONS_DIR.glob(f"{revision}_*.py"))
    assert files, f"找不到 revision={revision} 的迁移文件"
    assert len(files) == 1, f"revision={revision} 匹配到多个文件: {files}"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", files[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 迁移链连续性
# ---------------------------------------------------------------------------


class TestMigrationChain:
    """0047-0050 迁移链 revision/down_revision 连续性。"""

    @pytest.mark.parametrize(
        "revision,expected_down",
        [
            ("0047", "0046"),
            ("0048", "0047"),
            ("0049", "0048"),
            ("0050", "0049"),
        ],
    )
    def test_revision_chain(self, revision: str, expected_down: str) -> None:
        """每个迁移的 down_revision 指向前一个迁移的 revision。"""
        module = _load_migration_module(revision)
        assert module.revision == revision
        assert module.down_revision == expected_down

    def test_0047_revises_0046(self) -> None:
        """0047 的 down_revision 为 0046。"""
        module = _load_migration_module("0047")
        assert module.revision == "0047"
        assert module.down_revision == "0046"

    def test_0048_revises_0047(self) -> None:
        """0048 的 down_revision 为 0047。"""
        module = _load_migration_module("0048")
        assert module.revision == "0048"
        assert module.down_revision == "0047"

    def test_0049_revises_0048(self) -> None:
        """0049 的 down_revision 为 0048。"""
        module = _load_migration_module("0049")
        assert module.revision == "0049"
        assert module.down_revision == "0048"

    def test_0050_revises_0049(self) -> None:
        """0050 的 down_revision 为 0049。"""
        module = _load_migration_module("0050")
        assert module.revision == "0050"
        assert module.down_revision == "0049"

    def test_all_have_upgrade_and_downgrade(self) -> None:
        """0047-0050 均定义了 upgrade 和 downgrade 函数。"""
        for revision in ("0047", "0048", "0049", "0050"):
            module = _load_migration_module(revision)
            assert callable(module.upgrade), f"{revision} 缺少 upgrade()"
            assert callable(module.downgrade), f"{revision} 缺少 downgrade()"


# ---------------------------------------------------------------------------
# 不可变表列表修正（0033 + 0047）
# ---------------------------------------------------------------------------


class TestImmutableTablesFix:
    """_IMMUTABLE_TABLES 修正：移除 flow_node_execution / evidence_set，
    添加 evidence_set_version。

    0033 和 0047 均定义了 _IMMUTABLE_TABLES，需确保两者一致且正确。
    """

    def test_0033_no_flow_node_execution(self) -> None:
        """0033 修改后 _IMMUTABLE_TABLES 不包含 flow_node_execution。"""
        module = _load_migration_module("0033")
        assert "flow_node_execution" not in module._IMMUTABLE_TABLES

    def test_0033_no_evidence_set(self) -> None:
        """0033 修改后 _IMMUTABLE_TABLES 不包含 evidence_set。"""
        module = _load_migration_module("0033")
        assert "evidence_set" not in module._IMMUTABLE_TABLES

    def test_0033_has_evidence_set_version(self) -> None:
        """0033 修改后 _IMMUTABLE_TABLES 包含 evidence_set_version。"""
        module = _load_migration_module("0033")
        assert "evidence_set_version" in module._IMMUTABLE_TABLES

    def test_0033_immutable_tables_content(self) -> None:
        """0033 _IMMUTABLE_TABLES 完整内容校验（含 fact_revision，后被 0055 删除）。"""
        module = _load_migration_module("0033")
        expected = {
            "fact_revision",
            "component_version",
            "flow_definition_version",
            "audit_event",
            "evidence_set_version",
        }
        assert set(module._IMMUTABLE_TABLES) == expected

    def test_0047_no_flow_node_execution(self) -> None:
        """0047 _IMMUTABLE_TABLES 不包含 flow_node_execution。"""
        module = _load_migration_module("0047")
        assert "flow_node_execution" not in module._IMMUTABLE_TABLES

    def test_0047_no_evidence_set(self) -> None:
        """0047 _IMMUTABLE_TABLES 不包含 evidence_set。"""
        module = _load_migration_module("0047")
        assert "evidence_set" not in module._IMMUTABLE_TABLES

    def test_0047_has_evidence_set_version(self) -> None:
        """0047 _IMMUTABLE_TABLES 包含 evidence_set_version。"""
        module = _load_migration_module("0047")
        assert "evidence_set_version" in module._IMMUTABLE_TABLES

    def test_0047_immutable_tables_content(self) -> None:
        """0047 _IMMUTABLE_TABLES 完整内容校验（含 fact_revision，后被 0055 删除）。"""
        module = _load_migration_module("0047")
        expected = {
            "fact_revision",
            "component_version",
            "flow_definition_version",
            "audit_event",
            "evidence_set_version",
        }
        assert set(module._IMMUTABLE_TABLES) == expected

    def test_0033_and_0047_immutable_tables_consistent(self) -> None:
        """0033 与 0047 的 _IMMUTABLE_TABLES 保持一致。"""
        m33 = _load_migration_module("0033")
        m47 = _load_migration_module("0047")
        assert set(m33._IMMUTABLE_TABLES) == set(m47._IMMUTABLE_TABLES)

    def test_0034_no_flow_node_execution_in_immutable(self) -> None:
        """0034 修改后 _IMMUTABLE_TABLES 不包含 flow_node_execution。"""
        module = _load_migration_module("0034")
        assert "flow_node_execution" not in module._IMMUTABLE_TABLES

    def test_0034_no_evidence_set_in_immutable(self) -> None:
        """0034 修改后 _IMMUTABLE_TABLES 不包含 evidence_set。"""
        module = _load_migration_module("0034")
        assert "evidence_set" not in module._IMMUTABLE_TABLES

    def test_0034_has_evidence_set_version_in_immutable(self) -> None:
        """0034 修改后 _IMMUTABLE_TABLES 包含 evidence_set_version。"""
        module = _load_migration_module("0034")
        assert "evidence_set_version" in module._IMMUTABLE_TABLES


# ---------------------------------------------------------------------------
# 0049 触发器操作验证
# ---------------------------------------------------------------------------


class TestMigration0049Triggers:
    """0049 迁移：DROP 错误触发器 + CREATE 正确触发器。

    0049 通过 op.execute() 执行原始 SQL，验证 SQL 文本内容：
    - DROP flow_node_execution 上的 prevent_modify 触发器；
    - DROP evidence_set 上的 prevent_modify 触发器；
    - CREATE evidence_set_version 上的 prevent_modify 触发器。
    """

    def _read_file_text(self) -> str:
        """读取 0049 迁移文件全文。"""
        files = list(MIGRATIONS_DIR.glob("0049_*.py"))
        assert len(files) == 1
        return files[0].read_text(encoding="utf-8")

    def test_drops_flow_node_execution_trigger(self) -> None:
        """0049 包含 DROP flow_node_execution 触发器的语句。"""
        content = self._read_file_text()
        assert "prevent_modify_flow_node_execution" in content
        assert "DROP TRIGGER" in content.upper()

    def test_drops_evidence_set_trigger(self) -> None:
        """0049 包含 DROP evidence_set 触发器的语句。"""
        content = self._read_file_text()
        assert "prevent_modify_evidence_set" in content
        assert "DROP TRIGGER" in content.upper()

    def test_creates_evidence_set_version_trigger(self) -> None:
        """0049 包含 CREATE evidence_set_version 触发器的语句。"""
        content = self._read_file_text()
        assert "prevent_modify_evidence_set_version" in content
        assert "CREATE TRIGGER" in content.upper()

    def test_drops_use_if_exists(self) -> None:
        """0049 的 DROP TRIGGER 使用 IF EXISTS（幂等）。"""
        content = self._read_file_text()
        assert (
            "DROP TRIGGER IF EXISTS" in content.upper()
            or "drop trigger if exists" in content.lower()
        )

    def test_0049_module_loads(self) -> None:
        """0049 迁移模块可正常加载。"""
        module = _load_migration_module("0049")
        assert module.revision == "0049"
        assert callable(module.upgrade)
        assert callable(module.downgrade)


# ---------------------------------------------------------------------------
# 0048 FORCE RLS 验证
# ---------------------------------------------------------------------------


class TestMigration0048ForceRls:
    """0048 迁移：动态启用 FORCE RLS + tenant_isolation policy。"""

    def _read_file_text(self) -> str:
        files = list(MIGRATIONS_DIR.glob("0048_*.py"))
        assert len(files) == 1
        return files[0].read_text(encoding="utf-8")

    def test_enables_force_rls(self) -> None:
        """0048 包含 FORCE ROW LEVEL SECURITY 语句。"""
        content = self._read_file_text()
        assert "FORCE ROW LEVEL SECURITY" in content.upper()

    def test_creates_tenant_isolation_policy(self) -> None:
        """0048 包含 tenant_isolation policy 创建。"""
        content = self._read_file_text()
        assert "tenant_isolation" in content

    def test_uses_organization_id_column(self) -> None:
        """0048 引用 organization_id 标识需要 FORCE RLS 的表（历史迁移，不可修改）。"""
        content = self._read_file_text()
        assert "organization_id" in content

    def test_drops_existing_policy_before_create(self) -> None:
        """0048 创建 policy 前先 DROP IF EXISTS（幂等）。"""
        content = self._read_file_text()
        assert "DROP POLICY IF EXISTS" in content.upper()

    def test_0048_module_loads(self) -> None:
        """0048 迁移模块可正常加载。"""
        module = _load_migration_module("0048")
        assert module.revision == "0048"
        assert callable(module.upgrade)
        assert callable(module.downgrade)


# ---------------------------------------------------------------------------
# 0050 component active_version_id 验证
# ---------------------------------------------------------------------------


class TestMigration0050ActiveVersion:
    """0050 迁移：为 component 表添加 active_version_id 列。"""

    def _read_file_text(self) -> str:
        files = list(MIGRATIONS_DIR.glob("0050_*.py"))
        assert len(files) == 1
        return files[0].read_text(encoding="utf-8")

    def test_adds_active_version_id_column(self) -> None:
        """0050 包含添加 active_version_id 列操作。"""
        content = self._read_file_text()
        assert "active_version_id" in content
        assert "ADD COLUMN" in content.upper()

    def test_creates_foreign_key(self) -> None:
        """0050 创建外键约束指向 component_version。"""
        content = self._read_file_text()
        assert "component_version" in content
        assert "create_foreign_key" in content or "FOREIGN KEY" in content.upper()

    def test_creates_index(self) -> None:
        """0050 为 active_version_id 创建索引。"""
        content = self._read_file_text()
        assert "create_index" in content or "CREATE INDEX" in content.upper()

    def test_0050_module_loads(self) -> None:
        """0050 迁移模块可正常加载。"""
        module = _load_migration_module("0050")
        assert module.revision == "0050"
        assert callable(module.upgrade)
        assert callable(module.downgrade)


# ---------------------------------------------------------------------------
# 0047 GRANT 顺序修复验证
# ---------------------------------------------------------------------------


class TestMigration0047GrantOrder:
    """0047 迁移：用 DO 块安全授权。"""

    def test_0047_uses_do_block_for_grant(self) -> None:
        """0047 的 GRANT 使用 DO 块包裹（检查表存在）。"""
        module = _load_migration_module("0047")
        assert hasattr(module, "_grant_safe")
        assert hasattr(module, "_revoke_safe")

    def test_0047_grant_safe_checks_table_exists(self) -> None:
        """_grant_safe 生成的 SQL 包含 information_schema.tables 检查。"""
        module = _load_migration_module("0047")
        sql = module._grant_safe("test_table", "SELECT", "irip_runtime")
        assert "information_schema.tables" in sql
        assert "DO $$" in sql
        assert "GRANT SELECT" in sql

    def test_0047_revoke_safe_checks_table_exists(self) -> None:
        """_revoke_safe 生成的 SQL 包含 information_schema.tables 检查。"""
        module = _load_migration_module("0047")
        sql = module._revoke_safe("test_table", "UPDATE, DELETE", "irip_runtime")
        assert "information_schema.tables" in sql
        assert "DO $$" in sql
        assert "REVOKE" in sql

    def test_0047_business_tables_not_empty(self) -> None:
        """0047 _BUSINESS_TABLES 非空。"""
        module = _load_migration_module("0047")
        assert len(module._BUSINESS_TABLES) > 0


# ---------------------------------------------------------------------------
# 0034 DO 块修复验证
# ---------------------------------------------------------------------------


class TestMigration0034DoBlock:
    """0034 修改后：GRANT/REVOKE 用 DO 块包裹。"""

    def _read_file_text(self) -> str:
        files = list(MIGRATIONS_DIR.glob("0034_*.py"))
        assert len(files) == 1
        return files[0].read_text(encoding="utf-8")

    def test_0034_uses_do_block_for_grant(self) -> None:
        """0034 修改后 GRANT 使用 DO 块包裹。"""
        content = self._read_file_text()
        assert "DO $$" in content
        assert "information_schema.tables" in content

    def test_0034_business_tables_not_empty(self) -> None:
        """0034 _BUSINESS_TABLES 非空。"""
        module = _load_migration_module("0034")
        assert len(module._BUSINESS_TABLES) > 0
