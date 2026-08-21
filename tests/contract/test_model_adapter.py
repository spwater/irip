"""模型适配器契约测试（IRIP V2-T04）。

验证：
- 命令行适配器（CommandModelAdapter）加载/验证/预测；
- 超时处理；
- 输出大小限制；
- 无效输入拒绝；
- ONNX 适配器（OnnxModelAdapter）加载/验证/预测（需 onnxruntime）。

无数据库依赖（纯适配器逻辑测试）。
"""

import sys
import textwrap
from pathlib import Path

import pytest

from packages.common.errors import AppError
from packages.models.adapters import (
    CommandModelAdapter,
    OnnxModelAdapter,
    build_adapter,
)
from packages.models.applicability import ApplicabilityChecker
from packages.models.contracts import ModelContract

#: 测试用输入/输出契约。
_TEST_CONTRACT_DICT: dict = {
    "name": "test_model",
    "version": "1.0.0",
    "input_schema": {
        "type": "object",
        "required": ["x", "y"],
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "additionalProperties": False,
    },
    "output_schema": {
        "type": "object",
        "required": ["sum", "product"],
        "properties": {
            "sum": {"type": "number"},
            "product": {"type": "number"},
        },
        "additionalProperties": False,
    },
    "applicability_domain": {
        "x": {"min": 0.0, "max": 100.0},
        "y": {"min": 0.0, "max": 100.0},
    },
    "executor": {
        "type": "cli",
        "command": ["python"],
        "timeout_seconds": 10,
    },
}


def _make_contract(
    executor: dict | None = None,
) -> ModelContract:
    """构建测试契约。

    Args:
        executor: 可选的 executor 规格，默认使用测试 CLI 契约。

    Returns:
        ModelContract: 测试契约值对象。
    """
    data = dict(_TEST_CONTRACT_DICT)
    if executor is not None:
        data["executor"] = executor
    return ModelContract.from_dict(data)


def _write_cli_script(
    tmp_path: Path,
    script_name: str,
    body: str,
) -> Path:
    """写入 CLI 辅助脚本并返回路径。

    Args:
        tmp_path: 临时目录。
        script_name: 脚本文件名。
        body: 脚本内容。

    Returns:
        Path: 脚本文件路径。
    """
    script_path = tmp_path / script_name
    script_path.write_text(body, encoding="utf-8")
    return script_path


#: 正常 CLI 脚本：读取 input.json，计算 sum 和 product，写入 output.json。
_CLI_NORMAL_SCRIPT: str = textwrap.dedent(
    """\
    import json
    import sys

    workdir = sys.argv[1]
    input_path = sys.argv[2]
    output_path = sys.argv[3]

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)
    inputs = data["inputs"]
    x = inputs["x"]
    y = inputs["y"]
    output = {
        "predictions": {"sum": x + y, "product": x * y},
        "metadata": {"adapter": "cli_test"},
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f)
    """
)


#: 超时 CLI 脚本：sleep 30s（超过超时阈值）。
_CLI_SLEEP_SCRIPT: str = textwrap.dedent(
    """\
    import sys
    import time
    time.sleep(30)
    """
)


#: 巨大输出 CLI 脚本：写入超过 max_output_bytes 的 output.json。
_CLI_HUGE_OUTPUT_SCRIPT: str = textwrap.dedent(
    """\
    import sys
    workdir = sys.argv[1]
    input_path = sys.argv[2]
    output_path = sys.argv[3]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("x" * (5 * 1024 * 1024))
    """
)


