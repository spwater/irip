"""单元测试：ToolExecutor 工具执行器。

覆盖：
- check_role_permission 基于 BUILTIN_ROLES 权限矩阵正确判定；
- check_role_permission 未知角色无权限；
- build_tool_schemas 仅暴露 ai_tool 类别工具（ingestion 类不暴露）；
- build_tool_schemas 产出 OpenAI tools 格式；
- _require_numeric_tools 未配置时抛 AppError；
- _build_numeric_principal 从 user 构造 NumericPrincipal；
- execute_tool 分派到对应 handler（search_standards / extract_data）；
- execute_tool 未知工具返回未实现提示；
- search_facts handler 通过 fact_service 搜索 / 空结果 / 截断到 20 条；
- search_parameters handler 通过 parameter_service 搜索 / 服务异常；
- explain_provenance handler 通过 provenance_service / 未配置 / 异常；
- compare_experiments handler 通过 fact_service / 异常 / 未配置；
- run_published_model handler 通过 model_service / 异常 / 未配置 / inputs 强制转换；
- draft_report handler 无 factory / 默认标题；
- extract_data handler 长路径截断 / 长 prompt 截断；
- evaluate_expression / describe_series 分派到 numeric_tools。
"""

from unittest.mock import AsyncMock, MagicMock
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


# ============================================================
# Handler tests with mocked services
# ============================================================


class TestSearchFactsHandler:
    """search_facts handler 测试。"""

    async def test_search_facts_with_service(self) -> None:
        """fact_service 可用时通过 service 搜索。"""
        fact_service = AsyncMock()
        fact_service.search = AsyncMock(
            return_value=[
                {
                    "id": "fact-1",
                    "subject_id": "sample-1",
                    "fact_type": "measurement",
                    "data_summary": "温度=25°C",
                },
                {
                    "id": "fact-2",
                    "subject_id": "sample-2",
                    "fact_type": "measurement",
                    "data_summary": "湿度=60%",
                },
            ]
        )
        executor = ToolExecutor(ToolRegistry(), fact_service=fact_service)
        org_id = uuid4()

        result = await executor.execute_tool(
            "search_facts",
            {"query": "温度", "fact_type": "measurement"},
            _make_user(["lab_member"]),
            org_id,
        )

        assert "搜索到 2 条事实" in result["summary"]
        assert result["data"]["count"] == 2
        assert result["data"]["results"][0]["id"] == "fact-1"
        fact_service.search.assert_awaited_once()
        call_kwargs = fact_service.search.call_args
        assert call_kwargs.kwargs["query"] == "温度"
        assert call_kwargs.kwargs["fact_type"] == "measurement"
        assert call_kwargs.kwargs["department_id"] == org_id

    async def test_search_facts_service_empty_results(self) -> None:
        """fact_service 返回空列表。"""
        fact_service = AsyncMock()
        fact_service.search = AsyncMock(return_value=[])
        executor = ToolExecutor(ToolRegistry(), fact_service=fact_service)

        result = await executor.execute_tool(
            "search_facts", {"query": "nonexistent"}, _make_user(["lab_member"]), uuid4()
        )

        assert "搜索到 0 条事实" in result["summary"]
        assert result["data"]["count"] == 0

    async def test_search_facts_truncates_to_20(self) -> None:
        """fact_service 返回超过 20 条时截断。"""
        fact_service = AsyncMock()
        fact_service.search = AsyncMock(
            return_value=[
                {"id": f"f-{i}", "subject_id": f"s-{i}", "fact_type": "t", "data_summary": ""}
                for i in range(25)
            ]
        )
        executor = ToolExecutor(ToolRegistry(), fact_service=fact_service)

        result = await executor.execute_tool(
            "search_facts", {"query": "all"}, _make_user(["lab_member"]), uuid4()
        )

        assert result["data"]["count"] == 20


class TestSearchParametersHandler:
    """search_parameters handler 测试。"""

    async def test_search_parameters_with_service(self) -> None:
        """parameter_service 可用时通过 service 搜索。"""
        param_service = AsyncMock()
        param_service.search_by_variable = AsyncMock(
            return_value=[
                {"id": "p-1", "variable_code": "TEMP", "value": "25", "status": "approved"},
            ]
        )
        executor = ToolExecutor(ToolRegistry(), parameter_service=param_service)

        result = await executor.execute_tool(
            "search_parameters", {"variable_code": "TEMP"}, _make_user(["lab_member"]), uuid4()
        )

        assert "搜索到 1 个参数" in result["summary"]
        assert result["data"]["results"][0]["variable_code"] == "TEMP"

    async def test_search_parameters_service_error(self) -> None:
        """parameter_service 抛异常时返回错误信息。"""
        param_service = AsyncMock()
        param_service.search_by_variable = AsyncMock(side_effect=RuntimeError("DB down"))
        executor = ToolExecutor(ToolRegistry(), parameter_service=param_service)

        result = await executor.execute_tool(
            "search_parameters", {"variable_code": "X"}, _make_user(["lab_member"]), uuid4()
        )

        assert "参数搜索失败" in result["summary"]
        assert "DB down" in result["data"]["error"]


