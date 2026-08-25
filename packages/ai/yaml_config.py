"""AI 配置 YAML 加载器：从 config/models.yaml + config/ai-usage.yaml 读取 AI 配置。

将 AI 配置从数据库迁移到 YAML 文件，启动时加载到内存单例缓存，
运行时通过同步 API 读取，无需数据库查询。

用法：
    from packages.ai.yaml_config import (
        get_scenario_config,
        validate_ai_config,
        async_provider_wrapper,
    )

    # 启动校验（API lifespan / Worker 启动时调用）
    validate_ai_config()

    # 运行时获取场景配置（同步）
    config = get_scenario_config("assistant")
    provider = OpenAICompatibleProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        thinking_enabled=config.thinking_enabled,
    )

    # 组件系统适配（async callable）
    ai_config_provider = async_provider_wrapper("data_extraction")

配置文件：
    - config/models.yaml: provider 配置（base_url / api_key / models 列表）
    - config/ai-usage.yaml: 5 个场景的模型选择配置

配置路径：
    默认 Path(__file__).resolve().parent.parent.parent / "config"
    支持环境变量 IRIP_CONFIG_DIR 覆盖
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

#: 5 个内置场景名常量。
REQUIRED_SCENARIOS: frozenset[str] = frozenset(
    {
        "data_extraction",
        "assistant",
        "research",
        "title_generation",
    }
)

#: 模块级缓存：provider 列表（models.yaml 解析结果）。
_PROVIDERS_CACHE: list[ProviderConfig] | None = None

#: 模块级缓存：场景配置字典（ai-usage.yaml + models.yaml 交叉解析结果）。
_SCENARIO_CACHE: dict[str, ScenarioConfig] | None = None

#: 模块级缓存：配置文件 mtime（用于热重载检测）。
_CACHE_MTIMES: dict[str, float] | None = None

#: 匹配 `${VAR}` 形式的环境变量占位符（仅限整串完全匹配，避免误伤明文中的 ``$``）。
_ENV_VAR_PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderConfig:
    """单个 AI provider 的配置（models.yaml 中一个 provider 条目的内存表示）。

    Attributes:
        name: provider 唯一标识（如 "deepseek"）。
        base_url: API 基础 URL（如 "http://10.1.2.1:18881/v1"）。
        api_key: API 密钥（明文）。
        models: 该 provider 下可用模型名列表。
    """

    name: str
    base_url: str
    api_key: str
    models: list[str]


@dataclass(frozen=True)
class ScenarioConfig:
    """单个 AI 场景的完整连接配置（YAML 加载后的内存表示）。

    由 ai-usage.yaml 中的场景配置 + models.yaml 中的 provider 信息组合而成。

    Attributes:
        provider_name: provider 名称（如 "deepseek"）。
        base_url: API 基础 URL。
        api_key: API 密钥（明文）。
        model: 模型名（如 "DeepSeek"，不含 provider 前缀）。
        thinking_enabled: 是否启用思考模式。
    """

    provider_name: str
    base_url: str
    api_key: str
    model: str
    thinking_enabled: bool


# ---------------------------------------------------------------------------
# Config Directory Resolution
# ---------------------------------------------------------------------------


def _get_config_dir() -> Path:
    """获取配置目录路径。

    优先使用环境变量 IRIP_CONFIG_DIR，否则使用项目默认路径
    Path(__file__).resolve().parent.parent.parent / "config"
    （与 prompt_store.py 一致）。

    Returns:
        Path: 配置目录路径。
    """
    env_dir = os.getenv("IRIP_CONFIG_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return Path(__file__).resolve().parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# Cache Management
# ---------------------------------------------------------------------------


def _reset_cache() -> None:
    """清除模块级缓存（主要用于测试和热重载）。

    将 provider 和 scenario 缓存重置为 None，下次调用 load_config() 时重新加载。
    """
    global _PROVIDERS_CACHE, _SCENARIO_CACHE, _CACHE_MTIMES
    _PROVIDERS_CACHE = None
    _SCENARIO_CACHE = None
    _CACHE_MTIMES = None


def reload() -> None:
    """强制重新加载 YAML 配置（清除缓存）。

    可用于手动触发热重载。
    """
    _reset_cache()
    logger.info("AI config cache cleared, will reload on next access")


def _check_and_reload_if_changed() -> bool:
    """检测配置文件是否被修改，若修改则清除缓存。

    通过比较文件 mtime 判断是否需要重新加载。

    Returns:
        bool: True 表示配置已变更并清除了缓存（下次访问会重新加载），
              False 表示配置未变更。
    """
    if _CACHE_MTIMES is None:
        return False

    config_dir = _get_config_dir()
    for filename, old_mtime in _CACHE_MTIMES.items():
        path = config_dir / filename
        if not path.exists():
            continue
        current_mtime = path.stat().st_mtime
        if current_mtime != old_mtime:
            logger.info("AI config file changed: %s, reloading...", filename)
            _reset_cache()
            return True
    return False


# ---------------------------------------------------------------------------
# YAML Parsing
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    """读取并解析 YAML 文件。

    Args:
        path: YAML 文件路径。

    Returns:
        dict: 解析后的 YAML 内容。

    Raises:
        FileNotFoundError: 文件不存在。
        yaml.YAMLError: YAML 解析失败。
    """
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    return data  # type: ignore[no-any-return]


def _resolve_api_key(raw_api_key: str, provider_index: int) -> str:
    """解析 api_key，支持 ``${VAR}`` 环境变量占位符展开。

    若 ``api_key`` 形如 ``${IRIP_LLM_HCRDI_API_KEY}``，则从 ``os.environ``
    读取同名环境变量替换之；若环境变量未设置或为空，fail-fast 抛
    ``ValueError``（错误信息只回显变量名，绝不回显任何密钥值）。

    若 ``api_key`` 不是占位符（例如测试用明文），保持原样返回，向后兼容。

    Args:
        raw_api_key: models.yaml 中的原始 api_key 字符串（已通过非空校验）。
        provider_index: provider 在列表中的索引（用于错误信息定位）。

    Returns:
        str: 展开后的 api_key。

    Raises:
        ValueError: 占位符对应的环境变量未设置或为空。
    """
    match = _ENV_VAR_PLACEHOLDER_RE.fullmatch(raw_api_key)
    if match is None:
        # 非占位符，保持明文原样（向后兼容，测试/本地直连场景仍可用）。
        return raw_api_key

    var_name = match.group(1)
    resolved = os.environ.get(var_name)
    if not resolved or not resolved.strip():
        # 注意：错误信息只出现变量名，绝不拼接任何密钥值，避免密钥二次泄露。
        raise ValueError(
            f"models.yaml: providers[{provider_index}].api_key 使用了环境变量"
            f"占位符 ${{{var_name}}}，但环境变量 {var_name} 未设置或为空。"
            f"请在启动前通过 `export {var_name}=...` 注入真实密钥。"
        )
    return resolved


def _parse_providers(models_data: dict[str, Any]) -> list[ProviderConfig]:
    """解析 models.yaml 数据为 ProviderConfig 列表。

    Args:
        models_data: models.yaml 解析后的字典。

    Returns:
        list[ProviderConfig]: provider 配置列表。

    Raises:
        ValueError: schema 不合法。
    """
    raw_providers = models_data.get("providers")
    if not isinstance(raw_providers, list):
        raise ValueError("models.yaml: 'providers' 必须是非空列表")

    providers: list[ProviderConfig] = []
    for i, item in enumerate(raw_providers):
        if not isinstance(item, dict):
            raise ValueError(f"models.yaml: providers[{i}] 必须是字典")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"models.yaml: providers[{i}].name 必须是非空字符串")

        base_url = item.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError(f"models.yaml: providers[{i}].base_url 必须是非空字符串")

        api_key = item.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError(f"models.yaml: providers[{i}].api_key 必须是非空字符串")

        # 展开 ${VAR} 占位符；非占位符明文保持原样（向后兼容）。
        api_key = _resolve_api_key(api_key, i)

        raw_models = item.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError(f"models.yaml: providers[{i}].models 必须是非空列表")

        models: list[str] = []
        for m in raw_models:
            if not isinstance(m, str) or not m.strip():
                raise ValueError(f"models.yaml: providers[{i}].models 包含非字符串或空值")
            models.append(m)

        providers.append(
            ProviderConfig(
                name=name,
                base_url=base_url,
                api_key=api_key,
                models=models,
            )
        )

    return providers


def _find_provider_for_model(model_ref: str) -> ProviderConfig:
    """解析模型引用，返回对应的 ProviderConfig。

    支持两种格式：
    - "DeepSeek"：仅模型名，在所有 provider 中搜索。如果多个 provider
      包含同名模型，抛出 ValueError 要求使用消歧格式。
    - "deepseek/DeepSeek"：provider/model 格式，精确指定 provider。

    Args:
        model_ref: 模型引用字符串。

    Returns:
        ProviderConfig: 匹配的 provider 配置。

    Raises:
        ValueError: 模型未找到或存在歧义。
        RuntimeError: 缓存未加载。
    """
    if _PROVIDERS_CACHE is None:
        raise RuntimeError("配置未加载，请先调用 load_config()")

    if "/" in model_ref:
        provider_name, model_name = model_ref.split("/", 1)
        for p in _PROVIDERS_CACHE:
            if p.name == provider_name and model_name in p.models:
                return p
        available = list_available_models()
        raise ValueError(f"模型 '{model_ref}' 未找到。可用模型: {available}")
    else:
        matches = [p for p in _PROVIDERS_CACHE if model_ref in p.models]
        if len(matches) == 0:
            available = list_available_models()
            raise ValueError(f"模型 '{model_ref}' 未找到。可用模型: {available}")
        if len(matches) > 1:
            provider_names = [p.name for p in matches]
            raise ValueError(
                f"模型 '{model_ref}' 在多个 provider 中存在: {provider_names}。"
                f"请使用 'provider/model' 格式消歧。"
            )
        return matches[0]


def _parse_scenarios(
    usage_data: dict[str, Any],
    providers: list[ProviderConfig],
) -> dict[str, ScenarioConfig]:
    """解析 ai-usage.yaml 数据为 ScenarioConfig 字典。

    交叉校验：每个场景的 model 引用必须在 providers 中可解析。

    Args:
        usage_data: ai-usage.yaml 解析后的字典。
        providers: 已解析的 provider 列表。

    Returns:
        dict[str, ScenarioConfig]: 场景名 → 场景配置。

    Raises:
        ValueError: schema 不合法或模型引用无法解析。
    """
    raw_scenarios = usage_data.get("scenarios")
    if not isinstance(raw_scenarios, dict):
        raise ValueError("ai-usage.yaml: 'scenarios' 必须是字典")

    # 临时设置 _PROVIDERS_CACHE 以便 _find_provider_for_model 可用
    global _PROVIDERS_CACHE
    _PROVIDERS_CACHE = providers

    scenarios: dict[str, ScenarioConfig] = {}
    for scenario_name, scenario_data in raw_scenarios.items():
        if not isinstance(scenario_data, dict):
            raise ValueError(f"ai-usage.yaml: scenarios.{scenario_name} 必须是字典")

        model_ref = scenario_data.get("model")
        if not isinstance(model_ref, str) or not model_ref.strip():
            raise ValueError(f"ai-usage.yaml: scenarios.{scenario_name}.model 必须是非空字符串")

        thinking_enabled = scenario_data.get("thinking_enabled")
        if not isinstance(thinking_enabled, bool):
            raise ValueError(
                f"ai-usage.yaml: scenarios.{scenario_name}.thinking_enabled 必须是布尔值"
            )

        # 解析模型引用 → provider
        provider = _find_provider_for_model(model_ref)

        # 提取纯模型名（去除 provider 前缀）
        if "/" in model_ref:
            model_name = model_ref.split("/", 1)[1]
        else:
            model_name = model_ref

        scenarios[scenario_name] = ScenarioConfig(
            provider_name=provider.name,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=model_name,
            thinking_enabled=thinking_enabled,
        )

    return scenarios


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config() -> None:
    """加载并缓存 models.yaml + ai-usage.yaml，构建 scenario 索引。

    幂等：多次调用安全，仅首次实际加载。

    Raises:
        FileNotFoundError: 配置文件不存在。
        ValueError: schema 不合法或模型引用无法解析。
        yaml.YAMLError: YAML 解析失败。
    """
    global _PROVIDERS_CACHE, _SCENARIO_CACHE

    if _SCENARIO_CACHE is not None and _PROVIDERS_CACHE is not None:
        return  # 已加载，跳过

    config_dir = _get_config_dir()
    models_path = config_dir / "models.yaml"
    usage_path = config_dir / "ai-usage.yaml"

    logger.debug("Loading AI config from %s", config_dir)

    # 解析 models.yaml
    models_data = _read_yaml(models_path)
    providers = _parse_providers(models_data)

    # 解析 ai-usage.yaml（内部会临时设置 _PROVIDERS_CACHE）
    usage_data = _read_yaml(usage_path)
    scenarios = _parse_scenarios(usage_data, providers)

    # 原子写入缓存
    _PROVIDERS_CACHE = providers
    _SCENARIO_CACHE = scenarios

    # 记录文件 mtime 用于热重载检测
    global _CACHE_MTIMES
    _CACHE_MTIMES = {
        "models.yaml": models_path.stat().st_mtime,
        "ai-usage.yaml": usage_path.stat().st_mtime,
    }

    logger.info(
        "AI config loaded: %d providers, %d scenarios",
        len(providers),
        len(scenarios),
    )


def validate_ai_config() -> None:
    """启动校验：文件存在 + schema 完整性 + 模型名引用合法性。

    校验内容：
    - models.yaml 和 ai-usage.yaml 存在且可解析
    - providers 非空列表
    - provider name 唯一
    - base_url 为合法 http/https URL
    - api_key 非空
    - models 非空列表，同一 provider 下无重复
    - 5 个内置场景齐全
    - 每个场景的 model 引用可解析

    失败时 logger.error + sys.exit(1)。
    """
    try:
        load_config()
    except (FileNotFoundError, ValueError, yaml.YAMLError) as e:
        logger.error("AI 配置加载失败: %s", e)
        sys.exit(1)

    assert _PROVIDERS_CACHE is not None
    assert _SCENARIO_CACHE is not None

    # 校验 providers 非空
    if not _PROVIDERS_CACHE:
        logger.error("AI 配置校验失败: providers 列表为空")
        sys.exit(1)

    # 校验 provider name 唯一
    names = [p.name for p in _PROVIDERS_CACHE]
    if len(names) != len(set(names)):
        duplicates = [n for n in names if names.count(n) > 1]
        logger.error("AI 配置校验失败: provider 名称重复: %s", duplicates)
        sys.exit(1)

    # 校验每个 provider 的字段
    for p in _PROVIDERS_CACHE:
        # URL 合法性
        parsed = urlparse(p.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            logger.error(
                "AI 配置校验失败: provider '%s' 的 base_url 无效: %s",
                p.name,
                p.base_url,
            )
            sys.exit(1)

        # api_key 非空
        if not p.api_key.strip():
            logger.error(
                "AI 配置校验失败: provider '%s' 的 api_key 为空",
                p.name,
            )
            sys.exit(1)

        # models 非空
        if not p.models:
            logger.error(
                "AI 配置校验失败: provider '%s' 的 models 列表为空",
                p.name,
            )
            sys.exit(1)

        # models 无重复
        if len(p.models) != len(set(p.models)):
            logger.error(
                "AI 配置校验失败: provider '%s' 的 models 列表有重复: %s",
                p.name,
                p.models,
            )
            sys.exit(1)

    # 校验 5 个内置场景齐全
    missing = REQUIRED_SCENARIOS - set(_SCENARIO_CACHE.keys())
    if missing:
        logger.error(
            "AI 配置校验失败: 缺少必需场景: %s（必需: %s）",
            sorted(missing),
            sorted(REQUIRED_SCENARIOS),
        )
        sys.exit(1)

    logger.info(
        "AI config validated: %d providers, %d scenarios — all checks passed",
        len(_PROVIDERS_CACHE),
        len(_SCENARIO_CACHE),
    )


def get_scenario_config(scenario_name: str) -> ScenarioConfig:
    """同步获取场景配置。

    未加载时自动触发 load_config()。

    Args:
        scenario_name: 场景名（如 "assistant"）。

    Returns:
        ScenarioConfig: 场景配置。

    Raises:
        KeyError: 场景不存在。
        FileNotFoundError: 配置文件不存在（自动加载时）。
        ValueError: schema 不合法（自动加载时）。
    """
    if _SCENARIO_CACHE is None:
        load_config()

    # 热重载检测：文件被修改后自动清除缓存并重新加载
    _check_and_reload_if_changed()
    if _SCENARIO_CACHE is None:
        load_config()

    assert _SCENARIO_CACHE is not None

    if scenario_name not in _SCENARIO_CACHE:
        raise KeyError(f"场景 '{scenario_name}' 不存在。可用场景: {list(_SCENARIO_CACHE.keys())}")
    return _SCENARIO_CACHE[scenario_name]


def async_provider_wrapper(scenario: str) -> Callable[[], Awaitable[dict[str, Any]]]:
    """适配函数：返回 async callable，供 ComponentContext.ai_config_provider 使用。

    内部调用 get_scenario_config(scenario) 并转为 dict 格式（兼容现有组件代码）。

    返回的 dict 格式：
        {
            "base_url": "...",
            "api_key": "...",
            "model_name": "...",      # 注意：key 是 model_name，不是 model
            "thinking_enabled": False,
        }

    Args:
        scenario: 场景名（如 "data_extraction"）。

    Returns:
        Callable[[], Awaitable[dict]]: async callable，调用时返回配置 dict。
    """

    async def _provider() -> dict[str, Any]:
        config = get_scenario_config(scenario)
        return {
            "base_url": config.base_url,
            "api_key": config.api_key,
            "model_name": config.model,
            "thinking_enabled": config.thinking_enabled,
        }

    return _provider


def list_available_models() -> list[str]:
    """列出 models.yaml 中所有可用模型名（含 provider 前缀）。

    返回格式为 "provider/model" 列表，用于错误信息和消歧提示。

    Returns:
        list[str]: 所有可用模型名列表（含 provider 前缀）。
    """
    if _PROVIDERS_CACHE is None:
        load_config()

    assert _PROVIDERS_CACHE is not None

    models: list[str] = []
    for p in _PROVIDERS_CACHE:
        for m in p.models:
            models.append(f"{p.name}/{m}")
    return models
