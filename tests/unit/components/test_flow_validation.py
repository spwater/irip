"""流程校验服务单元测试（IRIP V2-T03）。

测试覆盖：
- DAG 无环检测通过
- DAG 有环检测失败
- 节点 ID 唯一性检查
- 端口类型匹配（mock registry）
- 参数 schema 校验
- 未知节点引用检测

这些测试为纯内存计算（DAG 校验）或使用 mock registry（端口/参数校验），
无需数据库。
"""

from unittest.mock import AsyncMock, MagicMock

import jsonschema
import pytest

from packages.components.flows import FlowEdge, FlowNode
from packages.components.flow_validation import (
    FlowValidationService,
    ValidationResult,
)
from packages.components.manifest import ComponentManifest
from packages.components.sdk import PortSpec


# ---- DAG 无环检测 ----


class TestValidateDagAcyclic:
    """DAG 无环检测测试。"""

    def test_linear_dag_passes(self) -> None:
        """线性 DAG（A → B → C）校验通过。"""
        nodes = (
            FlowNode(
                node_id="a",
                component_name="comp_a",
                component_version="1.0.0",
            ),
            FlowNode(
                node_id="b",
                component_name="comp_b",
                component_version="1.0.0",
            ),
            FlowNode(
                node_id="c",
                component_name="comp_c",
                component_version="1.0.0",
            ),
        )
        edges = (
            FlowEdge("a", "out", "b", "in"),
            FlowEdge("b", "out", "c", "in"),
        )

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_diamond_dag_passes(self) -> None:
        """菱形 DAG（A → B, A → C, B → D, C → D）校验通过。"""
        nodes = (
            FlowNode("a", "comp_a", "1.0.0"),
            FlowNode("b", "comp_b", "1.0.0"),
            FlowNode("c", "comp_c", "1.0.0"),
            FlowNode("d", "comp_d", "1.0.0"),
        )
        edges = (
            FlowEdge("a", "out", "b", "in"),
            FlowEdge("a", "out", "c", "in"),
            FlowEdge("b", "out", "d", "in"),
            FlowEdge("c", "out", "d", "in"),
        )

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_single_node_no_edges_passes(self) -> None:
        """单节点无边的 DAG 校验通过（有孤立节点警告）。"""
        nodes = (FlowNode("a", "comp_a", "1.0.0"),)
        edges: tuple[FlowEdge, ...] = ()

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is True
        assert len(result.errors) == 0
        # 单节点应为孤立节点警告
        assert any("孤立" in w for w in result.warnings)

    def test_empty_dag_passes(self) -> None:
        """空 DAG 校验通过。"""
        result = FlowValidationService.validate_dag((), ())

        assert result.valid is True
        assert len(result.errors) == 0


# ---- DAG 有环检测 ----


class TestValidateDagCyclic:
    """DAG 有环检测测试。"""

    def test_simple_cycle_detected(self) -> None:
        """简单环（A → B → A）检测失败。"""
        nodes = (
            FlowNode("a", "comp_a", "1.0.0"),
            FlowNode("b", "comp_b", "1.0.0"),
        )
        edges = (
            FlowEdge("a", "out", "b", "in"),
            FlowEdge("b", "out", "a", "in"),
        )

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is False
        assert any("环" in e for e in result.errors)

    def test_three_node_cycle_detected(self) -> None:
        """三节点环（A → B → C → A）检测失败。"""
        nodes = (
            FlowNode("a", "comp_a", "1.0.0"),
            FlowNode("b", "comp_b", "1.0.0"),
            FlowNode("c", "comp_c", "1.0.0"),
        )
        edges = (
            FlowEdge("a", "out", "b", "in"),
            FlowEdge("b", "out", "c", "in"),
            FlowEdge("c", "out", "a", "in"),
        )

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is False
        assert any("环" in e for e in result.errors)

    def test_self_loop_detected(self) -> None:
        """自环边检测失败。"""
        nodes = (FlowNode("a", "comp_a", "1.0.0"),)
        edges = (FlowEdge("a", "out", "a", "in"),)

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is False
        assert any("自环" in e for e in result.errors)


# ---- 节点 ID 唯一性 ----


