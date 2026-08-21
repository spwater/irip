"""单元测试：模型适配器 CommandModelAdapter / OnnxModelAdapter / build_adapter。

覆盖：
- build_adapter：onnx 构建 OnnxModelAdapter；python / cli / 缺失 / 未知
  一律抛 unsafe_model_format（fail closed）；
- CommandModelAdapter：load 写入工件 + validate_input（空 schema / 合法 / 非法）
  + healthcheck（空命令 / 绝对路径 / PATH 命令 / 不存在）+ _build_safe_env 过滤；
- OnnxModelAdapter：load（有效 ONNX + SHA-256 校验失败 + 无效字节 + 超大工件）
  + validate_input + predict（按 schema 映射 / 未知输入拒绝）+ healthcheck；
- 源码安全扫描：生产代码不含 pickle.loads / joblib.load；
- CommandModelAdapter.predict：未加载抛错 / 命令不存在。
"""

from pathlib import Path
from typing import Any

import pytest

from packages.common.errors import AppError
from packages.models.adapters import (
    CommandModelAdapter,
    OnnxModelAdapter,
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
        artifact_sha256=artifact_sha256 or "",
    )


def _build_onnx_model() -> bytes:
    """构建一个确定性最小 ONNX 模型：单输入 X[None,2] -> 单输出 Y[None,2] = X（Identity）。

    用于 OnnxModelAdapter 单元测试。需要 onnx 包。

    Returns:
        bytes: 序列化的 ONNX 模型字节。
    """
    pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [None, 2])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [None, 2])
    node = helper.make_node("Identity", ["X"], ["Y"])
    graph = helper.make_graph([node], "test_identity", [x], [y])
    model = helper.make_model(graph)
    model.opset_import[0].version = 13
    return model.SerializeToString()


# ============================================================
# 源码安全扫描
# ============================================================


class TestSourceScan:
    """生产源码安全扫描：禁止反序列化 RCE 向量。"""

    def test_production_source_has_no_pickle_or_joblib_load(self) -> None:
        """adapters.py 不得包含 pickle.loads 或 joblib.load（反序列化 RCE 向量）。"""
        source = Path("packages/models/adapters.py").read_text(encoding="utf-8")
        assert "pickle.loads" not in source
        assert "joblib.load" not in source

    def test_production_source_has_no_import_subprocess(self) -> None:
        """adapters.py 不得直接 import subprocess（避免通过契约传递任意命令）。"""
        source = Path("packages/models/adapters.py").read_text(encoding="utf-8")
        assert "import subprocess" not in source


# ============================================================
# build_adapter
# ============================================================


class TestBuildAdapter:
    """build_adapter 工厂函数测试（fail closed）。"""

    def test_onnx_executor_builds_onnx_adapter(self) -> None:
        """executor.type=onnx 构建 OnnxModelAdapter。"""
        contract = _make_contract(
            executor={"type": "onnx", "timeout_seconds": 60}
        )
        adapter = build_adapter(contract)
        assert isinstance(adapter, OnnxModelAdapter)
        assert adapter._timeout_seconds == 60

    @pytest.mark.parametrize("executor_type", ["python", "cli"])
    def test_build_adapter_rejects_code_executing_formats(
        self,
        executor_type: str,
    ) -> None:
        """python / cli 类型抛 unsafe_model_format（禁止主进程执行不可信代码）。"""
        contract = _make_contract(executor={"type": executor_type})
        with pytest.raises(AppError) as exc:
            build_adapter(contract)
        assert exc.value.code == "unsafe_model_format"

    def test_no_executor_rejected(self) -> None:
        """无 executor 默认拒绝（fail closed）。"""
        contract = _make_contract()
        with pytest.raises(AppError) as exc:
            build_adapter(contract)
        assert exc.value.code == "unsafe_model_format"

    def test_empty_executor_dict_rejected(self) -> None:
        """空 executor dict 拒绝（fail closed）。"""
        contract = _make_contract(executor={})
        with pytest.raises(AppError) as exc:
            build_adapter(contract)
        assert exc.value.code == "unsafe_model_format"

    def test_unknown_executor_type_rejected(self) -> None:
        """未知 executor 类型拒绝。"""
        contract = _make_contract(executor={"type": "torchscript"})
        with pytest.raises(AppError) as exc:
            build_adapter(contract)
        assert exc.value.code == "unsafe_model_format"

    def test_cli_executor_no_longer_builds_command_adapter(self) -> None:
        """cli executor 不再构建 CommandModelAdapter（安全收敛）。"""
        contract = _make_contract(
            executor={"type": "cli", "command": ["python", "predict.py"]}
        )
        with pytest.raises(AppError) as exc:
            build_adapter(contract)
        assert exc.value.code == "unsafe_model_format"


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
# OnnxModelAdapter
# ============================================================


