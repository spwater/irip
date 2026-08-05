"""流程 DAG 辅助函数。

纯函数，无副作用：
- ``topological_sort``: Kahn 算法拓扑排序，返回节点 ID 的执行顺序；
- ``resolve_input``: 解析输入绑定，从上游节点输出或外部输入获取数据。
"""

from __future__ import annotations

from typing import Any

from packages.common.errors import AppError
from packages.components.flow.flows import FlowEdge, FlowNode


def topological_sort(
    nodes: tuple[FlowNode, ...],
    edges: tuple[FlowEdge, ...],
) -> list[str]:
    """Kahn 算法拓扑排序，返回节点 ID 的执行顺序。

    Args:
        nodes: 节点元组。
        edges: 边元组。

    Returns:
        list[str]: 拓扑排序后的节点 ID 列表。

    Raises:
        AppError: code="validation_failed"，当存在环。
    """
    in_degree: dict[str, int] = {n.node_id: 0 for n in nodes}
    adjacency: dict[str, list[str]] = {n.node_id: [] for n in nodes}

    for edge in edges:
        adjacency[edge.source_node].append(edge.target_node)
        in_degree[edge.target_node] += 1

    queue: list[str] = [nid for nid, deg in in_degree.items() if deg == 0]
    order: list[str] = []

    while queue:
        current: str = queue.pop(0)
        order.append(current)
        for neighbor in adjacency[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(nodes):
        raise AppError(
            code="validation_failed",
            message="流程存在环，无法拓扑排序",
            retryable=False,
            fields={},
        )

    return order


def resolve_input(
    binding: str,
    node_outputs: dict[str, dict[str, Any]],
    input_snapshot: dict[str, Any],
) -> Any:
    """解析输入绑定，从上游节点输出或外部输入获取数据。

    绑定格式：
    - ``"<source_node_id>:<source_port>"`` → 上游节点输出；
    - ``"<external_input_name>"`` → 流程外部输入。

    Args:
        binding: 绑定字符串。
        node_outputs: 已执行节点的输出映射。
        input_snapshot: 流程外部输入快照。

    Returns:
        Any: 解析后的输入数据（找不到时为 None）。
    """
    if ":" in binding:
        parts: list[str] = binding.split(":", 1)
        src_node: str = parts[0]
        src_port: str = parts[1]
        return node_outputs.get(src_node, {}).get(src_port)
    return input_snapshot.get(binding)
