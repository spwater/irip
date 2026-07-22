"""模型适配器契约测试（IRIP V2-T04）。

验证：
- 命令行适配器（CommandModelAdapter）加载/验证/预测；
- 超时处理；
- 输出大小限制；
- 无效输入拒绝；
- Python 适配器（PythonModelAdapter）加载/验证/预测（需 sklearn）。

无数据库依赖（纯适配器逻辑测试）。
"""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from packages.common.errors import AppError
from packages.models.adapters import (
    CommandModelAdapter,
    PythonModelAdapter,
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
        script_path = _write_cli_script(
            tmp_path, "predict.py", _CLI_NORMAL_SCRIPT
        )
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
        script_path = _write_cli_script(
            tmp_path, "slow.py", _CLI_SLEEP_SCRIPT
        )
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
        script_path = _write_cli_script(
            tmp_path, "huge.py", _CLI_HUGE_OUTPUT_SCRIPT
        )
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
        script_path = _write_cli_script(
            tmp_path, "predict.py", _CLI_NORMAL_SCRIPT
        )
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
        contract = _make_contract()
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
    """适配器工厂测试。"""

    def test_build_cli_adapter(self) -> None:
        """根据 executor.type=cli 构建 CommandModelAdapter。"""
        contract = _make_contract(
            executor={
                "type": "cli",
                "command": ["python", "predict.py"],
                "timeout_seconds": 60,
            }
        )
        adapter = build_adapter(contract)
        assert isinstance(adapter, CommandModelAdapter)

    def test_build_python_adapter(self) -> None:
        """根据 executor.type=python 构建 PythonModelAdapter。"""
        contract = _make_contract(
            executor={"type": "python", "timeout_seconds": 60}
        )
        adapter = build_adapter(contract)
        assert isinstance(adapter, PythonModelAdapter)

    def test_build_default_python(self) -> None:
        """未声明 executor 时默认构建 PythonModelAdapter。"""
        data = dict(_TEST_CONTRACT_DICT)
        data.pop("executor", None)
        contract = ModelContract.from_dict(data)
        adapter = build_adapter(contract)
        assert isinstance(adapter, PythonModelAdapter)


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


@pytest.mark.skipif(
    True,
    reason="sklearn 适配器测试在 TestPythonModelAdapter 中按需运行",
)
class TestPythonModelAdapterSklearn:
    """Python 适配器 sklearn 测试占位。"""

    pass


_SKLEARN_AVAILABLE: bool = True
try:
    import sklearn  # noqa: F401
    import joblib  # noqa: F401
except ImportError:
    _SKLEARN_AVAILABLE = False


@pytest.mark.skipif(
    not _SKLEARN_AVAILABLE,
    reason="scikit-learn 未安装，跳过 Python 适配器 sklearn 测试",
)
class TestPythonModelAdapter:
    """Python 进程内适配器测试（需 sklearn）。"""

    @pytest.mark.asyncio
    async def test_load_validate_predict(self, tmp_path: Path) -> None:
        """Python 适配器加载 sklearn 模型并预测。"""
        import io

        import joblib
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        # 训练一个微型多输出模型
        import numpy as np

        rng = np.random.RandomState(42)
        x = rng.uniform(0, 100, size=(50, 2))
        y = np.column_stack([x[:, 0] + x[:, 1], x[:, 0] * x[:, 1] * 0.01])
        pipeline = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                (
                    "rf",
                    RandomForestRegressor(
                        n_estimators=10,
                        random_state=42,
                        n_jobs=1,
                    ),
                ),
            ]
        )
        pipeline.fit(x, y)

        buf = io.BytesIO()
        joblib.dump(pipeline, buf)
        artifact_bytes = buf.getvalue()

        contract = ModelContract.from_dict(_TEST_CONTRACT_DICT)
        adapter = PythonModelAdapter(timeout_seconds=30)

        loaded = await adapter.load(artifact_bytes, contract)
        assert loaded.metadata["adapter_type"] == "python"

        result = adapter.validate_input({"x": 10, "y": 20}, contract)
        assert result.valid is True

        output = await adapter.predict({"x": 10, "y": 20})
        assert "sum" in output.predictions
        assert "product" in output.predictions

    def test_healthcheck_not_loaded(self) -> None:
        """未加载时健康检查返回 healthy=False。"""
        adapter = PythonModelAdapter()
        status = adapter.healthcheck()
        assert status.healthy is False
