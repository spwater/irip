"""单元测试：AI 工具白名单策略。

覆盖：
- 工具白名单验证（8 个只读工具 + 4 个候选工具已注册）；
- 未知工具拒绝（防注入）；
- 候选工具标记为 candidate=True；
- 工具参数 schema 记录（parameters_schema 非空）；
- 权限声明正确（required_permission 与权限矩阵一致）。
"""

import pytest

from packages.ai.tools import (
    ALL_TOOL_NAMES,
    CANDIDATE_TOOL_NAMES,
    CANDIDATE_TOOLS,
    WHITELIST_TOOL_NAMES,
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

    def test_candidate_has_four_tools(self) -> None:
        """候选工具包含 4 个需审批工具。"""
        assert len(CANDIDATE_TOOLS) == 4

    def test_all_tools_is_union(self) -> None:
        """全部工具 = 白名单 + 候选 + 插件。"""
        assert len(ALL_TOOL_NAMES) == 14

    def test_whitelist_tool_names_match(self) -> None:
        """白名单工具名称集合正确。"""
        expected = {
            "search_standards",
            "search_facts",
            "search_parameters",
            "explain_provenance",
            "compare_experiments",
            "run_published_model",
            "draft_report",
            "extract_data",
        }
        assert WHITELIST_TOOL_NAMES == expected

    def test_candidate_tool_names_match(self) -> None:
        """候选工具名称集合正确。"""
        expected = {
            "suggest_mapping",
            "suggest_fact_revision",
            "create_parameter_candidate",
            "create_model_publish_request",
        }
        assert CANDIDATE_TOOL_NAMES == expected

    def test_whitelist_and_candidate_disjoint(self) -> None:
        """白名单与候选工具不重叠。"""
        assert WHITELIST_TOOL_NAMES.isdisjoint(CANDIDATE_TOOL_NAMES)

    def test_extract_data_tool_properties(self) -> None:
        """extract_data 工具属性正确（V2-T03 新增白名单工具）。"""
        registry = ToolRegistry()
        spec = registry.get("extract_data")
        assert spec.name == "extract_data"
        assert spec.candidate is False
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
        # 属于白名单工具（可直接执行）
        assert registry.is_whitelist("extract_data") is True
        assert registry.is_candidate("extract_data") is False


class TestToolRegistryValidation:
    """ToolRegistry 验证逻辑。"""

    def test_default_registry_has_all_tools(self) -> None:
        """默认注册表包含全部 12 个工具。"""
        registry = ToolRegistry()
        assert len(registry.list_tools()) == 14

    def test_get_known_tool(self) -> None:
        """按名称获取已知工具。"""
        registry = ToolRegistry()
        spec = registry.get("search_standards")
        assert spec.name == "search_standards"
        assert spec.display_name == "搜索标准变量"
        assert spec.candidate is False

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


class TestCandidateToolMarking:
    """候选工具标记验证。"""

    def test_candidate_tools_marked(self) -> None:
        """候选工具标记为 candidate=True。"""
        registry = ToolRegistry()
        for spec in CANDIDATE_TOOLS:
            assert registry.is_candidate(spec.name) is True
            assert registry.is_whitelist(spec.name) is False

    def test_whitelist_tools_not_candidate(self) -> None:
        """白名单工具标记为 candidate=False。"""
        registry = ToolRegistry()
        for spec in WHITELIST_TOOLS:
            assert registry.is_candidate(spec.name) is False
            assert registry.is_whitelist(spec.name) is True

    def test_list_candidate_tools(self) -> None:
        """list_candidate_tools 返回 4 个候选工具。"""
        registry = ToolRegistry()
        candidates = registry.list_candidate_tools()
        assert len(candidates) == 4
        assert all(s.candidate for s in candidates)

    def test_list_whitelist_tools(self) -> None:
        """list_whitelist_tools 返回 10 个非候选工具（8 只读 + 2 插件）。"""
        registry = ToolRegistry()
        whitelist = registry.list_whitelist_tools()
        assert len(whitelist) == 10
        assert all(not s.candidate for s in whitelist)


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
        assert len(names) == 14
        assert "search_standards" in names
        assert "suggest_mapping" in names
        assert "extract_data" in names
