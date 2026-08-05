"""流程执行序列化辅助函数。

纯函数，无副作用：
- ``compute_output_digest``: 计算流程执行的输出摘要（SHA-256）；
- ``serialize_output_summary``: 将 ComponentResult 输出序列化为 JSON 兼容摘要；
- ``serialize_input_summary``: 将节点输入序列化为 JSON 兼容摘要。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.components.sdk import ComponentResult


def compute_output_digest(
    version_digest: str,
    input_snapshot: dict[str, Any],
    node_executions: list[dict[str, Any]],
) -> str:
    """计算流程执行的输出摘要。

    Args:
        version_digest: 流程版本摘要。
        input_snapshot: 输入快照。
        node_executions: 节点执行摘要列表。

    Returns:
        str: SHA-256 摘要（hex 小写）。
    """
    payload: dict[str, Any] = {
        "version_digest": version_digest,
        "input_snapshot": input_snapshot,
        "node_summaries": node_executions,
    }
    canonical: str = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialize_output_summary(
    result: ComponentResult,
) -> dict[str, Any]:
    """将 ComponentResult 输出序列化为 JSON 兼容摘要。

    仅保留输出端口的键与可序列化值，非可序列化值转为字符串。

    Args:
        result: 组件执行结果。

    Returns:
        dict[str, Any]: JSON 兼容摘要。
    """
    summary: dict[str, Any] = {}
    for key, value in result.outputs.items():
        try:
            summary[key] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            summary[key] = str(value)
    summary["_metadata"] = result.metadata
    summary["_summary_text"] = result.summary
    return summary


def serialize_input_summary(
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """将节点输入序列化为 JSON 兼容摘要。

    Args:
        inputs: 节点输入字典。

    Returns:
        dict[str, Any]: JSON 兼容摘要。
    """
    summary: dict[str, Any] = {}
    for key, value in inputs.items():
        try:
            summary[key] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            summary[key] = str(value)
    return summary
