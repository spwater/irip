"""test_numeric_seeding_migration.py — 播种与迁移测试。

设计文档 §19.8 迁移测试。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from packages.ai.tools import ALL_TOOLS, WHITELIST_TOOLS

# Load the migration module (filename starts with digits, needs importlib)
_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "migrations"
    / "versions"
    / "0079_ai_numeric_tools.py"
)


def _load_migration_module():
    """Load the 0079 migration module via importlib (filename starts with digits)."""
    spec = importlib.util.spec_from_file_location("migration_0079", str(_MIGRATION_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# seed_missing_builtin_tools 幂等性
# =============================================================================


class TestSeedMissingBuiltinTools:
    """seed_missing_builtin_tools 幂等性。"""

    def test_seed_idempotent(self) -> None:
        """调用两次，第二次返回 0。"""
        from packages.ai.tool_seeding import seed_missing_builtin_tools

        # Mock session that tracks existing names
        existing_names: set[str] = set()

        async def mock_execute(stmt):
            # Check if it's a SELECT (checking existence) or INSERT
            stmt_str = str(stmt)
            if "SELECT" in stmt_str.upper():
                # Return mock result
                result = MagicMock()
                result.scalar_one_or_none = lambda: None if not existing_names else "exists"
                return result
            return MagicMock()

        session = AsyncMock()
        session.execute = mock_execute
        session.add = MagicMock()
        session.flush = AsyncMock()

        # First call
        count1 = asyncio.run(seed_missing_builtin_tools(session))
        assert count1 > 0  # Should insert all tools

        # Mark all as existing now
        for spec in ALL_TOOLS:
            existing_names.add(spec.name)

        # Second call
        count2 = asyncio.run(seed_missing_builtin_tools(session))
        assert count2 == 0  # Nothing to insert

    def test_seed_returns_count(self) -> None:
        """返回新插入行数。"""
        from packages.ai.tool_seeding import seed_missing_builtin_tools

        session = AsyncMock()
        call_count = [0]

        async def mock_execute(stmt):
            call_count[0] += 1
            result = MagicMock()
            # All tools already exist
            result.scalar_one_or_none = lambda: "exists"
            return result

        session.execute = mock_execute
        session.flush = AsyncMock()

        count = asyncio.run(seed_missing_builtin_tools(session))
        assert count == 0

    def test_seed_inserts_all_for_empty_db(self) -> None:
        """空数据库升级得到全部内置工具。"""
        from packages.ai.tool_seeding import seed_missing_builtin_tools

        session = AsyncMock()

        async def mock_execute(stmt):
            result = MagicMock()
            result.scalar_one_or_none = lambda: None  # nothing exists
            return result

        session.execute = mock_execute
        session.add = MagicMock()
        session.flush = AsyncMock()

        count = asyncio.run(seed_missing_builtin_tools(session))
        assert count == len(ALL_TOOLS)

    def test_seed_only_inserts_missing(self) -> None:
        """已有部分工具的数据库只补缺失项。"""
        from packages.ai.tool_seeding import seed_missing_builtin_tools

        # Simulate first 5 whitelist tools already existing
        existing_names = {spec.name for spec in WHITELIST_TOOLS[:5]}
        existing_set = set(existing_names)

        session = AsyncMock()

        async def mock_execute(stmt):
            result = MagicMock()
            result.scalar_one_or_none = lambda: (
                "exists"
                if existing_set
                and _check_stmt_is_select(stmt)
                and _stmt_tool_name(stmt) in existing_set
                else None
            )
            return result

        def _check_stmt_is_select(stmt):
            return "SELECT" in str(stmt).upper()

        def _stmt_tool_name(stmt):
            # Try to extract the tool name from bound parameters
            try:
                compiled = stmt.compile()
                params = compiled.params
                for key, val in params.items():
                    if key == "name" or (isinstance(val, str) and val in existing_set):
                        return val
            except Exception:
                pass
            return None

        session.execute = mock_execute
        session.add = MagicMock()
        session.flush = AsyncMock()

        count = asyncio.run(seed_missing_builtin_tools(session))
        # Should insert all tools minus existing ones
        assert count == len(ALL_TOOLS) - len(existing_set)

    def test_admin_edits_not_overwritten(self) -> None:
        """同名管理员编辑不被覆盖。"""
        from packages.ai.tool_seeding import seed_missing_builtin_tools

        # All tools exist
        session = AsyncMock()

        async def mock_execute(stmt):
            result = MagicMock()
            result.scalar_one_or_none = lambda: "exists"
            return result

        session.execute = mock_execute
        session.add = MagicMock()
        session.flush = AsyncMock()

        count = asyncio.run(seed_missing_builtin_tools(session))
        assert count == 0  # Nothing inserted, nothing overwritten


# =============================================================================
# 0079 迁移验证
# =============================================================================


class TestMigration0079:
    """0079 upgrade/downgrade 可重复验证。"""

    def test_revision_number(self) -> None:
        m = _load_migration_module()
        assert m.revision == "0079"
        assert m.down_revision == "0078"

    def test_get_schemas_returns_json_strings(self) -> None:
        m = _load_migration_module()
        eval_schema, desc_schema = m._get_schemas()
        eval_dict = json.loads(eval_schema)
        desc_dict = json.loads(desc_schema)

        from packages.ai.numeric.contracts import (
            DESCRIBE_SERIES_SCHEMA,
            EVALUATE_EXPRESSION_SCHEMA,
        )

        assert eval_dict == EVALUATE_EXPRESSION_SCHEMA
        assert desc_dict == DESCRIBE_SERIES_SCHEMA

    def test_upgrade_calls_op_execute_twice(self) -> None:
        """upgrade 执行两次 INSERT。"""
        m = _load_migration_module()

        with patch.object(m, "op") as mock_op:
            mock_op.execute = MagicMock()
            mock_op.text = MagicMock()
            mock_op.bindparam = MagicMock(side_effect=lambda name, value: (name, value))

            m.upgrade()

            assert mock_op.execute.call_count == 2

    def test_downgrade_deletes_by_name(self) -> None:
        """downgrade 按 name 删除两个工具。"""
        m = _load_migration_module()

        with patch.object(m, "op") as mock_op:
            mock_op.execute = MagicMock()

            m.downgrade()

            assert mock_op.execute.call_count == 1
            call_args = str(mock_op.execute.call_args)
            assert "evaluate_expression" in call_args
            assert "describe_series" in call_args

    def test_fixed_uuids(self) -> None:
        """固定 UUID 用于确定性插入。"""
        m = _load_migration_module()
        assert m._EVALUATE_EXPRESSION_ID == "018f0000-0000-7000-8000-000000000010"
        assert m._DESCRIBE_SERIES_ID == "018f0000-0000-7000-8000-000000000011"


# =============================================================================
# ToolRegistry reload 后启停即时生效
# =============================================================================


class TestToolRegistryWithNumeric:
    """ToolRegistry 包含数值工具。"""

    def test_registry_has_numeric_tools(self) -> None:
        from packages.ai.tools import ToolRegistry

        registry = ToolRegistry()
        assert registry.is_registered("evaluate_expression")
        assert registry.is_registered("describe_series")

    def test_registry_list_enabled_includes_numeric(self) -> None:
        from packages.ai.tools import ToolRegistry

        registry = ToolRegistry()
        enabled = registry.enabled_names()
        assert "evaluate_expression" in enabled
        assert "describe_series" in enabled

    def test_registry_validate_numeric(self) -> None:
        from packages.ai.tools import ToolRegistry

        registry = ToolRegistry()
        spec = registry.validate("evaluate_expression")
        assert spec.name == "evaluate_expression"
        spec = registry.validate("describe_series")
        assert spec.name == "describe_series"

    def test_registry_get_numeric_schema(self) -> None:
        from packages.ai.tools import ToolRegistry

        registry = ToolRegistry()
        spec = registry.get("evaluate_expression")
        assert spec.parameters_schema is not None
        assert spec.parameters_schema.get("type") == "object"

    def test_build_tool_schemas_includes_numeric(self) -> None:
        """build_tool_schemas 包含数值工具。"""
        from packages.ai.tool_executor import ToolExecutor
        from packages.ai.tools import ToolRegistry

        registry = ToolRegistry()
        executor = ToolExecutor(registry)
        schemas = executor.build_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "evaluate_expression" in names
        assert "describe_series" in names
