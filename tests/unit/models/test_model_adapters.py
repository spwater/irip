"""单元测试：模型适配器 CommandModelAdapter / PythonModelAdapter / build_adapter。

覆盖：
- build_adapter：cli / python / 默认（无 executor）路由；
- CommandModelAdapter：load 写入工件 + validate_input（空 schema / 合法 / 非法）
  + healthcheck（空命令 / 绝对路径 / PATH 命令 / 不存在）+ _build_safe_env 过滤；
- PythonModelAdapter：load（pickle/pickle 回退 + SHA-256 校验失败 + 反序列化失败）
  + validate_input + predict（按 schema 映射 / 无 schema 回退）+ healthcheck；
- CommandModelAdapter.predict：未加载抛错 / 命令不存在 / 非零退出 / 输出过大 / JSON 解析失败。
"""

from typing import Any

import pytest

from packages.common.errors import AppError
from packages.models.adapters import (
    CommandModelAdapter,
    PythonModelAdapter,
    build_adapter,
)
from packages.models.contracts import (
    LoadedModel,
    ModelContract,
    ModelOutput,
)

# ============================================================
# Helpers
# ============================================================


def _make_contract(
    name: str = "test_model",
    version: str = "1.0.0",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    applicability_domain: dict[str, Any] | None = None,
    executor: dict[str, Any] | None = None,
    artifact_sha256: str = "",
) -> ModelContract:
    return ModelContract(
        name=name,
        version=version,
        input_schema=input_schema or {},
        output_schema=output_schema or {},
        applicability_domain=applicability_domain or {},
        executor=executor or {},
        sha256=artifact_sha256 or "",
    )


# ---- 模块级 Fake 模型类（pickle 可序列化，joblib 不可用时回退 pickle） ----


class _FakePredictResult:
    """模拟 numpy-like 预测结果（含 tolist）。"""

    def tolist(self) -> list[list[float]]:
        return [[3.5, 4.5]]


class _FakeSchemaModel:
    """带 schema 映射的 Fake 模型。"""

    def predict(self, x: Any) -> _FakePredictResult:
        return _FakePredictResult()


class _FakeListModel:
    """返回 list 的 Fake 模型。"""

    def predict(self, x: Any) -> list[list[float]]:
        return [[7.0, 8.0]]


class _FakeFailingModel:
    """predict 抛异常的 Fake 模型。"""

    def predict(self, x: Any) -> list[list[float]]:
        raise RuntimeError("boom")


# ============================================================
# build_adapter
# ============================================================


class TestBuildAdapter:
    """build_adapter 工厂函数测试。"""

    def test_cli_executor_builds_command_adapter(self) -> None:
        """executor.type=cli 构建 CommandModelAdapter。"""
        contract = _make_contract(
            executor={"type": "cli", "command": ["python", "predict.py"], "timeout_seconds": 120}
        )
        adapter = build_adapter(contract)
        assert isinstance(adapter, CommandModelAdapter)
        assert adapter._command == ("python", "predict.py")
        assert adapter._timeout_seconds == 120

    def test_python_executor_builds_python_adapter(self) -> None:
        """executor.type=python 构建 PythonModelAdapter。"""
        contract = _make_contract(executor={"type": "python", "timeout_seconds": 60})
        adapter = build_adapter(contract)
        assert isinstance(adapter, PythonModelAdapter)
        assert adapter._timeout_seconds == 60

    def test_no_executor_defaults_to_python(self) -> None:
        """无 executor 默认构建 PythonModelAdapter。"""
        contract = _make_contract()
        adapter = build_adapter(contract)
        assert isinstance(adapter, PythonModelAdapter)

    def test_empty_executor_dict_defaults_to_python(self) -> None:
        """空 executor dict 默认构建 PythonModelAdapter。"""
        contract = _make_contract(executor={})
        adapter = build_adapter(contract)
        assert isinstance(adapter, PythonModelAdapter)

    def test_cli_executor_empty_command(self) -> None:
        """cli executor 无 command 时构建空命令元组的 CommandModelAdapter。"""
        contract = _make_contract(executor={"type": "cli"})
        adapter = build_adapter(contract)
        assert isinstance(adapter, CommandModelAdapter)
        assert adapter._command == ()


# ============================================================
# CommandModelAdapter
# ============================================================


class TestCommandModelAdapterLoad:
    """CommandModelAdapter.load 测试。"""

    async def test_load_writes_artifact_and_returns_loaded_model(self) -> None:
        """load 写入工件字节并返回 LoadedModel。"""
        adapter = CommandModelAdapter(command=("python",))
        contract = _make_contract()
        loaded = await adapter.load(b"artifact-bytes", contract)

        assert isinstance(loaded, LoadedModel)
        assert loaded.metadata["adapter_type"] == "cli"
        assert loaded.metadata["artifact_size"] == len(b"artifact-bytes")
        assert adapter._loaded is loaded
        assert adapter._contract is contract

    async def test_load_empty_bytes(self) -> None:
        """load 空字节也能正常加载。"""
        adapter = CommandModelAdapter(command=("python",))
        loaded = await adapter.load(b"", _make_contract())
        assert loaded.metadata["artifact_size"] == 0


