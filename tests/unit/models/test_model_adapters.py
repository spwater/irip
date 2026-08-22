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
    artifact_signature: str = "",
    signing_public_key: str = "",
    publisher: str = "",
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
        artifact_signature=artifact_signature,
        signing_public_key=signing_public_key,
        publisher=publisher,
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


def _build_onnx_add_model() -> bytes:
    """构建含未授权算子 Add 的最小 ONNX 模型（两输入相加）。

    用于算子白名单拒绝测试：Add 可用 allowed_op_types=frozenset({"Identity"})
    剔除。需要 onnx 包。

    Returns:
        bytes: 序列化的 ONNX 模型字节。
    """
    pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [None, 2])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [None, 2])
    z = helper.make_tensor_value_info("Z", TensorProto.FLOAT, [None, 2])
    node = helper.make_node("Add", ["X", "Y"], ["Z"])
    graph = helper.make_graph([node], "test_add", [x, y], [z])
    model = helper.make_model(graph)
    model.opset_import[0].version = 13
    return model.SerializeToString()


def _sign_artifact(artifact: bytes) -> tuple[str, str]:
    """用固定 Ed25519 私钥（bytes(range(32))）对工件签名，返回 (signature_hex, public_key_hex)。

    固定私钥保证测试可复现（与 crypto.py 测试环境固定密钥同思路）。

    Returns:
        tuple[str, str]: (64 字节签名的 hex, 32 字节公钥的 hex)。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signature = private_key.sign(artifact)
    public_key = private_key.public_key()
    public_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return signature.hex(), public_raw.hex()


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
        contract = _make_contract(executor={"type": "onnx", "timeout_seconds": 60})
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
        contract = _make_contract(executor={"type": "cli", "command": ["python", "predict.py"]})
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
        result = adapter.validate_input({"x": "not-a-number"}, _make_contract(input_schema=schema))
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


class TestOnnxModelAdapterSecurity:
    """OnnxModelAdapter 三层安全校验（B4）：签名 / 发布者白名单 / 算子白名单。

    均通过 enforce_security=True 开启；生产入口 build_adapter 默认开启。
    直接构造 OnnxModelAdapter() 时默认 False（遗留/可信部署路径，向后兼容），
    因此本类测试显式传 enforce_security=True 以验证 fail-closed 语义。
    """

    async def test_signature_valid_passes(self) -> None:
        """签名、发布者、算子三者均合法时加载成功。"""
        pytest.importorskip("onnxruntime")
        artifact = _build_onnx_model()
        sig_hex, pub_hex = _sign_artifact(artifact)
        contract = _make_contract(
            executor={"type": "onnx"},
            artifact_signature=sig_hex,
            signing_public_key=pub_hex,
            publisher="org:trusted",
        )
        adapter = OnnxModelAdapter(
            enforce_security=True,
            allowed_publishers=frozenset({"org:trusted"}),
        )
        loaded = await adapter.load(artifact, contract)
        assert loaded.metadata["adapter_type"] == "onnx"

    async def test_signature_missing_rejected_fail_closed(self) -> None:
        """enforce_security=True 且缺签名/公钥时拒绝加载。"""
        artifact = _build_onnx_model()
        adapter = OnnxModelAdapter(
            enforce_security=True,
            allowed_publishers=frozenset({"org:trusted"}),
        )
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                artifact,
                _make_contract(executor={"type": "onnx"}, publisher="org:trusted"),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_signature_mismatch_rejected(self) -> None:
        """签名与工件不匹配（篡改）时拒绝加载。"""
        artifact = _build_onnx_model()
        sig_hex, pub_hex = _sign_artifact(artifact)
        tampered = artifact + b"tampered"
        adapter = OnnxModelAdapter(
            enforce_security=True,
            allowed_publishers=frozenset({"org:trusted"}),
        )
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                tampered,
                _make_contract(
                    executor={"type": "onnx"},
                    artifact_signature=sig_hex,
                    signing_public_key=pub_hex,
                    publisher="org:trusted",
                ),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_publisher_not_in_allowlist_rejected(self) -> None:
        """发布者不在白名单内时拒绝加载。"""
        artifact = _build_onnx_model()
        sig_hex, pub_hex = _sign_artifact(artifact)
        adapter = OnnxModelAdapter(
            enforce_security=True,
            allowed_publishers=frozenset({"org:trusted"}),
        )
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                artifact,
                _make_contract(
                    executor={"type": "onnx"},
                    artifact_signature=sig_hex,
                    signing_public_key=pub_hex,
                    publisher="org:evil",
                ),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_publisher_missing_rejected(self) -> None:
        """契约未声明发布者时拒绝加载（fail-closed）。"""
        artifact = _build_onnx_model()
        sig_hex, pub_hex = _sign_artifact(artifact)
        adapter = OnnxModelAdapter(
            enforce_security=True,
            allowed_publishers=frozenset({"org:trusted"}),
        )
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                artifact,
                _make_contract(
                    executor={"type": "onnx"},
                    artifact_signature=sig_hex,
                    signing_public_key=pub_hex,
                ),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    async def test_opset_not_in_allowlist_rejected(self) -> None:
        """图中含未授权算子（Add 不在 {Identity} 白名单）时拒绝加载。"""
        artifact = _build_onnx_add_model()
        sig_hex, pub_hex = _sign_artifact(artifact)
        adapter = OnnxModelAdapter(
            enforce_security=True,
            allowed_publishers=frozenset({"org:trusted"}),
            allowed_op_types=frozenset({"Identity"}),
        )
        with pytest.raises(AppError) as exc_info:
            await adapter.load(
                artifact,
                _make_contract(
                    executor={"type": "onnx"},
                    artifact_signature=sig_hex,
                    signing_public_key=pub_hex,
                    publisher="org:trusted",
                ),
            )
        assert exc_info.value.code == "invalid_model_artifact"

    def test_build_adapter_defaults_enforce_security_on(self) -> None:
        """生产入口 build_adapter 默认 enforce_security=True（fail-closed）。"""
        contract = _make_contract(
            executor={"type": "onnx"},
        )
        adapter = build_adapter(contract)
        assert adapter._enforce_security is True

    def test_build_adapter_can_disable_enforce_security(self) -> None:
        """executor.enforce_security=false 可显式关闭（仅限可信内部部署）。"""
        contract = _make_contract(
            executor={"type": "onnx", "enforce_security": False},
        )
        adapter = build_adapter(contract)
        assert adapter._enforce_security is False