class TestNodeIdUniqueness:
    """节点 ID 唯一性检查测试。"""

    def test_duplicate_node_ids_detected(self) -> None:
        """重复节点 ID 检测失败。"""
        nodes = (
            FlowNode("a", "comp_a", "1.0.0"),
            FlowNode("a", "comp_b", "1.0.0"),
        )
        edges: tuple[FlowEdge, ...] = ()

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is False
        assert any("重复" in e for e in result.errors)

    def test_unique_node_ids_pass(self) -> None:
        """唯一节点 ID 校验通过。"""
        nodes = (
            FlowNode("a", "comp_a", "1.0.0"),
            FlowNode("b", "comp_b", "1.0.0"),
        )
        edges = (FlowEdge("a", "out", "b", "in"),)

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is True


# ---- 未知节点引用 ----


class TestUnknownNodeReferences:
    """未知节点引用检测测试。"""

    def test_edge_references_unknown_source(self) -> None:
        """边引用不存在的源节点检测失败。"""
        nodes = (FlowNode("a", "comp_a", "1.0.0"),)
        edges = (FlowEdge("x", "out", "a", "in"),)

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is False
        assert any("源节点不存在" in e for e in result.errors)

    def test_edge_references_unknown_target(self) -> None:
        """边引用不存在的目标节点检测失败。"""
        nodes = (FlowNode("a", "comp_a", "1.0.0"),)
        edges = (FlowEdge("a", "out", "x", "in"),)

        result = FlowValidationService.validate_dag(nodes, edges)

        assert result.valid is False
        assert any("目标节点不存在" in e for e in result.errors)


# ---- 端口类型匹配 ----


def _make_mock_registry(
    port_schemas_map: dict[str, dict],
) -> MagicMock:
    """构建 mock registry，按 (name, version) 返回带 port_schemas 的版本。"""

    async def _get(name: str, version: str) -> MagicMock:
        key = f"{name}@{version}"
        if key not in port_schemas_map:
            from packages.common.errors import AppError

            raise AppError(
                code="not_found",
                message=f"组件不存在: {key}",
                retryable=False,
                fields={"name": name, "version": version},
            )
        mock_version = MagicMock()
        mock_version.port_schemas = port_schemas_map[key]
        return mock_version

    registry = MagicMock()
    registry.get = _get
    return registry


