"""IRIP 流程定义值对象：节点、边、版本化流程定义。

提供 frozen dataclass 值对象：
- FlowNode: 流程节点（绑定组件 + 参数 + 输入映射）；
- FlowEdge: 流程边（源节点端口 → 目标节点端口）；
- FlowDefinitionVersion: 版本化流程定义（节点 + 边 + 随机种子 + SHA-256 摘要）。

设计要点（IRIP V2-T03）：
- 所有值对象为 frozen dataclass，确保不可变性与哈希稳定性；
- FlowDefinitionVersion.sha256 为节点+边+随机种子的内容寻址摘要，
  相同内容 → 相同摘要，用于版本不可变性校验；
- nodes/edges 使用 tuple 而非 list，保证不可变性与可哈希；
- 提供序列化/反序列化辅助函数，供 ORM JSONB 存储与 API 传输使用。
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FlowNode:
    """流程节点（不可变值对象）。

    描述流程中的一个执行节点，绑定到特定版本的组件，
    并携带该节点的参数与输入端口映射。

    Attributes:
        node_id: 节点唯一标识（流程内唯一，如 ``"ingest_1"``）。
        component_name: 组件名称（如 ``"csv_ingestion"``）。
        component_version: 组件语义化版本（如 ``"1.0.0"``）。
        params: 节点参数（应已通过组件 manifest parameters
            JSON Schema 校验）。
        input_bindings: 输入端口绑定映射，
            键为该节点的输入端口名，值为上游引用字符串。
            格式 ``"<source_node_id>:<source_port>"`` 引用上游节点输出，
            或 ``"<external_input_name>"`` 引用流程外部输入。
    """

    node_id: str
    component_name: str
    component_version: str
    params: dict[str, Any] = field(default_factory=dict)
    input_bindings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FlowEdge:
    """流程边（不可变值对象）。

    描述从源节点的输出端口到目标节点的输入端口的数据流。

    Attributes:
        source_node: 源节点 ID。
        source_port: 源节点输出端口名。
        target_node: 目标节点 ID。
        target_port: 目标节点输入端口名。
    """

    source_node: str
    source_port: str
    target_node: str
    target_port: str


@dataclass(frozen=True)
class FlowDefinitionVersion:
    """版本化流程定义（不可变值对象）。

    封装一个流程版本的完整定义：节点、边、随机种子与内容摘要。
    版本一旦发布即不可变，sha256 用于校验版本完整性。

    Attributes:
        version: 版本号（从 1 递增）。
        nodes: 节点元组（流程内 node_id 唯一）。
        edges: 边元组（描述节点间数据流）。
        random_seed: 随机种子（确保可复现性）。
        sha256: 节点+边+随机种子的 SHA-256 摘要（hex 小写）。
    """

    version: int
    nodes: tuple[FlowNode, ...]
    edges: tuple[FlowEdge, ...]
    random_seed: int = 0
    sha256: str = ""


# ---- 序列化辅助函数 ----


def node_to_dict(node: FlowNode) -> dict[str, Any]:
    """将 FlowNode 序列化为 JSON 兼容字典。

    Args:
        node: 流程节点。

    Returns:
        dict[str, Any]: JSON 兼容字典。
    """
    return {
        "node_id": node.node_id,
        "component_name": node.component_name,
        "component_version": node.component_version,
        "params": node.params,
        "input_bindings": node.input_bindings,
    }


def edge_to_dict(edge: FlowEdge) -> dict[str, Any]:
    """将 FlowEdge 序列化为 JSON 兼容字典。

    Args:
        edge: 流程边。

    Returns:
        dict[str, Any]: JSON 兼容字典。
    """
    return {
        "source_node": edge.source_node,
        "source_port": edge.source_port,
        "target_node": edge.target_node,
        "target_port": edge.target_port,
    }


def node_from_dict(data: dict[str, Any]) -> FlowNode:
    """从字典反序列化 FlowNode。

    Args:
        data: JSON 兼容字典。

    Returns:
        FlowNode: 流程节点值对象。
    """
    return FlowNode(
        node_id=data["node_id"],
        component_name=data["component_name"],
        component_version=data["component_version"],
        params=data.get("params", {}),
        input_bindings=data.get("input_bindings", {}),
    )


def edge_from_dict(data: dict[str, Any]) -> FlowEdge:
    """从字典反序列化 FlowEdge。

    Args:
        data: JSON 兼容字典。

    Returns:
        FlowEdge: 流程边值对象。
    """
    return FlowEdge(
        source_node=data["source_node"],
        source_port=data["source_port"],
        target_node=data["target_node"],
        target_port=data["target_port"],
    )


def nodes_to_json(nodes: tuple[FlowNode, ...]) -> list[dict[str, Any]]:
    """将节点元组序列化为 JSON 列表。

    Args:
        nodes: 节点元组。

    Returns:
        list[dict[str, Any]]: JSON 兼容列表。
    """
    return [node_to_dict(n) for n in nodes]


def edges_to_json(edges: tuple[FlowEdge, ...]) -> list[dict[str, Any]]:
    """将边元组序列化为 JSON 列表。

    Args:
        edges: 边元组。

    Returns:
        list[dict[str, Any]]: JSON 兼容列表。
    """
    return [edge_to_dict(e) for e in edges]


def nodes_from_json(data: list[dict[str, Any]]) -> tuple[FlowNode, ...]:
    """从 JSON 列表反序列化节点元组。

    Args:
        data: JSON 兼容列表。

    Returns:
        tuple[FlowNode, ...]: 节点元组。
    """
    return tuple(node_from_dict(d) for d in data)


def edges_from_json(data: list[dict[str, Any]]) -> tuple[FlowEdge, ...]:
    """从 JSON 列表反序列化边元组。

    Args:
        data: JSON 兼容列表。

    Returns:
        tuple[FlowEdge, ...]: 边元组。
    """
    return tuple(edge_from_dict(d) for d in data)


def compute_flow_digest(
    nodes: tuple[FlowNode, ...],
    edges: tuple[FlowEdge, ...],
    random_seed: int,
) -> str:
    """计算流程定义的 SHA-256 内容摘要。

    将节点、边、随机种子序列化为规范 JSON（排序键）后计算 SHA-256。
    相同内容 → 相同摘要，用于版本不可变性校验。

    Args:
        nodes: 节点元组。
        edges: 边元组。
        random_seed: 随机种子。

    Returns:
        str: 64 位小写十六进制 SHA-256 摘要。
    """
    payload: dict[str, Any] = {
        "nodes": nodes_to_json(nodes),
        "edges": edges_to_json(edges),
        "random_seed": random_seed,
    }
    canonical: str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
