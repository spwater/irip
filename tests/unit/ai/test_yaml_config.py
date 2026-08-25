"""yaml_config.py 单元测试。

测试 YAML 配置加载、校验、场景配置读取、适配函数等。

使用 monkeypatch 设置 IRIP_CONFIG_DIR 环境变量指向临时目录，
在临时目录中创建 YAML 配置文件进行测试。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.ai.yaml_config import (
    ProviderConfig,
    ScenarioConfig,
    _find_provider_for_model,
    _reset_cache,
    async_provider_wrapper,
    get_scenario_config,
    list_available_models,
    load_config,
    validate_ai_config,
)

#: 测试用 models.yaml 内容。
_VALID_MODELS_YAML = """
providers:
  - name: deepseek
    base_url: "http://10.1.2.1:18881/v1"
    api_key: "test-api-key"
    models:
      - "DeepSeek"
      - "GLM"
      - "Qwen"
      - "Qwen-Flash"
  - name: openai
    base_url: "https://api.openai.com/v1"
    api_key: "sk-openai-key"
    models:
      - "gpt-4"
      - "Qwen"
"""

#: 测试用 ai-usage.yaml 内容（使用消歧格式引用 Qwen）。
_VALID_AI_USAGE_YAML = """
scenarios:
  data_extraction:
    model: "DeepSeek"
    thinking_enabled: false
  assistant:
    model: "deepseek/Qwen"
    thinking_enabled: true
  research:
    model: "DeepSeek"
    thinking_enabled: true
  conclusion:
    model: "Qwen-Flash"
    thinking_enabled: false
  title_generation:
    model: "Qwen-Flash"
    thinking_enabled: false
"""


def _write_config(
    config_dir: Path,
    models_yaml: str | None = None,
    usage_yaml: str | None = None,
) -> None:
    """在指定目录写入配置文件。

    Args:
        config_dir: 配置目录路径。
        models_yaml: models.yaml 内容，None 表示不创建。
        usage_yaml: ai-usage.yaml 内容，None 表示不创建。
    """
    if models_yaml is not None:
        (config_dir / "models.yaml").write_text(models_yaml, encoding="utf-8")
    if usage_yaml is not None:
        (config_dir / "ai-usage.yaml").write_text(usage_yaml, encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """每个测试前后清除缓存，确保测试隔离。"""
    _reset_cache()
    yield
    _reset_cache()


@pytest.fixture
def _valid_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """创建有效的配置目录并设置 IRIP_CONFIG_DIR。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_config(config_dir, _VALID_MODELS_YAML, _VALID_AI_USAGE_YAML)
    monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))
    return config_dir


# ---------------------------------------------------------------------------
# 正常加载
# ---------------------------------------------------------------------------


class TestNormalLoad:
    """测试正常加载场景。"""

    def test_get_scenario_config_returns_correct_config(self, _valid_config_dir: Path) -> None:
        """正常加载：get_scenario_config("assistant") 返回正确的 ScenarioConfig。"""
        load_config()
        config = get_scenario_config("assistant")

        assert isinstance(config, ScenarioConfig)
        assert config.provider_name == "deepseek"
        assert config.base_url == "http://10.1.2.1:18881/v1"
        assert config.api_key == "test-api-key"
        assert config.model == "Qwen"
        assert config.thinking_enabled is True

    def test_get_scenario_config_data_extraction(self, _valid_config_dir: Path) -> None:
        """data_extraction 场景配置正确。"""
        load_config()
        config = get_scenario_config("data_extraction")

        assert config.provider_name == "deepseek"
        assert config.model == "DeepSeek"
        assert config.thinking_enabled is False

    def test_load_config_is_idempotent(self, _valid_config_dir: Path) -> None:
        """多次调用 load_config() 安全，仅首次实际加载。"""
        load_config()
        first = get_scenario_config("assistant")

        load_config()  # 第二次调用应无副作用
        second = get_scenario_config("assistant")

        assert first == second

    def test_get_scenario_config_auto_triggers_load(self, _valid_config_dir: Path) -> None:
        """未调用 load_config() 时，get_scenario_config() 自动触发加载。"""
        config = get_scenario_config("assistant")
        assert config.model == "Qwen"


# ---------------------------------------------------------------------------
# 文件缺失
# ---------------------------------------------------------------------------


