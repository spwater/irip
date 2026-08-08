"""单元测试：ToolExecutor 工具执行器。

覆盖：
- check_role_permission 基于 BUILTIN_ROLES 权限矩阵正确判定；
- check_role_permission 未知角色无权限；
- build_tool_schemas 仅暴露 ai_tool 类别工具（ingestion 类不暴露）；
- build_tool_schemas 产出 OpenAI tools 格式；
- _require_numeric_tools 未配置时抛 AppError；
- _build_numeric_principal 从 user 构造 NumericPrincipal；
- execute_tool 分派到对应 handler（search_standards / extract_data）；
- execute_tool 未知工具返回未实现提示。
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from packages.ai.tool_executor import ToolExecutor
from packages.ai.tools import ToolRegistry
from packages.common.errors import AppError


def _make_user(roles: list[str]) -> MagicMock:
    """构造带 user_id / roles 属性的 user mock。"""

    class FakeUser:
        def __init__(self, roles: list[str]) -> None:
            self.user_id = uuid4()
            self.roles = roles

    return FakeUser(roles)


class TestCheckRolePermission:
    """ToolExecutor.check_role_permission 权限矩阵测试。"""

    def test_lab_member_has_fact_read(self) -> None:
        """lab_member 拥有 fact:read 权限。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_member"])
        assert executor.check_role_permission(user, "fact:read") is True

    def test_lab_viewer_lacks_fact_write(self) -> None:
        """lab_viewer 无 fact:write 权限。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_viewer"])
        assert executor.check_role_permission(user, "fact:write") is False

    def test_platform_administrator_has_all(self) -> None:
        """platform_administrator 拥有任意权限。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["platform_administrator"])
        assert executor.check_role_permission(user, "user:manage") is True
        assert executor.check_role_permission(user, "model:publish") is True

    def test_unknown_role_no_permission(self) -> None:
        """未知角色无任何权限。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["nonexistent_role"])
        assert executor.check_role_permission(user, "fact:read") is False

    def test_multiple_roles_union(self) -> None:
        """用户拥有多角色时取并集。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_viewer", "lab_member"])
        # lab_viewer 无 fact:write，但 lab_member 有
        assert executor.check_role_permission(user, "fact:write") is True


class TestBuildToolSchemas:
    """ToolExecutor.build_tool_schemas 测试。"""

    def test_schemas_are_openai_format(self) -> None:
        """schema 为 OpenAI tools 格式（type=function + function.name）。"""
        executor = ToolExecutor(ToolRegistry())
        schemas = executor.build_tool_schemas()
        assert len(schemas) > 0
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]

    def test_schemas_exclude_ingestion_tools(self) -> None:
        """ingestion 类工具不暴露给 AI 对话。"""
        executor = ToolExecutor(ToolRegistry())
        schemas = executor.build_tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        assert "xrd_converter" not in names
        assert "raman_converter" not in names
        assert "tga_converter" not in names

    def test_schemas_include_ai_tools(self) -> None:
        """ai_tool 类别工具出现在 schema 中。"""
        executor = ToolExecutor(ToolRegistry())
        schemas = executor.build_tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        assert "search_facts" in names
        assert "search_standards" in names
        assert "evaluate_expression" in names


class TestNumericTools:
    """ToolExecutor 数值工具相关测试。"""

    def test_require_numeric_tools_raises_when_none(self) -> None:
        """numeric_tools 未配置时 _require_numeric_tools 抛 AppError。"""
        executor = ToolExecutor(ToolRegistry())
        with pytest.raises(AppError, match="numeric tools not configured"):
            executor._require_numeric_tools()

    def test_require_numeric_tools_returns_when_configured(self) -> None:
        """numeric_tools 已配置时正常返回。"""
        fake_numeric = MagicMock()
        executor = ToolExecutor(ToolRegistry(), numeric_tools=fake_numeric)
        assert executor._require_numeric_tools() is fake_numeric

    def test_build_numeric_principal_without_user_id_raises(self) -> None:
        """user 无 user_id 时 _build_numeric_principal 抛 AppError。"""
        executor = ToolExecutor(ToolRegistry())
        user = MagicMock()
        user.user_id = None
        user.roles = ["lab_member"]
        with pytest.raises(AppError, match="user_id is required"):
            executor._build_numeric_principal(user, uuid4())

    def test_build_numeric_principal_constructs_from_user(self) -> None:
        """_build_numeric_principal 从 user 正确构造 NumericPrincipal。"""
        executor = ToolExecutor(ToolRegistry())
        org_id = uuid4()
        user = _make_user(["lab_member"])
        principal = executor._build_numeric_principal(user, org_id)
        assert principal.user_id == user.user_id
        assert principal.department_id == org_id
        assert "lab_member" in principal.roles


class TestExecuteToolDispatch:
    """ToolExecutor.execute_tool 分派逻辑测试。"""

    async def test_extract_data_returns_metadata(self) -> None:
        """extract_data 工具返回元数据（不依赖外部服务）。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_member"])
        result = await executor.execute_tool(
            "extract_data",
            {"path": "/data/file.csv", "prompt": "提取温度列", "schema": []},
            user,
            uuid4(),
        )
        assert "summary" in result
        assert "data" in result
        assert result["data"]["path"] == "/data/file.csv"

    async def test_unknown_tool_returns_not_implemented(self) -> None:
        """未知工具名返回未实现提示。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_member"])
        result = await executor.execute_tool("nonexistent_tool", {}, user, uuid4())
        assert "未实现" in result["summary"]
        assert "error" in result["data"]

    async def test_search_parameters_without_service_returns_unavailable(self) -> None:
        """parameter_service 未配置时 search_parameters 返回不可用提示。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_member"])
        result = await executor.execute_tool(
            "search_parameters", {"variable_code": "TEMP"}, user, uuid4()
        )
        assert "不可用" in result["summary"]

    async def test_compare_experiments_requires_two_ids(self) -> None:
        """compare_experiments 传入不足 2 个 ID 时返回错误提示。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_member"])
        result = await executor.execute_tool(
            "compare_experiments", {"fact_ids": ["one_id"]}, user, uuid4()
        )
        assert "至少 2" in result["summary"]
        assert "error" in result["data"]
