"""单元测试：AI 工具白名单策略。

覆盖：
- 工具白名单验证（8 个只读工具 + 4 个候选工具已注册）；
- 未知工具拒绝（防注入）；
- 工具参数 schema 记录（parameters_schema 非空）；
- 权限声明正确（required_permission 与权限矩阵一致）。
"""

import pytest

from packages.ai.tools import (
    AI_TOOL_NAMES,
    ALL_TOOL_NAMES,
    CANDIDATE_TOOLS,
    WHITELIST_TOOLS,
    ToolRegistry,
    ToolSpec,
)
from packages.common.errors import AppError


class TestToolWhitelist:
    """工具白名单验证。"""

    def test_whitelist_has_eight_tools(self) -> None:
        """白名单包含 8 个只读工具。"""
        assert len(WHITELIST_TOOLS) == 8

        """候选工具包含 3 个需审批工具。"""
        assert len(CANDIDATE_TOOLS) == 3

    def test_all_tools_is_union(self) -> None:
        """全部工具 = 白名单 + 候选 + 插件。"""
        assert len(ALL_TOOL_NAMES) == 15

    def test_whitelist_tool_names_match(self) -> None:
        """AI 工具名称集合正确。"""
        expected = {
            "search_standards",
            "search_facts",
            "search_parameters",
            "explain_provenance",
            "compare_experiments",
            "run_published_model",
            "draft_report",
            "extract_data",
            "suggest_mapping",
            "create_parameter_candidate",
            "create_model_publish_request",
        }
        assert AI_TOOL_NAMES == expected

        """候选工具名称集合正确。"""
        expected = {
            "suggest_mapping",
            "create_parameter_candidate",
            "create_model_publish_request",
        }
        assert frozenset(spec.name for spec in CANDIDATE_TOOLS) == expected

        """白名单与候选工具不重叠。"""
        whitelist_names = frozenset(spec.name for spec in WHITELIST_TOOLS)
        candidate_names = frozenset(spec.name for spec in CANDIDATE_TOOLS)
        assert whitelist_names.isdisjoint(candidate_names)

    def test_extract_data_tool_properties(self) -> None:
        """extract_data 工具属性正确（V2-T03 新增白名单工具）。"""
        registry = ToolRegistry()
        spec = registry.get("extract_data")
        assert spec.name == "extract_data"
        assert spec.required_permission == "ingestion:write"
        # 参数 schema 包含 path/prompt/schema 三个必填参数
        assert spec.parameters_schema["type"] == "object"
        props = spec.parameters_schema["properties"]
        assert "path" in props
        assert "prompt" in props
        assert "schema" in props
        required = spec.parameters_schema["required"]
        assert "path" in required
        assert "prompt" in required
        assert "schema" in required
        # 显示名含中文
        assert any("\u4e00" <= ch <= "\u9fff" for ch in spec.display_name)


class TestToolRegistryValidation:
    """ToolRegistry 验证逻辑。"""

    def test_default_registry_has_all_tools(self) -> None:
        """默认注册表包含全部 15 个工具。"""
        registry = ToolRegistry()
        assert len(registry.list_tools()) == 15

    def test_get_known_tool(self) -> None:
        """按名称获取已知工具。"""
        registry = ToolRegistry()
        spec = registry.get("search_standards")
        assert spec.name == "search_standards"
        assert spec.display_name == "搜索标准变量"

    def test_get_unknown_tool_raises(self) -> None:
        """获取未知工具抛出 AppError。"""
        registry = ToolRegistry()
        with pytest.raises(AppError, match="未知工具"):
            registry.get("malicious_tool")

    def test_validate_rejects_unknown_tool(self) -> None:
        """验证拒绝未知工具（防注入）。"""
        registry = ToolRegistry()
        with pytest.raises(AppError, match="未知工具"):
            registry.validate("inject_sql")

    def test_validate_accepts_known_tool(self) -> None:
        """验证接受已知工具。"""
        registry = ToolRegistry()
        spec = registry.validate("explain_provenance")
        assert spec.name == "explain_provenance"

    def test_duplicate_registration_raises(self) -> None:
        """重复注册同名工具抛出 AppError。"""
        registry = ToolRegistry()
        with pytest.raises(AppError, match="已注册"):
            registry.register(
                ToolSpec(
                    name="search_standards",
                    display_name="重复",
                    description="",
                    required_permission="standard:read",
                )
            )


class TestToolParametersRecord:
    """工具参数记录验证。"""

    def test_all_tools_have_parameters_schema(self) -> None:
        """所有工具有 parameters_schema（字典类型）。"""
        registry = ToolRegistry()
        for spec in registry.list_tools():
            assert isinstance(spec.parameters_schema, dict)
            # AI 工具应有 type 字段，插件工具可为空 schema
            if spec.category == "ai_tool":
                assert "type" in spec.parameters_schema

    def test_all_tools_have_required_permission(self) -> None:
        """AI 工具声明了所需权限，插件工具可为空。"""
        registry = ToolRegistry()
        for spec in registry.list_tools():
            if spec.category == "ai_tool":
                assert spec.required_permission != ""
                assert ":" in spec.required_permission

    def test_all_tools_have_display_name(self) -> None:
        """所有工具有中文显示名。"""
        registry = ToolRegistry()
        for spec in registry.list_tools():
            assert spec.display_name != ""
            # 显示名含中文字符
            assert any("\u4e00" <= ch <= "\u9fff" for ch in spec.display_name)

    def test_all_tools_have_description(self) -> None:
        """所有工具有描述。"""
        registry = ToolRegistry()
        for spec in registry.list_tools():
            assert spec.description != ""

    def test_tool_names_returned(self) -> None:
        """names() 返回全部工具名称元组。"""
        registry = ToolRegistry()
        names = registry.names()
        assert len(names) == 15
        assert "search_standards" in names
        assert "suggest_mapping" in names
        assert "extract_data" in names
