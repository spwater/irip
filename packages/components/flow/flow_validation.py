"""IRIP 流程校验服务：DAG 校验 + 端口类型 + 参数 schema。

提供：
- ValidationResult: 校验结果值对象（valid + errors + warnings）；
- FlowValidationService: 流程校验服务，包含：
  - validate_dag: Kahn 算法拓扑排序 + 环检测 + 节点 ID 唯一性；
  - check_port_types: 查询组件 manifest，验证边端口 data_type 兼容；
  - check_param_schema: 验证节点参数符合组件 parameters JSON Schema；
  - check_node_references: 验证边引用的节点是否存在。

设计要点（IRIP V2-T03）：
- 校验分为结构校验（DAG 无环、节点 ID 唯一、边引用有效）
  与语义校验（端口类型匹配、参数 schema 校验）；
- 结构校验（validate_dag）不依赖 registry，纯内存计算；
- 语义校验（check_port_types）依赖 ComponentRegistryService
  查询组件 manifest 的端口规格；
- check_param_schema 接收 manifest 参数，由调用方负责获取 manifest；
- 校验结果累积所有错误与警告，不提前返回（fail-fast 不适用）。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import jsonschema

from packages.common.errors import AppError
from packages.components.flow.flows import FlowEdge, FlowNode
from packages.components.manifest import ComponentManifest
from packages.components.registry import ComponentRegistryService  # type: ignore[attr-defined]


@dataclass(frozen=True)
class ValidationResult:
    """校验结果（不可变值对象）。

    Attributes:
        valid: 校验是否通过（无错误时为 True）。
        errors: 错误消息元组（空元组表示无错误）。
        warnings: 警告消息元组（不影响 valid 判定）。
    """

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class FlowValidationService:
    """流程校验服务。

    提供流程定义的结构校验与语义校验。
    结构校验（validate_dag）不依赖外部服务，
    语义校验（check_port_types）依赖 ComponentRegistryService
    查询组件 manifest 的端口规格。
    """

    @staticmethod
    def validate_dag(
        nodes: Iterable[FlowNode],
        edges: Iterable[FlowEdge],
    ) -> ValidationResult:
        """DAG 校验：节点 ID 唯一性 + 边引用有效性 + Kahn 拓扑排序 + 环检测。

        纯内存计算，不依赖外部服务。

        Args:
            nodes: 节点集合。
            edges: 边集合。

        Returns:
            ValidationResult: 校验结果（含所有错误与警告）。
        """
        errors: list[str] = []
        warnings: list[str] = []

        node_list: list[FlowNode] = list(nodes)
        edge_list: list[FlowEdge] = list(edges)

        # ---- 1. 节点 ID 唯一性 ----
        seen_ids: dict[str, int] = {}
        for node in node_list:
            seen_ids[node.node_id] = seen_ids.get(node.node_id, 0) + 1
        for node_id, count in seen_ids.items():
            if count > 1:
                errors.append(f"节点 ID 重复: {node_id}（出现 {count} 次）")

        valid_node_ids: set[str] = set(seen_ids.keys())

        # ---- 2. 边引用的节点是否存在 ----
        for edge in edge_list:
            if edge.source_node not in valid_node_ids:
                errors.append(f"边引用的源节点不存在: {edge.source_node}")
            if edge.target_node not in valid_node_ids:
                errors.append(f"边引用的目标节点不存在: {edge.target_node}")
            if (
                edge.source_node in valid_node_ids
                and edge.target_node in valid_node_ids
                and edge.source_node == edge.target_node
            ):
                errors.append(f"自环边不允许: {edge.source_node} → {edge.target_node}")

        # 过滤掉引用不存在节点的边（避免后续分析出错）
        valid_edges: list[FlowEdge] = [
            e
            for e in edge_list
            if e.source_node in valid_node_ids
            and e.target_node in valid_node_ids
            and e.source_node != e.target_node
        ]

        # ---- 3. Kahn 拓扑排序 + 环检测 ----
        in_degree: dict[str, int] = {n.node_id: 0 for n in node_list}
        adjacency: dict[str, list[str]] = {n.node_id: [] for n in node_list}

        for edge in valid_edges:
            adjacency[edge.source_node].append(edge.target_node)
            in_degree[edge.target_node] += 1

        queue: list[str] = [nid for nid, deg in in_degree.items() if deg == 0]
        sorted_count: int = 0

        while queue:
            current: str = queue.pop(0)
            sorted_count += 1
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if sorted_count != len(node_list):
            cycle_nodes: list[str] = [nid for nid, deg in in_degree.items() if deg > 0]
            errors.append(f"流程存在环（DAG 校验失败），涉及节点: {sorted(cycle_nodes)}")

        # ---- 4. 警告：孤立节点 ----
        connected: set[str] = set()
        for edge in valid_edges:
            connected.add(edge.source_node)
            connected.add(edge.target_node)
        for node in node_list:
            if node.node_id not in connected:
                warnings.append(f"节点 {node.node_id} 无任何输入/输出边（孤立节点）")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    @staticmethod
    async def check_port_types(
        nodes: Iterable[FlowNode],
        edges: Iterable[FlowEdge],
        registry: ComponentRegistryService,
    ) -> ValidationResult:
        """端口类型兼容性校验。

        查询每个节点的 ComponentVersion，从 port_schemas 获取
        输入/输出端口规格，验证边的源端口 data_type 与目标端口
        data_type 兼容（相同类型或源端口类型为 ``"any"``）。

        Args:
            nodes: 节点集合。
            edges: 边集合。
            registry: 组件注册表服务（查询 manifest 端口规格）。

        Returns:
            ValidationResult: 校验结果。
        """
        errors: list[str] = []
        warnings: list[str] = []

        node_list: list[FlowNode] = list(nodes)
        edge_list: list[FlowEdge] = list(edges)

        # 构建 node_id → port_schemas 映射
        node_ports: dict[str, dict[str, Any]] = {}
        for node in node_list:
            try:
                version_row = await registry.get(
                    node.component_name,
                    node.component_version,
                )
                node_ports[node.node_id] = version_row.port_schemas
            except AppError:
                errors.append(
                    f"节点 {node.node_id} 引用的组件不存在: "
                    f"{node.component_name}@"
                    f"{node.component_version}"
                )

        # 校验每条边的端口类型兼容性
        for edge in edge_list:
            source_ports: list[dict[str, Any]] = node_ports.get(edge.source_node, {}).get(
                "outputs", []
            )
            target_ports: list[dict[str, Any]] = node_ports.get(edge.target_node, {}).get(
                "inputs", []
            )

            source_port: dict[str, Any] | None = next(
                (p for p in source_ports if p["name"] == edge.source_port),
                None,
            )
            target_port: dict[str, Any] | None = next(
                (p for p in target_ports if p["name"] == edge.target_port),
                None,
            )

            if source_port is None:
                errors.append(
                    f"边 {edge.source_node}:{edge.source_port} → "
                    f"{edge.target_node}:{edge.target_port} "
                    f"源端口不存在"
                )
                continue
            if target_port is None:
                errors.append(
                    f"边 {edge.source_node}:{edge.source_port} → "
                    f"{edge.target_node}:{edge.target_port} "
                    f"目标端口不存在"
                )
                continue

            source_type: str = source_port.get("data_type", "")
            target_type: str = target_port.get("data_type", "")

            # 类型兼容：相同类型或源为 "any"
            if source_type != target_type and source_type != "any" and target_type != "any":
                errors.append(
                    f"端口类型不兼容: "
                    f"{edge.source_node}:{edge.source_port}"
                    f"({source_type}) → "
                    f"{edge.target_node}:{edge.target_port}"
                    f"({target_type})"
                )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    @staticmethod
    def check_param_schema(
        node: FlowNode,
        manifest: ComponentManifest,
    ) -> ValidationResult:
        """节点参数 JSON Schema 校验。

        验证 node.params 是否符合 manifest.parameters 定义的 JSON Schema。
        同时校验必需输入端口是否有绑定。

        Args:
            node: 流程节点。
            manifest: 组件清单（含 parameters JSON Schema 与端口规格）。

        Returns:
            ValidationResult: 校验结果。
        """
        errors: list[str] = []
        warnings: list[str] = []

        # ---- 1. 参数 JSON Schema 校验 ----
        schema: dict[str, Any] = manifest.parameters
        if schema:
            try:
                jsonschema.validate(instance=node.params, schema=schema)
            except jsonschema.ValidationError as exc:
                errors.append(f"节点 {node.node_id} 参数校验失败: {exc.message}")

        # ---- 2. 必需输入端口绑定检查 ----
        for port in manifest.inputs:
            if port.required and port.name not in node.input_bindings:
                errors.append(f"节点 {node.node_id} 必需输入端口 '{port.name}' 未绑定")

        # ---- 3. 警告：未使用的可选输出端口 ----
        bound_targets: set[str] = set()
        for _port_name, binding in node.input_bindings.items():
            bound_targets.add(binding)
        for port in manifest.outputs:
            output_ref = f"{node.node_id}:{port.name}"
            if port.required and output_ref not in bound_targets:
                warnings.append(
                    f"节点 {node.node_id} 的必需输出端口 '{port.name}' 未被任何下游节点引用"
                )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