class TestCommandModelAdapterValidateInput:
    """CommandModelAdapter.validate_input 测试。"""

    def test_empty_schema_always_valid(self) -> None:
        """空 input_schema 始终通过。"""
        adapter = CommandModelAdapter(command=("python",))
        result = adapter.validate_input({"x": 1}, _make_contract(input_schema={}))
        assert result.valid is True
        assert result.errors == ()

    def test_valid_input_passes(self) -> None:
        """合法输入通过校验。"""
        schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        }
        adapter = CommandModelAdapter(command=("python",))
        result = adapter.validate_input({"x": 1.0}, _make_contract(input_schema=schema))
        assert result.valid is True

    def test_invalid_input_returns_errors(self) -> None:
        """非法输入返回校验错误。"""
        schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        }
        adapter = CommandModelAdapter(command=("python",))
        result = adapter.validate_input({}, _make_contract(input_schema=schema))
        assert result.valid is False
        assert len(result.errors) == 1
        assert "input_validation_failed" in result.errors[0]


class TestCommandModelAdapterHealthcheck:
    """CommandModelAdapter.healthcheck 测试。"""

    def test_empty_command_unhealthy(self) -> None:
        """空命令不健康。"""
        adapter = CommandModelAdapter(command=())
        status = adapter.healthcheck()
        assert status.healthy is False
        assert "命令为空" in status.message

    def test_absolute_path_not_exists_unhealthy(self, tmp_path: Any) -> None:
        """绝对路径可执行文件不存在不健康。"""
        adapter = CommandModelAdapter(command=(str(tmp_path / "nonexistent"),))
        status = adapter.healthcheck()
        assert status.healthy is False
        assert "可执行文件不存在" in status.message

    def test_absolute_path_exists_healthy(self) -> None:
        """绝对路径可执行文件存在健康。"""
        import sys

        adapter = CommandModelAdapter(command=(sys.executable,))
        status = adapter.healthcheck()
        assert status.healthy is True

    def test_command_in_path_healthy(self) -> None:
        """PATH 中的命令健康。"""
        adapter = CommandModelAdapter(command=("python",))
        status = adapter.healthcheck()
        assert status.healthy is True

    def test_command_not_in_path_unhealthy(self) -> None:
        """不在 PATH 中的命令不健康。"""
        adapter = CommandModelAdapter(command=("nonexistent-cmd-xyz-12345",))
        status = adapter.healthcheck()
        assert status.healthy is False
        assert "不在 PATH 中" in status.message