class TestCheckPortTypes:
    """端口类型匹配测试。"""

    @pytest.mark.asyncio
    async def test_matching_port_types_pass(self) -> None:
        """端口类型匹配校验通过。"""
        port_schemas = {
            "source@1.0.0": {
                "inputs": [],
                "outputs": [
                    {"name": "out", "data_type": "dataset", "required": True}
                ],
            },
            "target@1.0.0": {
                "inputs": [
                    {"name": "in", "data_type": "dataset", "required": True}
                ],
                "outputs": [],
            },
        }
        registry = _make_mock_registry(port_schemas)

        nodes = (
            FlowNode("src", "source", "1.0.0"),
            FlowNode("tgt", "target", "1.0.0"),
        )
        edges = (FlowEdge("src", "out", "tgt", "in"),)

        result = await FlowValidationService.check_port_types(
            nodes, edges, registry
        )

        assert result.valid is True
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_mismatched_port_types_fail(self) -> None:
        """端口类型不匹配校验失败。"""
        port_schemas = {
            "source@1.0.0": {
                "inputs": [],
                "outputs": [
                    {"name": "out", "data_type": "dataset", "required": True}
                ],
            },
            "target@1.0.0": {
                "inputs": [
                    {"name": "in", "data_type": "report", "required": True}
                ],
                "outputs": [],
            },
        }
        registry = _make_mock_registry(port_schemas)

        nodes = (
            FlowNode("src", "source", "1.0.0"),
            FlowNode("tgt", "target", "1.0.0"),
        )
        edges = (FlowEdge("src", "out", "tgt", "in"),)

        result = await FlowValidationService.check_port_types(
            nodes, edges, registry
        )

        assert result.valid is False
        assert any("类型不兼容" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_any_type_compatible_with_anything(self) -> None:
        """源端口类型为 'any' 时与任何目标类型兼容。"""
        port_schemas = {
            "source@1.0.0": {
                "inputs": [],
                "outputs": [
                    {"name": "out", "data_type": "any", "required": True}
                ],
            },
            "target@1.0.0": {
                "inputs": [
                    {"name": "in", "data_type": "report", "required": True}
                ],
                "outputs": [],
            },
        }
        registry = _make_mock_registry(port_schemas)

        nodes = (
            FlowNode("src", "source", "1.0.0"),
            FlowNode("tgt", "target", "1.0.0"),
        )
        edges = (FlowEdge("src", "out", "tgt", "in"),)

        result = await FlowValidationService.check_port_types(
            nodes, edges, registry
        )

        assert result.valid is True

    @pytest.mark.asyncio
    async def test_nonexistent_port_detected(self) -> None:
        """引用不存在的端口检测失败。"""
        port_schemas = {
            "source@1.0.0": {
                "inputs": [],
                "outputs": [
                    {"name": "out", "data_type": "dataset", "required": True}
                ],
            },
            "target@1.0.0": {
                "inputs": [
                    {"name": "in", "data_type": "dataset", "required": True}
                ],
                "outputs": [],
            },
        }
        registry = _make_mock_registry(port_schemas)

        nodes = (
            FlowNode("src", "source", "1.0.0"),
            FlowNode("tgt", "target", "1.0.0"),
        )
        edges = (FlowEdge("src", "nonexistent", "tgt", "in"),)

        result = await FlowValidationService.check_port_types(
            nodes, edges, registry
        )

        assert result.valid is False
        assert any("源端口不存在" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_unknown_component_detected(self) -> None:
        """引用不存在的组件检测失败。"""
        port_schemas: dict[str, dict] = {}
        registry = _make_mock_registry(port_schemas)

        nodes = (FlowNode("src", "nonexistent", "1.0.0"),)
        edges: tuple[FlowEdge, ...] = ()

        result = await FlowValidationService.check_port_types(
            nodes, edges, registry
        )

        assert result.valid is False
        assert any("组件不存在" in e for e in result.errors)


# ---- 参数 schema 校验 ----


def _make_manifest(
    parameters: dict | None = None,
    inputs: tuple[PortSpec, ...] = (),
    outputs: tuple[PortSpec, ...] = (),
) -> ComponentManifest:
    """构建测试用 ComponentManifest。"""
    return ComponentManifest(
        name="test_component",
        version="1.0.0",
        kind="transform",
        runtime="python",
        inputs=inputs,
        outputs=outputs,
        parameters=parameters or {},
        dependencies=(),
        raw_yaml="",
        sha256="",
    )


class TestCheckParamSchema:
    """参数 schema 校验测试。"""

    def test_valid_params_pass(self) -> None:
        """参数符合 schema 校验通过。"""
        schema = {
            "type": "object",
            "properties": {
                "threshold": {"type": "number"},
                "name": {"type": "string"},
            },
            "required": ["threshold"],
        }
        manifest = _make_manifest(parameters=schema)
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={"threshold": 0.5, "name": "test"},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is True
        assert len(result.errors) == 0

    def test_missing_required_param_fails(self) -> None:
        """缺少必需参数校验失败。"""
        schema = {
            "type": "object",
            "properties": {
                "threshold": {"type": "number"},
            },
            "required": ["threshold"],
        }
        manifest = _make_manifest(parameters=schema)
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is False
        assert any("参数校验失败" in e for e in result.errors)

    def test_wrong_param_type_fails(self) -> None:
        """参数类型错误校验失败。"""
        schema = {
            "type": "object",
            "properties": {
                "threshold": {"type": "number"},
            },
            "required": ["threshold"],
        }
        manifest = _make_manifest(parameters=schema)
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={"threshold": "not_a_number"},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is False

    def test_no_schema_allows_any_params(self) -> None:
        """无 parameters schema 时允许任意参数。"""
        manifest = _make_manifest(parameters={})
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={"anything": "goes", "number": 42},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is True

    def test_missing_required_input_binding_fails(self) -> None:
        """必需输入端口未绑定校验失败。"""
        manifest = _make_manifest(
            inputs=(PortSpec(name="data", data_type="dataset", required=True),),
        )
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={},
            input_bindings={},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is False
        assert any("必需输入端口" in e for e in result.errors)

    def test_optional_input_binding_not_required(self) -> None:
        """可选输入端口不绑定时通过。"""
        manifest = _make_manifest(
            inputs=(PortSpec(name="data", data_type="dataset", required=False),),
        )
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={},
            input_bindings={},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is True

    def test_bound_input_port_passes(self) -> None:
        """已绑定的必需输入端口校验通过。"""
        manifest = _make_manifest(
            inputs=(PortSpec(name="data", data_type="dataset", required=True),),
        )
        node = FlowNode(
            node_id="n1",
            component_name="test_component",
            component_version="1.0.0",
            params={},
            input_bindings={"data": "source:output"},
        )

        result = FlowValidationService.check_param_schema(node, manifest)

        assert result.valid is True