class TestFileMissing:
    """测试配置文件缺失场景。"""

    def test_validate_ai_config_exits_when_models_yaml_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """models.yaml 不存在时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_validate_ai_config_exits_when_usage_yaml_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ai-usage.yaml 不存在时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        _write_config(config_dir, models_yaml=_VALID_MODELS_YAML)
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()


# ---------------------------------------------------------------------------
# Schema 校验失败
# ---------------------------------------------------------------------------


class TestInvalidSchema:
    """测试 schema 校验失败场景。"""

    def test_missing_scenarios_key_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ai-usage.yaml 缺少 scenarios 键时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        _write_config(
            config_dir,
            models_yaml=_VALID_MODELS_YAML,
            usage_yaml="other_key: value\n",
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_missing_providers_key_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """models.yaml 缺少 providers 键时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        _write_config(
            config_dir,
            models_yaml="other_key: value\n",
            usage_yaml=_VALID_AI_USAGE_YAML,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_empty_providers_list_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """providers 列表为空时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        _write_config(
            config_dir,
            models_yaml="providers: []\n",
            usage_yaml=_VALID_AI_USAGE_YAML,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_duplicate_provider_names_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """provider 名称重复时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        dup_models = """
providers:
  - name: deepseek
    base_url: "http://host1/v1"
    api_key: "key1"
    models: ["DeepSeek"]
  - name: deepseek
    base_url: "http://host2/v1"
    api_key: "key2"
    models: ["GLM"]
"""
        _write_config(config_dir, models_yaml=dup_models, usage_yaml=_VALID_AI_USAGE_YAML)
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_invalid_base_url_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """base_url 非 http/https URL 时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        bad_url_models = """
providers:
  - name: deepseek
    base_url: "ftp://invalid/v1"
    api_key: "key1"
    models: ["DeepSeek"]
"""
        _write_config(config_dir, models_yaml=bad_url_models, usage_yaml=_VALID_AI_USAGE_YAML)
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_empty_api_key_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """api_key 为空时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        empty_key_models = """
providers:
  - name: deepseek
    base_url: "http://10.1.2.1:18881/v1"
    api_key: ""
    models: ["DeepSeek"]
"""
        _write_config(
            config_dir,
            models_yaml=empty_key_models,
            usage_yaml=_VALID_AI_USAGE_YAML,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_duplicate_models_in_provider_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同一 provider 下 models 重复时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        dup_models_yaml = """
providers:
  - name: deepseek
    base_url: "http://10.1.2.1:18881/v1"
    api_key: "key1"
    models: ["DeepSeek", "DeepSeek"]
"""
        _write_config(
            config_dir,
            models_yaml=dup_models_yaml,
            usage_yaml=_VALID_AI_USAGE_YAML,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()


# ---------------------------------------------------------------------------
# 模型名引用校验
# ---------------------------------------------------------------------------


class TestModelRefValidation:
    """测试模型名引用校验。"""

    def test_model_ref_not_found_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """场景引用不存在的模型时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        bad_usage = """
scenarios:
  data_extraction:
    model: "NonExistentModel"
    thinking_enabled: false
  assistant:
    model: "Qwen"
    thinking_enabled: true
  research:
    model: "DeepSeek"
    thinking_enabled: true
  conclusion:
    model: "Qwen-Flash"
    thinking_enabled: false
  title_generation:
    model: "Qwen-Flash"
    thinking_enabled: false
"""
        _write_config(config_dir, models_yaml=_VALID_MODELS_YAML, usage_yaml=bad_usage)
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_ambiguous_model_ref_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模型名在多个 provider 中存在且未消歧时，触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Qwen 同时在 deepseek 和 openai 两个 provider 中
        ambiguous_usage = """
scenarios:
  data_extraction:
    model: "DeepSeek"
    thinking_enabled: false
  assistant:
    model: "Qwen"
    thinking_enabled: true
  research:
    model: "DeepSeek"
    thinking_enabled: true
  conclusion:
    model: "Qwen-Flash"
    thinking_enabled: false
  title_generation:
    model: "Qwen-Flash"
    thinking_enabled: false
"""
        _write_config(
            config_dir,
            models_yaml=_VALID_MODELS_YAML,
            usage_yaml=ambiguous_usage,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()

    def test_disambiguated_model_ref_resolves_correctly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """provider/model 消歧格式正确解析到指定 provider。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # 使用 deepseek/Qwen 消歧
        _write_config(
            config_dir,
            models_yaml=_VALID_MODELS_YAML,
            usage_yaml=_VALID_AI_USAGE_YAML,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        load_config()
        config = get_scenario_config("assistant")

        # 应解析到 deepseek provider，而非 openai
        assert config.provider_name == "deepseek"
        assert config.model == "Qwen"
        assert config.base_url == "http://10.1.2.1:18881/v1"


# ---------------------------------------------------------------------------
# 5 场景齐全校验
# ---------------------------------------------------------------------------


class TestRequiredScenarios:
    """测试 5 个内置场景齐全校验。"""

    def test_all_five_scenarios_present(self, _valid_config_dir: Path) -> None:
        """验证所有必需场景都被返回。"""
        load_config()

        for scenario_name in (
            "data_extraction",
            "assistant",
            "research",
            "conclusion",
            "title_generation",
        ):
            config = get_scenario_config(scenario_name)
            assert isinstance(config, ScenarioConfig)
            assert config.model  # model 非空
            assert isinstance(config.thinking_enabled, bool)

    def test_missing_scenario_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """缺少必需场景时，validate_ai_config() 触发 SystemExit。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        incomplete_usage = """
scenarios:
  data_extraction:
    model: "DeepSeek"
    thinking_enabled: false
  assistant:
    model: "Qwen"
    thinking_enabled: true
  research:
    model: "DeepSeek"
    thinking_enabled: true
  # 缺少 conclusion 和 title_generation
"""
        _write_config(
            config_dir,
            models_yaml=_VALID_MODELS_YAML,
            usage_yaml=incomplete_usage,
        )
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

        with pytest.raises(SystemExit):
            validate_ai_config()


# ---------------------------------------------------------------------------
# async_provider_wrapper
# ---------------------------------------------------------------------------


class TestAsyncProviderWrapper:
    """测试 async_provider_wrapper 适配函数。"""

    def test_returns_callable_with_model_name_key(self, _valid_config_dir: Path) -> None:
        """async_provider_wrapper 返回的 callable 返回包含 model_name 键的 dict。"""
        load_config()

        provider = async_provider_wrapper("assistant")
        assert callable(provider)

        result = asyncio.run(provider())

        assert isinstance(result, dict)
        assert "base_url" in result
        assert "api_key" in result
        assert "model_name" in result
        assert "thinking_enabled" in result
        # model_name（而非 model）键存在
        assert result["model_name"] == "Qwen"
        assert result["thinking_enabled"] is True
        assert result["base_url"] == "http://10.1.2.1:18881/v1"
        assert result["api_key"] == "test-api-key"

    def test_wrapper_auto_triggers_load(self, _valid_config_dir: Path) -> None:
        """未调用 load_config() 时，wrapper 内部自动触发加载。"""
        provider = async_provider_wrapper("data_extraction")
        result = asyncio.run(provider())

        assert result["model_name"] == "DeepSeek"
        assert result["thinking_enabled"] is False

    def test_wrapper_for_unknown_scenario_raises_key_error(self, _valid_config_dir: Path) -> None:
        """未知场景名时，wrapper 内部抛出 KeyError。"""
        load_config()
        provider = async_provider_wrapper("nonexistent_scenario")

        with pytest.raises(KeyError):
            asyncio.run(provider())


# ---------------------------------------------------------------------------
# api_key ${VAR} 占位符展开
# ---------------------------------------------------------------------------


class TestApiKeyPlaceholder:
    """测试 api_key 的 ${VAR} 环境变量占位符展开。"""

    _PLACEHOLDER_MODELS = """
providers:
  - name: hcrdi
    base_url: "http://10.1.2.1:18881/v1"
    api_key: "${IRIP_LLM_HCRDI_API_KEY}"
    models:
      - "DeepSeek"
  - name: deepseek
    base_url: "http://10.1.2.1:18881/v1"
    api_key: "plaintext-key-for-test"
    models:
      - "GLM"
"""

    _PLACEHOLDER_USAGE = """
scenarios:
  data_extraction:
    model: "DeepSeek"
    thinking_enabled: false
  assistant:
    model: "hcrdi/DeepSeek"
    thinking_enabled: true
  research:
    model: "GLM"
    thinking_enabled: true
  conclusion:
    model: "GLM"
    thinking_enabled: false
  title_generation:
    model: "GLM"
    thinking_enabled: false
"""

    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入含占位符的配置并设置 IRIP_CONFIG_DIR。"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        _write_config(config_dir, self._PLACEHOLDER_MODELS, self._PLACEHOLDER_USAGE)
        monkeypatch.setenv("IRIP_CONFIG_DIR", str(config_dir))

    def test_placeholder_expands_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """${VAR} 占位符从环境变量正确展开为真实密钥。"""
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("IRIP_LLM_HCRDI_API_KEY", "sk-resolved-secret")

        load_config()
        config = get_scenario_config("data_extraction")

        assert config.provider_name == "hcrdi"
        assert config.api_key == "sk-resolved-secret"

    def test_placeholder_missing_env_fails_fast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量未设置时 fail-fast，错误信息含变量名。"""
        self._setup(tmp_path, monkeypatch)
        monkeypatch.delenv("IRIP_LLM_HCRDI_API_KEY", raising=False)

        with pytest.raises(ValueError) as excinfo:
            load_config()

        assert "IRIP_LLM_HCRDI_API_KEY" in str(excinfo.value)

    def test_placeholder_empty_env_fails_fast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量为空字符串时 fail-fast，错误信息含变量名。"""
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("IRIP_LLM_HCRDI_API_KEY", "")

        with pytest.raises(ValueError) as excinfo:
            load_config()

        assert "IRIP_LLM_HCRDI_API_KEY" in str(excinfo.value)

    def test_placeholder_error_does_not_leak_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """错误信息绝不包含密钥值（仅变量名）。"""
        self._setup(tmp_path, monkeypatch)
        monkeypatch.delenv("IRIP_LLM_HCRDI_API_KEY", raising=False)

        with pytest.raises(ValueError) as excinfo:
            load_config()

        msg = str(excinfo.value)
        assert "IRIP_LLM_HCRDI_API_KEY" in msg
        assert "sk-" not in msg

    def test_plaintext_api_key_still_parses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非占位符明文 api_key 仍可解析（向后兼容）。"""
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("IRIP_LLM_HCRDI_API_KEY", "sk-resolved-secret")

        load_config()
        config = get_scenario_config("research")

        assert config.provider_name == "deepseek"
        assert config.api_key == "plaintext-key-for-test"


# ---------------------------------------------------------------------------
# _find_provider_for_model
# ---------------------------------------------------------------------------


class TestFindProviderForModel:
    """测试 _find_provider_for_model 模型解析。"""

    def test_simple_model_name_resolution(self, _valid_config_dir: Path) -> None:
        """无歧义模型名正确解析到对应 provider。"""
        load_config()

        provider = _find_provider_for_model("DeepSeek")
        assert isinstance(provider, ProviderConfig)
        assert provider.name == "deepseek"

    def test_provider_model_disambiguation(self, _valid_config_dir: Path) -> None:
        """provider/model 格式正确解析到指定 provider。"""
        load_config()

        # Qwen 在 deepseek 和 openai 两个 provider 中都有
        provider = _find_provider_for_model("openai/Qwen")
        assert provider.name == "openai"
        assert "Qwen" in provider.models

        provider = _find_provider_for_model("deepseek/Qwen")
        assert provider.name == "deepseek"
        assert "Qwen" in provider.models

    def test_ambiguous_model_raises_value_error(self, _valid_config_dir: Path) -> None:
        """有歧义的模型名（多 provider 包含）抛出 ValueError。"""
        load_config()

        # Qwen 在 deepseek 和 openai 两个 provider 中
        with pytest.raises(ValueError, match="歧义|歧|ambiguous|multiple"):
            _find_provider_for_model("Qwen")

    def test_nonexistent_model_raises_value_error(self, _valid_config_dir: Path) -> None:
        """不存在的模型名抛出 ValueError。"""
        load_config()

        with pytest.raises(ValueError, match="未找到|not found"):
            _find_provider_for_model("NonExistent")

    def test_nonexistent_provider_in_disambig_format_raises_value_error(
        self, _valid_config_dir: Path
    ) -> None:
        """provider/model 格式中 provider 不存在时抛出 ValueError。"""
        load_config()

        with pytest.raises(ValueError, match="未找到|not found"):
            _find_provider_for_model("nonexistent_provider/DeepSeek")


# ---------------------------------------------------------------------------
# list_available_models
# ---------------------------------------------------------------------------


class TestListAvailableModels:
    """测试 list_available_models。"""

    def test_returns_all_models_with_provider_prefix(self, _valid_config_dir: Path) -> None:
        """返回所有模型名（含 provider 前缀）。"""
        load_config()

        models = list_available_models()
        assert isinstance(models, list)
        assert "deepseek/DeepSeek" in models
        assert "deepseek/Qwen" in models
        assert "openai/gpt-4" in models
        assert "openai/Qwen" in models

    def test_auto_triggers_load(self, _valid_config_dir: Path) -> None:
        """未调用 load_config() 时自动触发加载。"""
        models = list_available_models()
        assert len(models) > 0
        assert all("/" in m for m in models)


# ---------------------------------------------------------------------------
# ScenarioConfig frozen dataclass
# ---------------------------------------------------------------------------


class TestScenarioConfigFrozen:
    """测试 ScenarioConfig 是 frozen dataclass。"""

    def test_scenario_config_is_frozen(self, _valid_config_dir: Path) -> None:
        """ScenarioConfig 实例不可变。"""
        load_config()
        config = get_scenario_config("assistant")

        with pytest.raises(AttributeError):
            config.model = "Changed"  # type: ignore[misc]

    def test_scenario_config_equality(self, _valid_config_dir: Path) -> None:
        """相同配置的 ScenarioConfig 相等。"""
        load_config()
        config1 = get_scenario_config("assistant")
        _reset_cache()
        load_config()
        config2 = get_scenario_config("assistant")

        assert config1 == config2
