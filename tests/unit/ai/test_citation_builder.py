"""单元测试：citation_builder 导航 citation 辅助函数。

覆盖：
- ``_TOOL_CITATION_CONFIG`` 映射结构正确性；
- ``_build_nav_citation`` 在各种输入下的构建结果（含 object_id / label / version / href）；
- ``_extract_object_id`` 对 search_facts / search_parameters / run_published_model 及
  未知工具的提取逻辑（含回退路径）；
- ``_extract_label`` 摘要截断与回退到 display_name 的行为；
- ``_extract_version`` 整数 / 字符串版本及缺失场景。
"""

from __future__ import annotations

import pytest

from packages.ai.citation_builder import (
    _TOOL_CITATION_CONFIG,
    _build_nav_citation,
    _extract_label,
    _extract_object_id,
    _extract_version,
)
from packages.ai.citations import Citation

# ── _TOOL_CITATION_CONFIG 结构测试 ────────────────────────────


class TestToolCitationConfig:
    """``_TOOL_CITATION_CONFIG`` 常量结构校验。"""

    def test_config_is_dict(self) -> None:
        """配置是一个 dict。"""
        assert isinstance(_TOOL_CITATION_CONFIG, dict)

    def test_config_contains_expected_tools(self) -> None:
        """配置包含三个已知工具。"""
        expected = {"search_facts", "search_parameters", "run_published_model"}
        assert set(_TOOL_CITATION_CONFIG.keys()) == expected

    @pytest.mark.parametrize("tool_name", list(_TOOL_CITATION_CONFIG.keys()))
    def test_each_entry_has_required_keys(self, tool_name: str) -> None:
        """每个工具配置包含 object_type 与 href_prefix。"""
        entry = _TOOL_CITATION_CONFIG[tool_name]
        assert "object_type" in entry
        assert "href_prefix" in entry
        assert isinstance(entry["object_type"], str)
        assert isinstance(entry["href_prefix"], str)

    def test_search_facts_mapping(self) -> None:
        """search_facts 映射到 fact_revision 与 /facts/。"""
        assert _TOOL_CITATION_CONFIG["search_facts"] == {
            "object_type": "fact_revision",
            "href_prefix": "/facts/",
        }

    def test_search_parameters_mapping(self) -> None:
        """search_parameters 映射到 parameter_version 与 /parameters/。"""
        assert _TOOL_CITATION_CONFIG["search_parameters"] == {
            "object_type": "parameter_version",
            "href_prefix": "/parameters/",
        }

    def test_run_published_model_mapping(self) -> None:
        """run_published_model 映射到 model_version 与 /models/。"""
        assert _TOOL_CITATION_CONFIG["run_published_model"] == {
            "object_type": "model_version",
            "href_prefix": "/models/",
        }


# ── _build_nav_citation 测试 ─────────────────────────────────