class TestExplainProvenanceHandler:
    """explain_provenance handler 测试。"""

    async def test_explain_provenance_with_service(self) -> None:
        """provenance_service 可用时返回溯源链路。"""
        prov_service = AsyncMock()
        prov_service.explain = AsyncMock(
            return_value={"steps": [{"step": 1}, {"step": 2}, {"step": 3}]}
        )
        executor = ToolExecutor(ToolRegistry(), provenance_service=prov_service)

        result = await executor.execute_tool(
            "explain_provenance", {"parameter_id": "p-1"}, _make_user(["lab_member"]), uuid4()
        )

        assert "3 个步骤" in result["summary"]
        assert len(result["data"]["steps"]) == 3

    async def test_explain_provenance_without_service(self) -> None:
        """provenance_service 未配置时返回不可用。"""
        executor = ToolExecutor(ToolRegistry())

        result = await executor.execute_tool(
            "explain_provenance", {"parameter_id": "p-1"}, _make_user(["lab_member"]), uuid4()
        )

        assert "溯源服务不可用" in result["summary"]

    async def test_explain_provenance_service_error(self) -> None:
        """provenance_service 抛异常时返回错误信息。"""
        prov_service = AsyncMock()
        prov_service.explain = AsyncMock(side_effect=ValueError("not found"))
        executor = ToolExecutor(ToolRegistry(), provenance_service=prov_service)

        result = await executor.execute_tool(
            "explain_provenance", {"parameter_id": "x"}, _make_user(["lab_member"]), uuid4()
        )

        assert "溯源查询失败" in result["summary"]