class TestCommandModelAdapter:
    """命令行模型适配器测试。"""

    @pytest.mark.asyncio
    async def test_load_validate_predict(self, tmp_path: Path) -> None:
        """命令行适配器加载、校验、预测正常流程。"""
        script_path = _write_cli_script(tmp_path, "predict.py", _CLI_NORMAL_SCRIPT)
        contract = _make_contract(
            executor={
                "type": "cli",
                "command": [sys.executable, str(script_path)],
                "timeout_seconds": 10,
            }
        )
        adapter = CommandModelAdapter(
            command=(sys.executable, str(script_path)),
            timeout_seconds=10,
        )

        # 加载
        loaded = await adapter.load(b"fake-artifact", contract)
        assert loaded.metadata["adapter_type"] == "cli"

        # 校验输入
        result = adapter.validate_input({"x": 3, "y": 4}, contract)
        assert result.valid is True

        # 预测
        output = await adapter.predict({"x": 3, "y": 4})
        assert output.predictions["sum"] == 7
        assert output.predictions["product"] == 12
        assert output.metadata["adapter"] == "cli_test"

    @pytest.mark.asyncio
    async def test_timeout_handling(self, tmp_path: Path) -> None:
        """超时处理：执行超时发 SIGTERM 并抛 AppError。"""
        script_path = _write_cli_script(tmp_path, "slow.py", _CLI_SLEEP_SCRIPT)
        contract = _make_contract(
            executor={
                "type": "cli",
                "command": [sys.executable, str(script_path)],
                "timeout_seconds": 1,
            }
        )
        adapter = CommandModelAdapter(
            command=(sys.executable, str(script_path)),
            timeout_seconds=1,
        )
        await adapter.load(b"", contract)

        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1, "y": 1})
        assert exc_info.value.code == "model_timeout"

    @pytest.mark.asyncio
    async def test_output_size_limit(self, tmp_path: Path) -> None:
        """输出大小限制：输出超限抛 AppError。"""
        script_path = _write_cli_script(tmp_path, "huge.py", _CLI_HUGE_OUTPUT_SCRIPT)
        contract = _make_contract(
            executor={
                "type": "cli",
                "command": [sys.executable, str(script_path)],
                "timeout_seconds": 10,
            }
        )
        adapter = CommandModelAdapter(
            command=(sys.executable, str(script_path)),
            timeout_seconds=10,
            max_output_bytes=1024,
        )
        await adapter.load(b"", contract)

        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1, "y": 1})
        assert exc_info.value.code == "invalid_output"

    @pytest.mark.asyncio
    async def test_invalid_input_rejected(self, tmp_path: Path) -> None:
        """无效输入拒绝：validate_input 对缺失字段返回 valid=False。"""
        script_path = _write_cli_script(tmp_path, "predict.py", _CLI_NORMAL_SCRIPT)
        contract = _make_contract(
            executor={
                "type": "cli",
                "command": [sys.executable, str(script_path)],
                "timeout_seconds": 10,
            }
        )
        adapter = CommandModelAdapter(
            command=(sys.executable, str(script_path)),
            timeout_seconds=10,
        )

        # 缺少 y 字段
        result = adapter.validate_input({"x": 3}, contract)
        assert result.valid is False
        assert any("input_validation_failed" in e for e in result.errors)

        # 类型错误（x 为字符串）
        result2 = adapter.validate_input({"x": "a", "y": 4}, contract)
        assert result2.valid is False

    @pytest.mark.asyncio
    async def test_predict_without_load_raises(self) -> None:
        """未加载即预测抛 AppError。"""
        _make_contract()
        adapter = CommandModelAdapter(
            command=("python", "predict.py"),
            timeout_seconds=10,
        )
        with pytest.raises(AppError) as exc_info:
            await adapter.predict({"x": 1, "y": 1})
        assert exc_info.value.code == "model_not_loaded"

    def test_healthcheck_executable_found(self) -> None:
        """健康检查：Python 解释器可执行时返回 healthy=True。"""
        adapter = CommandModelAdapter(
            command=(sys.executable, "predict.py"),
            timeout_seconds=10,
        )
        status = adapter.healthcheck()
        assert status.healthy is True

    def test_healthcheck_executable_not_found(self) -> None:
        """健康检查：不存在的命令返回 healthy=False。"""
        adapter = CommandModelAdapter(
            command=("/nonexistent/binary_xyz", "predict.py"),
            timeout_seconds=10,
        )
        status = adapter.healthcheck()
        assert status.healthy is False


class TestBuildAdapter:
    """适配器工厂测试（fail closed）。"""

    def test_build_cli_adapter_rejected(self) -> None:
        """executor.type=cli 抛 unsafe_model_format（禁止主进程执行不可信命令）。"""
        contract = _make_contract(
            executor={
                "type": "cli",
                "command": ["python", "predict.py"],
                "timeout_seconds": 60,
            }
        )
        with pytest.raises(AppError) as exc_info:
            build_adapter(contract)
        assert exc_info.value.code == "unsafe_model_format"

    def test_build_python_adapter_rejected(self) -> None:
        """executor.type=python 抛 unsafe_model_format（禁止反序列化 RCE）。"""
        contract = _make_contract(executor={"type": "python", "timeout_seconds": 60})
        with pytest.raises(AppError) as exc_info:
            build_adapter(contract)
        assert exc_info.value.code == "unsafe_model_format"

    def test_build_default_rejected(self) -> None:
        """未声明 executor 时拒绝（fail closed）。"""
        data = dict(_TEST_CONTRACT_DICT)
        data.pop("executor", None)
        contract = ModelContract.from_dict(data)
        with pytest.raises(AppError) as exc_info:
            build_adapter(contract)
        assert exc_info.value.code == "unsafe_model_format"

    def test_build_onnx_adapter(self) -> None:
        """executor.type=onnx 构建 OnnxModelAdapter。"""
        contract = _make_contract(executor={"type": "onnx", "timeout_seconds": 60})
        adapter = build_adapter(contract)
        assert isinstance(adapter, OnnxModelAdapter)