class TestOnnxModelAdapterLoad:
    """OnnxModelAdapter.load 测试。"""

    async def test_load_valid_onnx(self) -> None:
        """有效 ONNX 工件加载成功。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter()
        loaded = await adapter.load(artifact, _make_contract(executor={"type": "onnx"}))

        assert loaded.metadata["adapter_type"] == "onnx"
        assert loaded.metadata["artifact_size"] == len(artifact)
        assert adapter._session is not None
        assert adapter._contract is not None

    async def test_load_sha256_mismatch_raises(self) -> None:
        """SHA-256 校验不通过抛 invalid_model_artifact。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter()
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                artifact,
                _make_contract(executor={"type": "onnx"}, artifact_sha256="0" * 64),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_load_invalid_bytes_raises(self) -> None:
        """无效字节抛 invalid_model_artifact，且消息不含解析器内部信息。"""
        pytest.importorskip("onnxruntime")
        adapter = OnnxModelAdapter()
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                b"not-a-valid-onnx-model",
                _make_contract(executor={"type": "onnx"}),
            )
        assert exc_info.value.code == "invalid_model_artifact"
        assert "onnxruntime" not in exc_info.value.message
        assert "Traceback" not in exc_info.value.message

    async def test_load_oversized_artifact_raises(self) -> None:
        """工件超过大小上限抛 invalid_model_artifact。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter(max_artifact_bytes=len(artifact) - 1)
        with pytest.raises(AppError) as exc_info:
            await adapter.load(artifact, _make_contract(executor={"type": "onnx"}))
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_load_with_correct_sha256(self) -> None:
        """提供正确 SHA-256 时校验通过。"""
        import hashlib

        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        correct_sha = hashlib.sha256(artifact).hexdigest()
        adapter = OnnxModelAdapter()
        loaded = await adapter.load(
            artifact,
            _make_contract(executor={"type": "onnx"}, artifact_sha256=correct_sha),
        )
        assert loaded.metadata["adapter_type"] == "onnx"


class TestOnnxModelAdapterValidateInput:
    """OnnxModelAdapter.validate_input 测试。"""

    def test_empty_schema_valid(self) -> None:
        """空 schema 始终通过。"""
        adapter = OnnxModelAdapter()
        result = adapter.validate_input({}, _make_contract())
        assert result.valid is True

    def test_valid_input(self) -> None:
        """合法输入通过校验。"""
        schema = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}
        adapter = OnnxModelAdapter()
        result = adapter.validate_input({"x": 1}, _make_contract(input_schema=schema))
        assert result.valid is True

    def test_invalid_input(self) -> None:
        """非法输入返回错误。"""
        schema = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}
        adapter = OnnxModelAdapter()
        result = adapter.validate_input(
            {"x": "not-a-number"}, _make_contract(input_schema=schema)
        )
        assert result.valid is False


class TestOnnxModelAdapterPredict:
    """OnnxModelAdapter.predict 测试。"""

    async def test_predict_not_loaded_raises(self) -> None:
        """未加载时 predict 抛 model_not_loaded。"""
        adapter = OnnxModelAdapter()
        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1})
        assert exc_info.value.code == "model_not_loaded"

    async def test_predict_with_schema_mapping(self) -> None:
        """按 output_schema 映射预测结果（Identity 模型：输出 = 输入）。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter()
        input_schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
        }
        output_schema = {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        }
        await adapter.load(
            artifact,
            _make_contract(
                executor={"type": "onnx"},
                input_schema=input_schema,
                output_schema=output_schema,
            ),
        )

        output = await adapter.predict({"x": 3.0, "y": 4.0})
        assert isinstance(output, ModelOutput)
        assert output.predictions["a"] == 3.0
        assert output.predictions["b"] == 4.0
        assert output.metadata["adapter_type"] == "onnx"

    async def test_predict_unknown_input_raises(self) -> None:
        """缺失输入维度抛 model_failed。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter()
        input_schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
        }
        output_schema = {"type": "object", "properties": {"a": {"type": "number"}}}
        await adapter.load(
            artifact,
            _make_contract(
                executor={"type": "onnx"},
                input_schema=input_schema,
                output_schema=output_schema,
            ),
        )

        # 缺少 y 维度
        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 3.0})
        assert exc_info.value.code == "model_failed"
        # 消息不含底层异常文本
        assert "KeyError" not in exc_info.value.message


class TestOnnxModelAdapterHealthcheck:
    """OnnxModelAdapter.healthcheck 测试。"""

    def test_not_loaded_unhealthy(self) -> None:
        """未加载不健康。"""
        adapter = OnnxModelAdapter()
        status = adapter.healthcheck()
        assert status.healthy is False
        assert "模型未加载" in status.message

    async def test_loaded_healthy(self) -> None:
        """加载后健康。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter()
        await adapter.load(artifact, _make_contract(executor={"type": "onnx"}))
        status = adapter.healthcheck()
        assert status.healthy is True
        assert "ONNX" in status.message
