"""单元测试：AI 工具管理模块（DB 声明层 + 热更新 + 启停控制）。

覆盖架构设计任务 1-13 的核心逻辑：
- ToolRegistry.reload_from_db：从 DB 全量重建 _tools 和 _enabled（任务 4）；
- validate() 对禁用工具抛 unknown_tool（D-3，任务 4）；
- enabled_names() / list_enabled_tools() 仅返回启用工具（D-3，任务 4/5）；
- list_tools() 仍返回全部工具含禁用（供管理 API，任务 4）；
- AIService._build_tool_schemas 仅含启用工具（任务 5）；
- ToolRepository 乐观锁冲突逻辑（D-2，任务 2）—— 需 DB 环境；
- seed_tools_if_empty 幂等性（任务 3）—— 需 DB 环境。

不需要 DB 环境的测试用 mock session；需要 DB 的测试用 pytest.mark.integrationdb 标注。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text as sa_text

from packages.ai.tool_repository import AIToolRow, ToolRepository
from packages.ai.tools import ALL_TOOLS, ToolRegistry
from packages.common.errors import AppError

# ============================================================
# 辅助函数：构建测试用 AIToolRow
# ============================================================


def _make_row(
    name: str = "search_standards",
    display_name: str = "搜索标准变量",
    description: str = "按编码、名称或别名搜索已发布标准变量。",
    required_permission: str = "standard:read",
    enabled: bool = True,
    lock_version: int = 0,
) -> AIToolRow:
    """构建测试用 AIToolRow 领域对象。"""
    return AIToolRow(
        id=uuid4(),
        name=name,
        display_name=display_name,
        description=description,
        required_permission=required_permission,
        parameters_schema={"type": "object", "properties": {}},
        enabled=enabled,
        lock_version=lock_version,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=None,
    )


def _make_mock_session(rows: list[AIToolRow]) -> Any:
    """构建 mock AsyncSession，使 ToolRepository.list_all 返回指定 rows。

    Args:
        rows: 预设的 AIToolRow 列表。

    Returns:
        MagicMock：模拟的 AsyncSession。
    """
    session = MagicMock()

    # ToolRepository.list_all 调用 session.execute(select(...).order_by(...))
    # 然后 result.scalars().all() 返回实体列表。
    # 但因为 reload_from_db 调用的是 ToolRepository.list_all(session)，
    # 我们直接 patch ToolRepository.list_all 即可。
    return session


# ============================================================
# 1. ToolRegistry.reload_from_db（任务 4）
# ============================================================


class TestReloadFromDb:
    """reload_from_db 从 DB 全量重建 _tools 与 _enabled。"""

    async def test_reload_rebuilds_tools_and_enabled(self) -> None:
        """reload_from_db 后 _tools 和 _enabled 从 DB 重建。"""
        registry = ToolRegistry()
        # 初始状态：15 个工具全部启用（11 AI + 4 插件）
        assert len(registry.list_tools()) == 15
        assert len(registry.enabled_names()) == 15

        rows = [
            _make_row(name="tool_a", enabled=True),
            _make_row(name="tool_b", enabled=False),
            _make_row(name="tool_c", enabled=True),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        # _tools 包含全部 3 个（含禁用）
        assert len(registry.list_tools()) == 3
        # _enabled 仅含 2 个（tool_b 被禁用）
        assert len(registry.enabled_names()) == 2
        assert "tool_a" in registry.enabled_names()
        assert "tool_c" in registry.enabled_names()
        assert "tool_b" not in registry.enabled_names()

    async def test_reload_replaces_previous_state(self) -> None:
        """reload_from_db 是全量替换，旧工具声明被清除。"""
        registry = ToolRegistry()
        assert len(registry.list_tools()) == 15

        rows = [_make_row(name="only_tool", enabled=True)]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        # 旧 12 个工具被替换为 1 个
        assert len(registry.list_tools()) == 1
        assert registry.list_tools()[0].name == "only_tool"

    async def test_reload_preserves_disabled_in_tools(self) -> None:
        """禁用工具在 _tools 中保留 ToolSpec（供管理 API），但 _enabled 不含。"""
        registry = ToolRegistry()
        rows = [
            _make_row(name="enabled_tool", enabled=True),
            _make_row(name="disabled_tool", enabled=False),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        # list_tools() 返回全部（含禁用）
        all_tools = registry.list_tools()
        tool_names = {t.name for t in all_tools}
        assert "enabled_tool" in tool_names
        assert "disabled_tool" in tool_names

        # get() 仍可返回禁用工具的 ToolSpec
        spec = registry.get("disabled_tool")
        assert spec.name == "disabled_tool"

    async def test_reload_maps_fields_correctly(self) -> None:
        """reload_from_db 正确映射 AIToolRow 字段到 ToolSpec。"""
        registry = ToolRegistry()
        row = _make_row(
            name="custom_tool",
            display_name="自定义工具",
            description="测试用",
            required_permission="test:read",
            enabled=True,
        )
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=[row]):
            await registry.reload_from_db(MagicMock())

        spec = registry.get("custom_tool")
        assert spec.name == "custom_tool"
        assert spec.display_name == "自定义工具"
        assert spec.description == "测试用"
        assert spec.required_permission == "test:read"
        assert isinstance(spec.parameters_schema, dict)


# ============================================================
# 2. validate() 对禁用工具抛 unknown_tool（D-3，任务 4）
# ============================================================


class TestValidateDisabledTool:
    """validate() 对禁用工具抛 unknown_tool（与未知工具同处理）。"""

    async def test_validate_rejects_disabled_tool(self) -> None:
        """禁用工具 validate() 抛 unknown_tool。"""
        registry = ToolRegistry()
        rows = [_make_row(name="my_tool", enabled=False)]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        with pytest.raises(AppError, match="已被禁用") as exc_info:
            registry.validate("my_tool")
        assert exc_info.value.code == "unknown_tool"

    async def test_validate_accepts_enabled_tool(self) -> None:
        """启用工具 validate() 正常返回 ToolSpec。"""
        registry = ToolRegistry()
        rows = [_make_row(name="my_tool", enabled=True)]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        spec = registry.validate("my_tool")
        assert spec.name == "my_tool"

    async def test_validate_rejects_truly_unknown_tool(self) -> None:
        """未知工具（不在 _tools 中）validate() 抛 unknown_tool。"""
        registry = ToolRegistry()
        rows = [_make_row(name="known_tool", enabled=True)]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        with pytest.raises(AppError, match="未知工具") as exc_info:
            registry.validate("nonexistent_tool")
        assert exc_info.value.code == "unknown_tool"

    def test_validate_accepts_all_tools_before_reload(self) -> None:
        """reload_from_db 前全部工具默认启用，validate() 通过。"""
        registry = ToolRegistry()
        spec = registry.validate("search_standards")
        assert spec.name == "search_standards"


# ============================================================
# 3. enabled_names() / list_enabled_tools()（D-3，任务 4/5）
# ============================================================


class TestEnabledFiltering:
    """enabled_names() / list_enabled_tools() 仅返回启用工具。"""

    async def test_enabled_names_excludes_disabled(self) -> None:
        """enabled_names() 不含禁用工具。"""
        registry = ToolRegistry()
        rows = [
            _make_row(name="a", enabled=True),
            _make_row(name="b", enabled=False),
            _make_row(name="c", enabled=True),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        names = registry.enabled_names()
        assert "a" in names
        assert "c" in names
        assert "b" not in names
        assert len(names) == 2

    async def test_names_equals_enabled_names(self) -> None:
        """names() 与 enabled_names() 行为一致（均仅返回启用工具）。"""
        registry = ToolRegistry()
        rows = [
            _make_row(name="a", enabled=True),
            _make_row(name="b", enabled=False),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        assert set(registry.names()) == set(registry.enabled_names())

    async def test_list_enabled_tools_excludes_disabled(self) -> None:
        """list_enabled_tools() 不含禁用工具的 ToolSpec。"""
        registry = ToolRegistry()
        rows = [
            _make_row(name="a", enabled=True),
            _make_row(name="b", enabled=False),
            _make_row(name="c", enabled=True),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        enabled = registry.list_enabled_tools()
        enabled_names = {s.name for s in enabled}
        assert "a" in enabled_names
        assert "c" in enabled_names
        assert "b" not in enabled_names
        assert len(enabled) == 2

    async def test_list_tools_includes_disabled(self) -> None:
        """list_tools() 返回全部工具（含禁用），供管理 API 使用。"""
        registry = ToolRegistry()
        rows = [
            _make_row(name="a", enabled=True),
            _make_row(name="b", enabled=False),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        all_tools = registry.list_tools()
        assert len(all_tools) == 2
        assert {t.name for t in all_tools} == {"a", "b"}


# ============================================================
# 4. AIService._build_tool_schemas 仅含启用工具（任务 5）
# ============================================================


class TestBuildToolSchemas:
    """_build_tool_schemas 仅含启用工具的 schema。"""

    async def test_build_schemas_excludes_disabled(self) -> None:
        """_build_tool_schemas 不为禁用工具生成 schema。"""
        from packages.ai.offline_provider import OfflineProvider
        from packages.ai.service import AIService

        registry = ToolRegistry()
        rows = [
            _make_row(
                name="enabled_tool",
                display_name="启用工具",
                description="已启用",
                required_permission="test:read",
                enabled=True,
            ),
            _make_row(
                name="disabled_tool",
                display_name="禁用工具",
                description="已禁用",
                required_permission="test:read",
                enabled=False,
            ),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        service = AIService(
            provider=OfflineProvider(),
            tool_registry=registry,
        )
        schemas = service._build_tool_schemas()
        schema_names = {s["function"]["name"] for s in schemas}
        assert "enabled_tool" in schema_names
        assert "disabled_tool" not in schema_names
        assert len(schemas) == 1

    async def test_build_schemas_format(self) -> None:
        """_build_tool_schemas 输出 OpenAI tools JSON schema 格式。"""
        from packages.ai.offline_provider import OfflineProvider
        from packages.ai.service import AIService

        registry = ToolRegistry()
        rows = [
            _make_row(
                name="my_tool",
                display_name="我的工具",
                description="测试工具",
                required_permission="test:read",
                enabled=True,
            ),
        ]
        with patch.object(ToolRepository, "list_all", new_callable=AsyncMock, return_value=rows):
            await registry.reload_from_db(MagicMock())

        service = AIService(
            provider=OfflineProvider(),
            tool_registry=registry,
        )
        schemas = service._build_tool_schemas()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "my_tool"
        assert schema["function"]["description"] == "测试工具"
        assert "parameters" in schema["function"]


# ============================================================
# 5. 种子数据验证（任务 3）
# ============================================================


class TestSeedDataIntegrity:
    """验证 ALL_TOOLS 种子源完整性。"""

    def test_all_tools_count_is_15(self) -> None:
        """ALL_TOOLS 包含 15 个工具（11 AI + 4 插件）。"""
        assert len(ALL_TOOLS) == 15

    def test_all_tools_have_unique_names(self) -> None:
        """ALL_TOOLS 中工具名唯一。"""
        names = [t.name for t in ALL_TOOLS]
        assert len(names) == len(set(names))

    def test_all_tools_have_required_fields(self) -> None:
        """ALL_TOOLS 中每个工具含必填字段。"""
        for spec in ALL_TOOLS:
            assert spec.name
            assert spec.display_name
            assert spec.description
            assert isinstance(spec.parameters_schema, dict)


# ============================================================
# 6. ToolRepository 乐观锁（D-2，任务 2）—— 需 DB 环境
# ============================================================


class TestOptimisticLock:
    """ToolRepository 乐观锁冲突场景。

    标注 @pytest.mark.integrationdb：需要真实 PostgreSQL 数据库环境运行。
    """

    @pytest.mark.integration
    async def test_update_with_correct_lock_version_succeeds(
        self,
        async_session_factory: Any,
    ) -> None:
        """update 传入正确的 lock_version 时成功，lock_version 自增。"""
        from uuid import uuid4

        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            row = await ToolRepository.create(
                session,
                {
                    "name": f"test-olv-{uuid4().hex[:8]}",
                    "display_name": "Test",
                    "description": "test tool",
                    "required_permission": "standard:read",
                    "parameters_schema": {},
                },
                updated_by=uuid4(),
            )
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                updated = await ToolRepository.update(
                    session,
                    row.name,
                    {
                        "display_name": "Updated",
                        "description": "updated desc",
                        "required_permission": "standard:read",
                        "parameters_schema": {},
                    },
                    lock_version=row.lock_version,
                    updated_by=uuid4(),
                )
                assert updated.lock_version == row.lock_version + 1
                assert updated.display_name == "Updated"
                await session.commit()
        finally:
            async with session_scope(async_session_factory) as session:
                existing = await ToolRepository.get_by_name(session, row.name)
                if existing:
                    await session.execute(
                        sa_text("DELETE FROM ai_tool WHERE name = :n"),
                        {"n": row.name},
                    )
                    await session.commit()

    @pytest.mark.integration
    async def test_update_with_stale_lock_version_raises_conflict(
        self,
        async_session_factory: Any,
    ) -> None:
        """update 传入过期的 lock_version 时抛 conflict（409）。"""
        from uuid import uuid4

        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            row = await ToolRepository.create(
                session,
                {
                    "name": f"test-olv-stale-{uuid4().hex[:8]}",
                    "display_name": "Test",
                    "description": "test tool",
                    "required_permission": "standard:read",
                    "parameters_schema": {},
                },
                updated_by=uuid4(),
            )
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                await ToolRepository.update(
                    session,
                    row.name,
                    {
                        "display_name": "V1",
                        "description": "v1",
                        "required_permission": "standard:read",
                        "parameters_schema": {},
                    },
                    lock_version=row.lock_version,
                    updated_by=uuid4(),
                )
                await session.commit()
            async with session_scope(async_session_factory) as session:
                with pytest.raises(AppError) as exc_info:
                    await ToolRepository.update(
                        session,
                        row.name,
                        {
                            "display_name": "V2",
                            "description": "v2",
                            "required_permission": "standard:read",
                            "parameters_schema": {},
                        },
                        lock_version=row.lock_version,
                        updated_by=uuid4(),
                    )
                assert exc_info.value.code == "conflict"
        finally:
            async with session_scope(async_session_factory) as session:
                await session.execute(
                    sa_text("DELETE FROM ai_tool WHERE name = :n"),
                    {"n": row.name},
                )
                await session.commit()

    @pytest.mark.integration
    async def test_set_enabled_with_stale_lock_version_raises_conflict(
        self,
        async_session_factory: Any,
    ) -> None:
        """set_enabled 传入过期的 lock_version 时抛 conflict（409）。"""
        from uuid import uuid4

        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            row = await ToolRepository.create(
                session,
                {
                    "name": f"test-olv-set-{uuid4().hex[:8]}",
                    "display_name": "Test",
                    "description": "test tool",
                    "required_permission": "standard:read",
                    "parameters_schema": {},
                },
                updated_by=uuid4(),
            )
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                await ToolRepository.set_enabled(
                    session,
                    row.name,
                    enabled=False,
                    lock_version=row.lock_version,
                    updated_by=uuid4(),
                )
                await session.commit()
            async with session_scope(async_session_factory) as session:
                with pytest.raises(AppError) as exc_info:
                    await ToolRepository.set_enabled(
                        session,
                        row.name,
                        enabled=True,
                        lock_version=row.lock_version,
                        updated_by=uuid4(),
                    )
                assert exc_info.value.code == "conflict"
        finally:
            async with session_scope(async_session_factory) as session:
                await session.execute(
                    sa_text("DELETE FROM ai_tool WHERE name = :n"),
                    {"n": row.name},
                )
                await session.commit()

    @pytest.mark.integration
    async def test_update_nonexistent_tool_raises_not_found(
        self,
        async_session_factory: Any,
    ) -> None:
        """update 不存在的工具抛 not_found（404）。"""
        from uuid import uuid4

        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            with pytest.raises(AppError) as exc_info:
                await ToolRepository.update(
                    session,
                    f"nonexistent-{uuid4().hex[:8]}",
                    {
                        "display_name": "X",
                        "description": "x",
                        "required_permission": "standard:read",
                        "parameters_schema": {},
                    },
                    lock_version=0,
                    updated_by=uuid4(),
                )
            assert exc_info.value.code == "not_found"

    @pytest.mark.integration
    async def test_set_enabled_nonexistent_tool_raises_not_found(
        self,
        async_session_factory: Any,
    ) -> None:
        """set_enabled 不存在的工具抛 not_found（404）。"""
        from uuid import uuid4

        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            with pytest.raises(AppError) as exc_info:
                await ToolRepository.set_enabled(
                    session,
                    f"nonexistent-{uuid4().hex[:8]}",
                    enabled=False,
                    lock_version=0,
                    updated_by=uuid4(),
                )
            assert exc_info.value.code == "not_found"

    @pytest.mark.integration
    async def test_create_duplicate_name_raises_conflict(
        self,
        async_session_factory: Any,
    ) -> None:
        """create 重复 name 抛 conflict（409）。"""
        from uuid import uuid4

        from packages.common.database import session_scope

        name = f"test-dup-{uuid4().hex[:8]}"
        async with session_scope(async_session_factory) as session:
            await ToolRepository.create(
                session,
                {
                    "name": name,
                    "display_name": "First",
                    "description": "first",
                    "required_permission": "standard:read",
                    "parameters_schema": {},
                },
                updated_by=uuid4(),
            )
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                with pytest.raises(AppError) as exc_info:
                    await ToolRepository.create(
                        session,
                        {
                            "name": name,
                            "display_name": "Second",
                            "description": "second",
                            "required_permission": "standard:read",
                            "parameters_schema": {},
                        },
                        updated_by=uuid4(),
                    )
                assert exc_info.value.code == "conflict"
        finally:
            async with session_scope(async_session_factory) as session:
                await session.execute(
                    sa_text("DELETE FROM ai_tool WHERE name = :n"),
                    {"n": name},
                )
                await session.commit()


# ============================================================
# 7. seed_tools_if_empty 幂等性（任务 3）—— 需 DB 环境
# ============================================================


class TestSeedToolsIfEmpty:
    """seed_tools_if_empty 幂等性验证。

    标注 @pytest.mark.integrationdb：需要真实 PostgreSQL 数据库环境运行。
    """

    @pytest.mark.integration
    async def test_seed_writes_14_tools_on_empty_table(
        self,
        async_session_factory: Any,
    ) -> None:
        """表空时写入 14 条种子数据（12 AI + 2 插件）。"""
        from packages.ai.tool_seeding import seed_tools_if_empty
        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            await session.execute(sa_text("DELETE FROM ai_tool"))
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                count = await seed_tools_if_empty(session)
                assert count == len(ALL_TOOLS)
                all_rows = await ToolRepository.list_all(session)
                assert len(all_rows) == len(ALL_TOOLS)
                for row in all_rows:
                    assert row.enabled is True
                    assert row.lock_version == 0
                await session.commit()
        finally:
            async with session_scope(async_session_factory) as session:
                await session.execute(sa_text("DELETE FROM ai_tool"))
                await session.commit()

    @pytest.mark.integration
    async def test_seed_skips_when_table_not_empty(
        self,
        async_session_factory: Any,
    ) -> None:
        """表非空时不写入，返回 0。"""
        from uuid import uuid4

        from packages.ai.tool_seeding import seed_tools_if_empty
        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            await session.execute(sa_text("DELETE FROM ai_tool"))
            await session.commit()
        async with session_scope(async_session_factory) as session:
            await ToolRepository.create(
                session,
                {
                    "name": f"pre-existing-{uuid4().hex[:8]}",
                    "display_name": "Pre",
                    "description": "pre-existing",
                    "required_permission": "standard:read",
                    "parameters_schema": {},
                },
                updated_by=uuid4(),
            )
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                count = await seed_tools_if_empty(session)
                assert count == 0
                await session.commit()
        finally:
            async with session_scope(async_session_factory) as session:
                await session.execute(sa_text("DELETE FROM ai_tool"))
                await session.commit()

    @pytest.mark.integration
    async def test_seed_tools_enabled_and_lock_version_zero(
        self,
        async_session_factory: Any,
    ) -> None:
        """种子数据 enabled=True, lock_version=0。"""
        from packages.ai.tool_seeding import seed_tools_if_empty
        from packages.common.database import session_scope

        async with session_scope(async_session_factory) as session:
            await session.execute(sa_text("DELETE FROM ai_tool"))
            await session.commit()
        try:
            async with session_scope(async_session_factory) as session:
                await seed_tools_if_empty(session)
                all_rows = await ToolRepository.list_all(session)
                for row in all_rows:
                    assert row.enabled is True
                    assert row.lock_version == 0
                await session.commit()
        finally:
            async with session_scope(async_session_factory) as session:
                await session.execute(sa_text("DELETE FROM ai_tool"))
                await session.commit()
