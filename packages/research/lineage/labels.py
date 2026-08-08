"""节点展示标签生成器（阶段 5 新增）。

NodeDisplayLabelGenerator 为静态工具类，按命名空间映射到类型标签、图标和跳转目标。
受限节点统一返回 display_label="受限来源"。

参照架构设计 3.3 节 NodeDisplayLabelGenerator。
"""

from typing import Any
from uuid import UUID

from packages.research.dtos import NodeDisplayLabel

# 命名空间 → (类型标签, 图标) 映射表。
_NAMESPACE_LABELS: dict[str, tuple[str, str]] = {
    "core:fact": ("实验事实", "🔬"),
    "core:derivation_run": ("核心推导", "⚙️"),
    "core:evidence_set": ("证据集", "📋"),
    "research:evidence_snapshot": ("证据快照", "📸"),
    "research:analysis_run": ("分析运行", "▶️"),
    "research:analysis_step": ("分析步骤", "📝"),
    "research:derived_dataset": ("衍生数据", "📊"),
    "research:derived_dataset_version": ("衍生数据版本", "📊"),
    "research:dataset_version": ("衍生数据版本", "📊"),
    "research:view": ("图表", "📈"),
    "research:view_version": ("图表版本", "📈"),
    "research:insight": ("Insight", "💡"),
    "research:insight_version": ("Insight 版本", "💡"),
    "research:result_version": ("成果版本", "📦"),
    "research:workspace": ("研究空间", "🏠"),
    "research:knowledge_reference": ("知识库引用", "📚"),
    "restricted": ("受限来源", "🔒"),
}

# 命名空间 → 跳转 URL 前缀映射表。
# 受限节点不提供跳转目标。
_NAMESPACE_JUMP_PREFIX: dict[str, str] = {
    "core:fact": "/facts/{node_id}",
    "core:derivation_run": "/provenance/derivation-runs/{node_id}",
    "core:evidence_set": "/provenance/evidence-sets/{node_id}",
    "research:evidence_snapshot": "/research/workspaces/{extra}/snapshots/{node_id}",
    "research:analysis_run": "/research/runs/{node_id}",
    "research:analysis_step": "/research/runs/{extra}/steps/{node_id}",
    "research:derived_dataset": "/research/datasets/{node_id}",
    "research:derived_dataset_version": "/research/datasets/{extra}/versions/{node_id}",
    "research:dataset_version": "/research/datasets/{extra}/versions/{node_id}",
    "research:view": "/research/views/{node_id}",
    "research:view_version": "/research/views/{extra}/versions/{node_id}",
    "research:insight": "/research/insights/{node_id}",
    "research:insight_version": "/research/insights/{extra}/versions/{node_id}",
    "research:result_version": "/research/publications/{node_id}/versions/{version}",
    "research:workspace": "/research/workspaces/{node_id}",
    "research:knowledge_reference": "/research/knowledge-refs/{node_id}",
}


class NodeDisplayLabelGenerator:
    """节点展示标签生成器（静态工具类）。

    按命名空间映射到类型标签、图标和跳转目标。
    受限节点统一返回 display_label="受限来源"。
    """

    @staticmethod
    def generate(namespace: str, node_data: dict[str, Any]) -> NodeDisplayLabel:
        """生成节点展示标签。

        Args:
            namespace: 命名空间（如 "core:fact"）。
            node_data: 节点属性字典（含 name / version 等展示信息）。

        Returns:
            NodeDisplayLabel: 展示标签。
        """
        if namespace == "restricted":
            return NodeDisplayLabelGenerator.restricted_label()

        type_label, icon = _NAMESPACE_LABELS.get(namespace, ("未知", "❓"))

        # 生成 display_label：优先使用 node_data 中的 name / title / subject_id
        name = (
            node_data.get("name")
            or node_data.get("title")
            or node_data.get("subject_id")
            or node_data.get("conclusion", "")
        )
        if name and len(name) > 60:
            name = name[:57] + "..."

        # 生成 version_summary
        version = node_data.get("version")
        version_number = node_data.get("version_number")
        snapshot_number = node_data.get("snapshot_number")
        if version is not None:
            version_summary = f"v{version}"
        elif version_number is not None:
            version_summary = f"v{version_number}"
        elif snapshot_number is not None:
            version_summary = f"快照 #{snapshot_number}"
        else:
            version_summary = ""

        # 生成 jump_target
        jump_target = NodeDisplayLabelGenerator.get_jump_target(
            namespace, node_data.get("node_id", UUID(int=0))
        )

        return NodeDisplayLabel(
            display_label=name or type_label,
            node_type_label=type_label,
            version_summary=version_summary,
            namespace=namespace,
            icon=icon,
            jump_target=jump_target,
        )

    @staticmethod
    def get_type_label(namespace: str) -> str:
        """命名空间 → 类型标签映射。

        Args:
            namespace: 命名空间。

        Returns:
            str: 中文类型标签。
        """
        if namespace == "restricted":
            return "受限来源"
        return _NAMESPACE_LABELS.get(namespace, ("未知", "❓"))[0]

    @staticmethod
    def get_icon(namespace: str) -> str:
        """命名空间 → 图标映射。

        Args:
            namespace: 命名空间。

        Returns:
            str: 图标 emoji。
        """
        if namespace == "restricted":
            return "🔒"
        return _NAMESPACE_LABELS.get(namespace, ("未知", "❓"))[1]

    @staticmethod
    def get_jump_target(namespace: str, node_id: UUID) -> str | None:
        """命名空间 → 跳转目标 URL 映射。

        受限节点返回 None。

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。

        Returns:
            str | None: 跳转 URL，受限节点返回 None。
        """
        if namespace == "restricted":
            return None
        prefix = _NAMESPACE_JUMP_PREFIX.get(namespace)
        if prefix is None:
            return None
        return prefix.format(node_id=str(node_id), extra="{node_id}", version="{version}")

    @staticmethod
    def restricted_label() -> NodeDisplayLabel:
        """生成受限占位节点的展示标签。

        Returns:
            NodeDisplayLabel: 受限节点标签。
        """
        return NodeDisplayLabel(
            display_label="受限来源",
            node_type_label="受限来源",
            version_summary="",
            namespace="restricted",
            icon="🔒",
            jump_target=None,
        )