class TestApplicabilityChecker:
    """适用域检查器测试。"""

    def test_within_domain_passes(self) -> None:
        """输入在适用域范围内通过。"""
        checker = ApplicabilityChecker()
        domain = {"x": {"min": 0.0, "max": 100.0}, "y": {"min": 0.0, "max": 50.0}}
        result = checker.check({"x": 50.0, "y": 25.0}, domain)
        assert result.valid is True
        assert result.errors == ()

    def test_outside_domain_rejected(self) -> None:
        """输入超出适用域被拒绝。"""
        checker = ApplicabilityChecker()
        domain = {"x": {"min": 0.0, "max": 100.0}, "y": {"min": 0.0, "max": 50.0}}
        result = checker.check({"x": 150.0, "y": 25.0}, domain)
        assert result.valid is False
        assert "outside_applicability_domain" in result.errors

    def test_missing_dimension_passes(self) -> None:
        """缺失维度不报错（由 input_schema 校验必填性）。"""
        checker = ApplicabilityChecker()
        domain = {"x": {"min": 0.0, "max": 100.0}}
        result = checker.check({}, domain)
        assert result.valid is True

    def test_boundary_values_pass(self) -> None:
        """边界值（等于 min/max）通过。"""
        checker = ApplicabilityChecker()
        domain = {"x": {"min": 0.0, "max": 100.0}}
        result_min = checker.check({"x": 0.0}, domain)
        result_max = checker.check({"x": 100.0}, domain)
        assert result_min.valid is True
        assert result_max.valid is True


class TestModelContract:
    """模型契约值对象测试。"""

    def test_contract_sha256_auto_computed(self) -> None:
        """契约未提供 sha256 时自动计算。"""
        contract = ModelContract.from_dict(_TEST_CONTRACT_DICT)
        assert len(contract.sha256) == 64
        assert contract.sha256 != ""

    def test_contract_sha256_deterministic(self) -> None:
        """相同内容产生相同 SHA-256。"""
        c1 = ModelContract.from_dict(_TEST_CONTRACT_DICT)
        c2 = ModelContract.from_dict(_TEST_CONTRACT_DICT)
        assert c1.sha256 == c2.sha256

    def test_contract_to_dict_roundtrip(self) -> None:
        """契约序列化/反序列化往返一致。"""
        contract = ModelContract.from_dict(_TEST_CONTRACT_DICT)
        data = contract.to_dict()
        contract2 = ModelContract.from_dict(data)
        assert contract2.name == contract.name
        assert contract2.version == contract.version
        assert contract2.sha256 == contract.sha256


_ONNX_AVAILABLE: bool = True
try:
    import onnx  # noqa: F401
    import onnxruntime  # noqa: F401
except ImportError:
    _ONNX_AVAILABLE = False


def _build_onnx_contract_model() -> bytes:
    """构建契约测试用 ONNX 模型（x,y 两输入 -> sum,product 两输出）。

    sum = x + y，product = x * y。需 onnx 包。

    Returns:
        bytes: 序列化的 ONNX 模型字节。
    """
    from onnx import TensorProto, helper

    x_in = helper.make_tensor_value_info("x", TensorProto.FLOAT, [None, 1])
    y_in = helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, 1])
    s_out = helper.make_tensor_value_info("sum", TensorProto.FLOAT, [None, 1])
    p_out = helper.make_tensor_value_info("product", TensorProto.FLOAT, [None, 1])
    add_node = helper.make_node("Add", ["x", "y"], ["sum"])
    mul_node = helper.make_node("Mul", ["x", "y"], ["product"])
    graph = helper.make_graph([add_node, mul_node], "contract_model", [x_in, y_in], [s_out, p_out])
    model = helper.make_model(graph)
    model.opset_import[0].version = 13
    return model.SerializeToString()


@pytest.mark.skipif(
    not _ONNX_AVAILABLE,
    reason="onnx/onnxruntime 未安装，跳过 ONNX 适配器契约测试",
)
class TestOnnxModelAdapter:
    """ONNX 进程内适配器契约测试（需 onnx/onnxruntime）。"""

    @pytest.mark.asyncio
    async def test_load_validate_predict(self) -> None:
        """ONNX 适配器加载模型并预测 sum/product。"""
        artifact_bytes = _build_onnx_contract_model()

        contract = _make_contract(executor={"type": "onnx", "timeout_seconds": 30})
        adapter = OnnxModelAdapter(timeout_seconds=30)

        loaded = await adapter.load(artifact_bytes, contract)
        assert loaded.metadata["adapter_type"] == "onnx"

        result = adapter.validate_input({"x": 10, "y": 20}, contract)
        assert result.valid is True

        output = await adapter.predict({"x": 10.0, "y": 20.0})
        assert "sum" in output.predictions
        assert "product" in output.predictions
        assert output.predictions["sum"] == pytest.approx(30.0)
        assert output.predictions["product"] == pytest.approx(200.0)

    def test_healthcheck_not_loaded(self) -> None:
        """未加载时健康检查返回 healthy=False。"""
        adapter = OnnxModelAdapter()
        status = adapter.healthcheck()
        assert status.healthy is False
        status = adapter.healthcheck()
        assert status.healthy is False