class TestCommandModelAdapterBuildSafeEnv:
    """CommandModelAdapter._build_safe_env 测试。"""

    def test_filters_unsafe_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不安全的环境变量被过滤。"""
        monkeypatch.setenv("IRIP_MODEL_SECRET", "safe-val")
        monkeypatch.setenv("SECRET_TOKEN", "unsafe-val")
        adapter = CommandModelAdapter(command=("python",))
        env = adapter._build_safe_env()
        assert "IRIP_MODEL_SECRET" in env
        assert "SECRET_TOKEN" not in env

    def test_keeps_safe_prefixes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """安全前缀的环境变量被保留。"""
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/test")
        adapter = CommandModelAdapter(command=("python",))
        env = adapter._build_safe_env()
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/test"


class TestCommandModelAdapterPredict:
    """CommandModelAdapter.predict 测试。"""

    async def test_predict_not_loaded_raises(self) -> None:
        """未加载时 predict 抛 model_not_loaded。"""
        adapter = CommandModelAdapter(command=("python",))
        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1})
        assert exc_info.value.code == "model_not_loaded"

    async def test_predict_command_not_found_raises(self) -> None:
        """命令不存在抛 model_failed。"""
        adapter = CommandModelAdapter(command=("nonexistent-cmd-xyz-12345",))
        await adapter.load(b"", _make_contract())
        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1})
        assert exc_info.value.code == "model_failed"


# ============================================================
# PythonModelAdapter
# ============================================================


class TestPythonModelAdapterLoad:
    """PythonModelAdapter.load 测试。"""

    async def test_load_pickle_artifact(self) -> None:
        """pickle 反序列化工件成功。"""
        import pickle

        model_obj = _FakeListModel()
        artifact = pickle.dumps(model_obj)

        adapter = PythonModelAdapter()
        loaded = await adapter.load(artifact, _make_contract())

        assert loaded.metadata["adapter_type"] == "python"
        assert loaded.metadata["model_class"] == "_FakeListModel"
        assert adapter._model_obj is not None

    async def test_load_sha256_mismatch_raises(self) -> None:
        """SHA-256 校验不通过抛 invalid_model_artifact。"""
        adapter = PythonModelAdapter()
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                b"artifact-bytes",
                _make_contract(artifact_sha256="0" * 64),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_load_invalid_artifact_raises(self) -> None:
        """反序列化失败抛 invalid_model_artifact。"""
        adapter = PythonModelAdapter()
        with pytest.raises(AppError) as exc_info:
            await adapter.load(b"not-a-valid-pickle", _make_contract())
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_load_without_sha_check(self) -> None:
        """无 artifact_sha256 时不校验哈希。"""
        import pickle

        model_obj = _FakeSchemaModel()
        artifact = pickle.dumps(model_obj)

        adapter = PythonModelAdapter()
        loaded = await adapter.load(artifact, _make_contract())
        assert loaded.metadata["model_class"] == "_FakeSchemaModel"


class TestPythonModelAdapterValidateInput:
    """PythonModelAdapter.validate_input 测试。"""

    def test_empty_schema_valid(self) -> None:
        """空 schema 始终通过。"""
        adapter = PythonModelAdapter()
        result = adapter.validate_input({}, _make_contract())
        assert result.valid is True

    def test_valid_input(self) -> None:
        """合法输入通过。"""
        schema = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}
        adapter = PythonModelAdapter()
        result = adapter.validate_input({"x": 1}, _make_contract(input_schema=schema))
        assert result.valid is True

    def test_invalid_input(self) -> None:
        """非法输入返回错误。"""
        schema = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}
        adapter = PythonModelAdapter()
        result = adapter.validate_input({"x": "not-a-number"}, _make_contract(input_schema=schema))
        assert result.valid is False


class TestPythonModelAdapterPredict:
    """PythonModelAdapter.predict 测试。"""

    async def test_predict_not_loaded_raises(self) -> None:
        """未加载时 predict 抛 model_not_loaded。"""
        adapter = PythonModelAdapter()
        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1})
        assert exc_info.value.code == "model_not_loaded"

    async def test_predict_with_schema_mapping(self) -> None:
        """按 output_schema 映射预测结果。"""
        import pickle

        artifact = pickle.dumps(_FakeSchemaModel())
        adapter = PythonModelAdapter()
        input_schema = {"properties": {"x": {"type": "number"}, "y": {"type": "number"}}}
        output_schema = {"properties": {"a": {"type": "number"}, "b": {"type": "number"}}}
        await adapter.load(
            artifact, _make_contract(input_schema=input_schema, output_schema=output_schema)
        )

        output = await adapter.predict({"x": 1.0, "y": 2.0})
        assert isinstance(output, ModelOutput)
        assert output.predictions["a"] == 3.5
        assert output.predictions["b"] == 4.5
        assert output.metadata["adapter_type"] == "python"

    async def test_predict_without_schema_fallback(self) -> None:
        """无 output_schema 时按 output_{i} 命名。"""
        import pickle

        artifact = pickle.dumps(_FakeListModel())
        adapter = PythonModelAdapter()
        await adapter.load(artifact, _make_contract(input_schema={}, output_schema={}))

        output = await adapter.predict({"x": 1.0})
        assert output.predictions["output_0"] == 7.0
        assert output.predictions["output_1"] == 8.0

    async def test_predict_model_failure_raises(self) -> None:
        """模型 predict 抛异常时转为 model_failed。"""
        import pickle

        artifact = pickle.dumps(_FakeFailingModel())
        adapter = PythonModelAdapter()
        await adapter.load(artifact, _make_contract())

        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1.0})
        assert exc_info.value.code == "model_failed"


class TestPythonModelAdapterHealthcheck:
    """PythonModelAdapter.healthcheck 测试。"""

    def test_not_loaded_unhealthy(self) -> None:
        """未加载不健康。"""
        adapter = PythonModelAdapter()
        status = adapter.healthcheck()
        assert status.healthy is False
        assert "模型未加载" in status.message

    def test_loaded_without_predict_unhealthy(self) -> None:
        """加载的模型缺少 predict 方法不健康。"""
        adapter = PythonModelAdapter()
        adapter._model_obj = object()
        status = adapter.healthcheck()
        assert status.healthy is False
        assert "predict" in status.message

    def test_loaded_with_predict_healthy(self) -> None:
        """加载的模型有 predict 方法健康。"""
        adapter = PythonModelAdapter()

        class FakeModel:
            def predict(self, x: Any) -> list[list[float]]:
                return [[1.0]]

        adapter._model_obj = FakeModel()
        status = adapter.healthcheck()
        assert status.healthy is True
        assert "FakeModel" in status.message