class TestBuildNavCitation:
    """``_build_nav_citation`` 构建逻辑测试。"""

    def test_unknown_tool_returns_none(self) -> None:
        """未在配置中的工具返回 None。"""
        result = _build_nav_citation(
            "unknown_tool", {}, {"data": {"items": [{"id": "x"}]}}, "Unknown"
        )
        assert result is None

    def test_search_facts_builds_citation(self) -> None:
        """search_facts 有结果时构建 Citation。"""
        tool_result = {"data": {"items": [{"id": "fact-123", "version": 3}]}, "summary": "ok"}
        citation = _build_nav_citation("search_facts", {}, tool_result, "Search Facts")
        assert isinstance(citation, Citation)
        assert citation.object_type == "fact_revision"
        assert citation.object_id == "fact-123"
        assert citation.href == "/facts/fact-123"
        assert citation.version == "v3"

    def test_search_parameters_builds_citation(self) -> None:
        """search_parameters 有结果时构建 Citation。"""
        tool_result = {
            "data": {"items": [{"id": "param-456"}]},
            "summary": "found param",
        }
        citation = _build_nav_citation("search_parameters", {}, tool_result, "Search Parameters")
        assert isinstance(citation, Citation)
        assert citation.object_type == "parameter_version"
        assert citation.object_id == "param-456"
        assert citation.href == "/parameters/param-456"

    def test_run_published_model_builds_citation(self) -> None:
        """run_published_model 从参数 model_id 构建 Citation。"""
        tool_args = {"model_id": "model-789"}
        citation = _build_nav_citation("run_published_model", tool_args, {}, "Run Published Model")
        assert isinstance(citation, Citation)
        assert citation.object_type == "model_version"
        assert citation.object_id == "model-789"
        assert citation.href == "/models/model-789"

    def test_returns_none_when_object_id_empty(self) -> None:
        """无法提取 object_id 时返回 None。"""
        # search_facts 无 items 且参数中无 fact_ids / parameter_id
        citation = _build_nav_citation("search_facts", {}, {"data": {"items": []}}, "Search Facts")
        assert citation is None

    def test_label_uses_summary_when_present(self) -> None:
        """结果含 summary 时 label 取摘要。"""
        tool_result = {
            "data": {"items": [{"id": "abc"}]},
            "summary": "这是摘要内容",
        }
        citation = _build_nav_citation("search_facts", {}, tool_result, "Fallback Name")
        assert citation is not None
        assert citation.label == "这是摘要内容"

    def test_label_falls_back_to_display_name(self) -> None:
        """无 summary 时 label 回退到 display_name。"""
        tool_result = {"data": {"items": [{"id": "abc"}]}}
        citation = _build_nav_citation("search_facts", {}, tool_result, "Display Name")
        assert citation is not None
        assert citation.label == "Display Name"

    def test_label_truncates_long_summary(self) -> None:
        """摘要超过 60 字符时截断到 60。"""
        long_summary = "x" * 100
        tool_result = {
            "data": {"items": [{"id": "abc"}]},
            "summary": long_summary,
        }
        citation = _build_nav_citation("search_facts", {}, tool_result, "Name")
        assert citation is not None
        assert len(citation.label) == 60


# ── _extract_object_id 测试 ───────────────────────────────────


class TestExtractObjectId:
    """``_extract_object_id`` 提取逻辑测试。"""

    def test_search_facts_from_items_id(self) -> None:
        """search_facts 从 items[0].id 提取。"""
        result = {"data": {"items": [{"id": "fact-1"}]}}
        assert _extract_object_id("search_facts", {}, result) == "fact-1"

    def test_search_facts_from_items_fact_id(self) -> None:
        """items[0] 无 id 但有 fact_id 时提取 fact_id。"""
        result = {"data": {"items": [{"fact_id": "fact-2"}]}}
        assert _extract_object_id("search_facts", {}, result) == "fact-2"

    def test_search_facts_fallback_to_fact_ids_arg(self) -> None:
        """无 items 时回退到参数 fact_ids。"""
        result = {"data": {"items": []}}
        args = {"fact_ids": ["fallback-fact"]}
        assert _extract_object_id("search_facts", args, result) == "fallback-fact"

    def test_search_parameters_fallback_to_parameter_id_arg(self) -> None:
        """search_parameters 无 items 时回退到参数 parameter_id。"""
        result = {"data": {"items": []}}
        args = {"parameter_id": "param-fallback"}
        assert _extract_object_id("search_parameters", args, result) == "param-fallback"

    def test_search_facts_empty_when_no_source(self) -> None:
        """无任何来源时返回空字符串。"""
        result = {"data": {"items": []}}
        assert _extract_object_id("search_facts", {}, result) == ""

    def test_search_facts_with_top_level_list(self) -> None:
        """data 直接是 list 时从中提取。"""
        result = {"data": [{"id": "list-id"}]}
        assert _extract_object_id("search_facts", {}, result) == "list-id"

    def test_run_published_model_from_model_id_arg(self) -> None:
        """run_published_model 从参数 model_id 提取。"""
        args = {"model_id": "model-abc"}
        assert _extract_object_id("run_published_model", args, {}) == "model-abc"

    def test_run_published_model_missing_model_id(self) -> None:
        """run_published_model 缺少 model_id 时返回空字符串。"""
        assert _extract_object_id("run_published_model", {}, {}) == ""

    def test_unknown_tool_returns_empty(self) -> None:
        """未知工具返回空字符串。"""
        assert _extract_object_id("unknown_tool", {}, {"data": {"items": [{"id": "x"}]}}) == ""

    def test_search_facts_non_dict_items_first_element(self) -> None:
        """items[0] 不是 dict 时回退到参数。"""
        result = {"data": {"items": ["not-a-dict"]}}
        args = {"fact_ids": ["fb"]}
        assert _extract_object_id("search_facts", args, result) == "fb"