class TestCompareExperimentsHandler:
    """compare_experiments handler 测试。"""

    async def test_compare_experiments_with_service(self) -> None:
        """fact_service 可用时对比实验。"""
        fid1 = str(uuid4())
        fid2 = str(uuid4())
        fact_service = AsyncMock()
        fact_service.get = AsyncMock(
            return_value={
                "id": "f-1",
                "subject_id": "s-1",
                "fact_type": "measurement",
                "data_summary": "data",
            }
        )
        executor = ToolExecutor(ToolRegistry(), fact_service=fact_service)

        result = await executor.execute_tool(
            "compare_experiments",
            {"fact_ids": [fid1, fid2]},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "对比了" in result["summary"]
        assert fact_service.get.await_count == 2

    async def test_compare_experiments_service_error(self) -> None:
        """fact_service.get 抛异常时返回错误。"""
        fact_service = AsyncMock()
        fact_service.get = AsyncMock(side_effect=RuntimeError("DB error"))
        executor = ToolExecutor(ToolRegistry(), fact_service=fact_service)

        result = await executor.execute_tool(
            "compare_experiments",
            {"fact_ids": [str(uuid4()), str(uuid4())]},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "实验对比失败" in result["summary"]

    async def test_compare_experiments_without_service(self) -> None:
        """fact_service 未配置时返回不可用。"""
        executor = ToolExecutor(ToolRegistry())

        result = await executor.execute_tool(
            "compare_experiments",
            {"fact_ids": [str(uuid4()), str(uuid4())]},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "事实服务不可用" in result["summary"]


class TestRunModelHandler:
    """run_published_model handler 测试。"""

    async def test_run_model_with_service(self) -> None:
        """model_service 可用时运行模型预测。"""
        model_service = AsyncMock()
        model_service.predict = AsyncMock(
            return_value=MagicMock(
                version="v1.0",
                model_id=uuid4(),
                model_version_id=uuid4(),
                predictions={"output": 42},
                fact_id=None,
            )
        )
        executor = ToolExecutor(ToolRegistry(), model_service=model_service)

        result = await executor.execute_tool(
            "run_published_model",
            {"model_id": str(uuid4()), "inputs": {"x": 1}},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "模型预测完成" in result["summary"]
        assert result["data"]["version"] == "v1.0"
        assert result["data"]["predictions"] == {"output": 42}

    async def test_run_model_service_error(self) -> None:
        """model_service 抛异常时返回错误。"""
        model_service = AsyncMock()
        model_service.predict = AsyncMock(side_effect=RuntimeError("model not found"))
        executor = ToolExecutor(ToolRegistry(), model_service=model_service)

        result = await executor.execute_tool(
            "run_published_model",
            {"model_id": str(uuid4()), "inputs": {}},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "模型预测失败" in result["summary"]

    async def test_run_model_without_service(self) -> None:
        """model_service 未配置时返回不可用。"""
        executor = ToolExecutor(ToolRegistry())

        result = await executor.execute_tool(
            "run_published_model",
            {"model_id": str(uuid4()), "inputs": {}},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "模型服务不可用" in result["summary"]

    async def test_run_model_invalid_inputs_coerced_to_dict(self) -> None:
        """inputs 非 dict 时被强制转为空 dict。"""
        model_service = AsyncMock()
        model_service.predict = AsyncMock(
            return_value=MagicMock(
                version="v",
                model_id=uuid4(),
                model_version_id=uuid4(),
                predictions={},
                fact_id=None,
            )
        )
        executor = ToolExecutor(ToolRegistry(), model_service=model_service)

        await executor.execute_tool(
            "run_published_model",
            {"model_id": str(uuid4()), "inputs": "not-a-dict"},
            _make_user(["lab_member"]),
            uuid4(),
        )

        # predict should receive {} as inputs
        call_kwargs = model_service.predict.call_args
        assert call_kwargs.kwargs["inputs"] == {}


class TestDraftReportHandler:
    """draft_report handler 测试。"""

    async def test_draft_report_without_factory(self) -> None:
        """无 factory 时返回空事实摘要的草稿。"""
        executor = ToolExecutor(ToolRegistry())

        result = await executor.execute_tool(
            "draft_report",
            {"title": "测试报告", "fact_ids": []},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert "报告草稿已生成" in result["summary"]
        assert result["data"]["title"] == "测试报告"
        assert result["data"]["referenced_facts"] == []

    async def test_draft_report_default_title(self) -> None:
        """无 title 时使用默认标题。"""
        executor = ToolExecutor(ToolRegistry())

        result = await executor.execute_tool(
            "draft_report",
            {"fact_ids": []},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert result["data"]["title"] == "未命名报告"


class TestExtractDataHandler:
    """extract_data handler 测试。"""

    async def test_extract_data_truncates_long_path(self) -> None:
        """path 超过 100 字符时在 summary 中截断。"""
        executor = ToolExecutor(ToolRegistry())
        long_path = "/" + "a" * 200

        result = await executor.execute_tool(
            "extract_data",
            {"path": long_path, "prompt": "extract"},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert len(result["summary"]) < len(f"数据提取请求已记录（路径: {long_path}）")

    async def test_extract_data_truncates_data_path(self) -> None:
        """path 在 data 中被截断到 200 字符。"""
        executor = ToolExecutor(ToolRegistry())
        long_path = "/" + "b" * 250

        result = await executor.execute_tool(
            "extract_data",
            {"path": long_path, "prompt": "x"},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert len(result["data"]["path"]) <= 200

    async def test_extract_data_truncates_prompt(self) -> None:
        """prompt 在 data 中被截断到 500 字符。"""
        executor = ToolExecutor(ToolRegistry())
        long_prompt = "p" * 600

        result = await executor.execute_tool(
            "extract_data",
            {"path": "/data", "prompt": long_prompt},
            _make_user(["lab_member"]),
            uuid4(),
        )

        assert len(result["data"]["prompt"]) <= 500


class TestNumericToolDispatch:
    """evaluate_expression / describe_series 分派测试。"""

    async def test_evaluate_expression_dispatches_to_numeric(self) -> None:
        """evaluate_expression 分派到 numeric_tools.evaluate_expression。"""
        numeric_mock = MagicMock()
        numeric_mock.evaluate_expression = AsyncMock(
            return_value=MagicMock(
                summary="计算结果",
                llm_data={"value": 8},
                audit_data={"expr": "3+5"},
                citation_params={},
            )
        )
        executor = ToolExecutor(ToolRegistry(), numeric_tools=numeric_mock)
        user = _make_user(["lab_member"])

        result = await executor.execute_tool("evaluate_expression", {"expr": "3+5"}, user, uuid4())

        assert result["summary"] == "计算结果"
        assert result["data"] == {"value": 8}
        assert "audit" in result

    async def test_describe_series_dispatches_to_numeric(self) -> None:
        """describe_series 分派到 numeric_tools.describe_series。"""
        numeric_mock = MagicMock()
        numeric_mock.describe_series = AsyncMock(
            return_value=MagicMock(
                summary="统计描述",
                llm_data={"mean": 25.5},
                audit_data={},
                citation_params={},
            )
        )
        executor = ToolExecutor(ToolRegistry(), numeric_tools=numeric_mock)
        user = _make_user(["lab_member"])

        result = await executor.execute_tool("describe_series", {"series_id": "s-1"}, user, uuid4())

        assert result["summary"] == "统计描述"
        assert result["data"] == {"mean": 25.5}

    async def test_evaluate_expression_without_numeric_raises(self) -> None:
        """numeric_tools 未配置时 evaluate_expression 抛 AppError。"""
        executor = ToolExecutor(ToolRegistry())
        user = _make_user(["lab_member"])

        with pytest.raises(AppError, match="numeric tools not configured"):
            await executor.execute_tool("evaluate_expression", {"expr": "1+1"}, user, uuid4())
