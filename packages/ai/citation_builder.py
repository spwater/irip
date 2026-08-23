"""工具 → 前端导航 citation 构建辅助函数。

从 ``ask_service.py`` 提取的模块级辅助逻辑，负责根据工具调用名称与返回结果
构建带 ``href`` 的 :class:`Citation` 对象，供前端跳转到对应业务详情页。

设计要点：
- ``_TOOL_CITATION_CONFIG`` 维护工具名 → (object_type, href_prefix) 的映射；
- 各 ``_extract_*`` 函数从工具参数 / 结果中抽取 object_id / label / version；
- 所有函数保持 ``_`` 前缀，表明它们是内部实现细节。
"""

from __future__ import annotations

from typing import Any

from packages.ai.citations import Citation

# ── 工具 → 前端导航 citation 映射 ─────────────────────────────
# 根据工具名称和返回结果，构建带 href 的 Citation 对象供前端跳转。
_TOOL_CITATION_CONFIG: dict[str, dict[str, str]] = {
    "search_facts": {"object_type": "fact_revision", "href_prefix": "/facts/"},
    "search_parameters": {"object_type": "parameter_version", "href_prefix": "/parameters/"},
    "run_published_model": {"object_type": "model_version", "href_prefix": "/models/"},
}


def _build_nav_citation(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_result: dict[str, Any],
    display_name: str,
) -> Citation | None:
    """根据工具调用结果构建前端导航 citation。

    从工具返回结果中提取 object_id，结合工具名称映射到前端路由路径。

    Args:
        tool_name: 工具名称。
        tool_args: 工具调用参数。
        tool_result: 工具执行结果字典。
        display_name: 工具显示名称（用作 label 回退）。

    Returns:
        Citation | None: 能构建出有效引用时返回 Citation，否则 None。
    """
    config = _TOOL_CITATION_CONFIG.get(tool_name)
    if config is None:
        return None

    object_id = _extract_object_id(tool_name, tool_args, tool_result)
    if not object_id:
        return None

    label = _extract_label(tool_name, tool_result, display_name)
    version = _extract_version(tool_result)

    return Citation(
        object_type=config["object_type"],
        object_id=object_id,
        version=version,
        label=label,
        href=f"{config['href_prefix']}{object_id}",
    )


def _extract_object_id(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_result: dict[str, Any],
) -> str:
    """从工具参数或结果中提取 object_id。"""
    # search_facts / search_parameters: 结果中的第一条记录的 id/fact_id
    if tool_name in ("search_facts", "search_parameters"):
        data = tool_result.get("data", tool_result)
        if isinstance(data, dict):
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        if items and isinstance(items[0], dict):
            return str(items[0].get("id") or items[0].get("fact_id") or "")
        # 回退：从参数中找 fact_ids / parameter_id
        if (
            "fact_ids" in tool_args
            and isinstance(tool_args["fact_ids"], list)
            and tool_args["fact_ids"]
        ):
            return str(tool_args["fact_ids"][0])
        if "parameter_id" in tool_args:
            return str(tool_args["parameter_id"])
        return ""

    # run_published_model: 参数中的 model_id
    if tool_name == "run_published_model":
        return str(tool_args.get("model_id", ""))

    return ""


def _extract_label(tool_name: str, tool_result: dict[str, Any], display_name: str) -> str:
    """从工具结果中提取可读标签。"""
    summary = str(tool_result.get("summary", ""))
    if summary:
        # 截取摘要前 60 字符作为标签
        return summary[:60] if len(summary) > 60 else summary
    return display_name


def _extract_version(tool_result: dict[str, Any]) -> str:
    """从工具结果中提取版本标识。"""
    data = tool_result.get("data", tool_result)
    if isinstance(data, dict):
        items = data.get("items", [])
        if items and isinstance(items[0], dict):
            v = items[0].get("version") or items[0].get("latest_version_number")
            if v is not None:
                return f"v{v}" if isinstance(v, int) else str(v)
    return ""
