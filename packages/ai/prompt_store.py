"""系统提示词加载器：从 config/prompts.yaml 读取提示词。

将所有大模型系统提示词从代码硬编码迁移到 YAML 配置文件，
方便集中管理和编辑，无需修改代码即可调整提示词。

用法：
    from packages.ai.prompt_store import get_prompt, get_prompt_version

    # 获取静态提示词
    system_prompt = get_prompt("research_recommendation.system_prompt")

    # 获取带模板变量的提示词（调用方负责 .format()）
    system_prompt = get_prompt("code_generation.system_prompt").format(
        question=question, expected_output=expected_output
    )

    # 获取版本号
    version = get_prompt_version("research_recommendation")

热重载：
    设置环境变量 IRIP_PROMPT_HOT_RELOAD=1 后，每次 get_prompt 都从磁盘读取。
    生产环境不推荐开启（默认关闭，启动时加载一次并缓存）。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

#: YAML 配置文件路径（相对于本模块：../../config/prompts.yaml）
_CONFIG_PATH: Path = Path(__file__).resolve().parent.parent.parent / "config" / "prompts.yaml"

#: 热重载开关（环境变量控制，默认关闭）
_HOT_RELOAD: bool = os.getenv("IRIP_PROMPT_HOT_RELOAD", "").lower() in ("1", "true", "yes")


def _load_yaml() -> dict[str, Any]:
    """加载 YAML 配置文件。

    Returns:
        dict: 解析后的 YAML 内容。

    Raises:
        FileNotFoundError: 配置文件不存在。
        yaml.YAMLError: YAML 解析失败。
    """
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"提示词配置文件不存在: {_CONFIG_PATH}\n"
            "请确保 config/prompts.yaml 已创建。"
        )
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    logger.debug("Loaded prompts from %s, top-level keys: %s", _CONFIG_PATH, list(data.keys()))
    return data


@lru_cache(maxsize=1)
def _get_data_cached() -> dict[str, Any]:
    """加载并缓存 YAML 数据（lru_cache 保证启动时只加载一次）。

    Returns:
        dict: 解析后的 YAML 内容。
    """
    return _load_yaml()


def _get_data() -> dict[str, Any]:
    """获取 YAML 数据（根据热重载设置决定是否走缓存）。

    Returns:
        dict: 解析后的 YAML 内容。
    """
    if _HOT_RELOAD:
        return _load_yaml()
    return _get_data_cached()


def _resolve_dotted_key(data: dict[str, Any], dotted_key: str) -> Any:
    """按点分键路径解析嵌套字典。

    示例: "research_recommendation.system_prompt"
    -> data["research_recommendation"]["system_prompt"]

    Args:
        data: YAML 解析后的字典。
        dotted_key: 点分键路径。

    Returns:
        对应的值，不存在时返回 None。
    """
    keys = dotted_key.split(".")
    val: Any = data
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
    return val


def get_prompt(dotted_key: str, default: str = "") -> str:
    """按点分键获取系统提示词。

    Args:
        dotted_key: 点分键路径，如 "research_recommendation.system_prompt"。
        default: 键不存在时的回退值。

    Returns:
        str: 提示词文本。键不存在时返回 default。
    """
    val = _resolve_dotted_key(_get_data(), dotted_key)
    if val is None:
        logger.warning("Prompt key not found: %s, using default", dotted_key)
        return default
    return str(val)


def get_prompt_version(section: str, default: str = "") -> str:
    """获取某个提示词分区的版本号。

    Args:
        section: 分区名，如 "research_recommendation"。
        default: 版本不存在时的回退值。

    Returns:
        str: 版本号字符串。
    """
    return get_prompt(f"{section}.version", default)


def get_prompt_section(section: str) -> dict[str, Any]:
    """获取整个提示词分区（含 description、version、system_prompt 等）。

    Args:
        section: 分区名，如 "research_recommendation"。

    Returns:
        dict: 分区字典。不存在时返回空字典。
    """
    val = _resolve_dotted_key(_get_data(), section)
    if isinstance(val, dict):
        return val
    return {}


def reload() -> None:
    """强制重新加载 YAML 配置（清除缓存）。

    热重载模式下不需要调用此方法（每次读取都从磁盘加载）。
    非热重载模式下调用此方法可清除缓存，下次读取时重新加载。
    """
    _get_data_cached.cache_clear()
    logger.info("Prompt store cache cleared, will reload on next access")


def list_sections() -> list[str]:
    """列出所有提示词分区名。

    Returns:
        list[str]: 顶层键列表。
    """
    return list(_get_data().keys())