# ── _extract_label 测试 ──────────────────────────────────────


class TestExtractLabel:
    """``_extract_label`` 提取逻辑测试。"""

    def test_returns_summary_when_present(self) -> None:
        """有 summary 时返回 summary。"""
        assert _extract_label("search_facts", {"summary": "hello"}, "Name") == "hello"

    def test_truncates_summary_over_60_chars(self) -> None:
        """summary 超过 60 字符时截断。"""
        long = "a" * 80
        label = _extract_label("search_facts", {"summary": long}, "Name")
        assert label == "a" * 60

    def test_summary_exactly_60_not_truncated(self) -> None:
        """summary 正好 60 字符时不截断。"""
        s = "b" * 60
        label = _extract_label("search_facts", {"summary": s}, "Name")
        assert label == s

    def test_falls_back_to_display_name_when_no_summary(self) -> None:
        """无 summary 时回退到 display_name。"""
        assert _extract_label("search_facts", {}, "Fallback") == "Fallback"

    def test_empty_string_summary_falls_back(self) -> None:
        """summary 为空字符串时回退到 display_name。"""
        # str("") == "" 是 falsy
        assert _extract_label("search_facts", {"summary": ""}, "Name") == "Name"


# ── _extract_version 测试 ────────────────────────────────────


class TestExtractVersion:
    """``_extract_version`` 提取逻辑测试。"""

    def test_int_version_prefixed_with_v(self) -> None:
        """整数 version 加 v 前缀。"""
        result = {"data": {"items": [{"version": 5}]}}
        assert _extract_version(result) == "v5"

    def test_string_version_as_is(self) -> None:
        """字符串 version 原样返回。"""
        result = {"data": {"items": [{"version": "rev2"}]}}
        assert _extract_version(result) == "rev2"

    def test_latest_version_number_field(self) -> None:
        """使用 latest_version_number 字段。"""
        result = {"data": {"items": [{"latest_version_number": 7}]}}
        assert _extract_version(result) == "v7"

    def test_no_items_returns_empty(self) -> None:
        """无 items 时返回空字符串。"""
        result = {"data": {"items": []}}
        assert _extract_version(result) == ""

    def test_no_data_key_uses_result_root(self) -> None:
        """无 data 键时使用 result 根。"""
        result = {"items": [{"version": 2}]}
        assert _extract_version(result) == "v2"

    def test_items_first_not_dict_returns_empty(self) -> None:
        """items[0] 非 dict 时返回空。"""
        result = {"data": {"items": ["str"]}}
        assert _extract_version(result) == ""

    def test_version_none_returns_empty(self) -> None:
        """version 为 None 时返回空字符串。"""
        result = {"data": {"items": [{"version": None}]}}
        assert _extract_version(result) == ""

    def test_empty_result_returns_empty(self) -> None:
        """空结果返回空字符串。"""
        assert _extract_version({}) == ""

    def test_top_level_list_not_supported(self) -> None:
        """data 为 list（非 dict）时返回空——只处理 dict 形式。"""
        result = {"data": [{"version": 1}]}
        assert _extract_version(result) == ""
