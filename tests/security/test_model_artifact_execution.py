"""安全测试：模型工件执行隔离（P0-DataIso-T7）。

验证主进程不会执行不可信模型代码：
- 生产源码不含 pickle.loads / joblib.load（禁止反序列化 RCE）；
- 生产源码不直接 import subprocess（避免通过契约传递任意命令）；
- build_adapter 默认拒绝（fail closed），仅允许 onnx；
- OnnxModelAdapter 拒绝哈希不匹配 / 无效字节 / 超大工件 / 未知输入。

这些测试构成安全回归门禁：任何引入 pickle/joblib 反序列化或
放宽 build_adapter 白名单的变更都会使本文件失败。
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from packages.common.errors import AppError
from packages.models.adapters import OnnxModelAdapter, build_adapter
from packages.models.contracts import ModelContract


def _build_onnx_bytes() -> bytes:
    """构建确定性最小 ONNX 模型（单输入 X[None,2] -> 单输出 Y[None,2] = X）。

    需要onnx 包；未安装时跳过依赖它的测试。

    Returns:
        bytes: 序列化的 ONNX 模型字节。
    """
    pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [None, 2])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [None, 2])
    node = helper.make_node("Identity", ["X"], ["Y"])
    graph = helper.make_graph([node], "sec_identity", [x], [y])
    model = helper.make_model(graph)
    model.opset_import[0].version = 13
    return model.SerializeToString()


def _make_contract(
    executor: dict[str, Any] | None = None,
    artifact_sha256: str = "",
) -> ModelContract:
    """构建测试模型契约。

    Args:
        executor: executor 规格（默认空，触发 fail closed）。
        artifact_sha256: 工件 SHA-256（默认空，跳过校验）。

    Returns:
        ModelContract: 测试契约值对象。
    """
    return ModelContract(
        name="sec_model",
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["x", "y"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        applicability_domain={},
        executor=executor or {},
        artifact_sha256=artifact_sha256,
    )


# ============================================================
# 源码安全扫描（不依赖 onnx）
# ============================================================


def test_source_has_no_pickle_or_joblib_load() -> None:
    """adapters.py 不得包含 pickle.loads / joblib.load（反序列化 RCE 向量）。"""
    source = Path("packages/models/adapters.py").read_text(encoding="utf-8")
    assert "pickle.loads" not in source
    assert "joblib.load" not in source


def test_source_has_no_subprocess_contract() -> None:
    """adapters.py 不得直接 import subprocess（避免通过契约传递任意命令）。

    CommandModelAdapter 使用 asyncio.subprocess（标准库子模块），
    但不得出现 ``import subprocess`` 顶层导入。
    """
    source = Path("packages/models/adapters.py").read_text(encoding="utf-8")
    assert "subprocess" not in source or "import subprocess" not in source


# ============================================================
# build_adapter fail closed
# ============================================================


@pytest.mark.parametrize("executor_type", ["python", "cli"])
def test_build_adapter_rejects_code_executing_formats(executor_type: str) -> None:
    """python / cli 类型一律抛 unsafe_model_format。"""
    contract = _make_contract(executor={"type": executor_type})
    with pytest.raises(AppError) as exc:
        build_adapter(contract)
    assert exc.value.code == "unsafe_model_format"


def test_build_adapter_rejects_missing_executor_type() -> None:
    """缺失 executor type 时拒绝（fail closed）。"""
    contract = _make_contract(executor={})
    with pytest.raises(AppError) as exc:
        build_adapter(contract)
    assert exc.value.code == "unsafe_model_format"


def test_build_adapter_rejects_unknown_executor_type() -> None:
    """未知 executor type 拒绝。"""
    contract = _make_contract(executor={"type": "torchscript"})
    with pytest.raises(AppError) as exc:
        build_adapter(contract)
    assert exc.value.code == "unsafe_model_format"


def test_build_adapter_accepts_onnx() -> None:
    """仅 onnx 类型构建 OnnxModelAdapter。"""
    contract = _make_contract(executor={"type": "onnx"})
    adapter = build_adapter(contract)
    assert isinstance(adapter, OnnxModelAdapter)


# ============================================================
# OnnxModelAdapter 恶意工件与资源限制
# ============================================================


async def test_onnx_load_rejects_hash_mismatch() -> None:
    """哈希不匹配（篡改工件）抛 invalid_model_artifact。"""
    pytest.importorskip("onnxruntime")
    artifact = _build_onnx_bytes()
    contract = _make_contract(
        executor={"type": "onnx"},
        artifact_sha256="0" * 64,
    )
    adapter = OnnxModelAdapter()
    with pytest.raises(AppError) as exc:
        await adapter.load(artifact, contract)
    assert exc.value.code == "invalid_model_artifact"


async def test_onnx_load_rejects_invalid_bytes() -> None:
    """无效 ONNX 字节抛 invalid_model_artifact，消息不泄露内部信息。"""
    pytest.importorskip("onnxruntime")
    contract = _make_contract(executor={"type": "onnx"})
    adapter = OnnxModelAdapter()
    with pytest.raises(AppError) as exc:
        await adapter.load(b"not a valid onnx model bytes !!!", contract)
    assert exc.value.code == "invalid_model_artifact"
    assert "onnxruntime" not in exc.value.message
    assert "Traceback" not in exc.value.message


async def test_onnx_load_rejects_oversized_artifact() -> None:
    """超大工件（超过大小上限）抛 invalid_model_artifact（资源限制）。"""
    pytest.importorskip("onnxruntime")
    artifact = _build_onnx_bytes()
    contract = _make_contract(executor={"type": "onnx"})
    adapter = OnnxModelAdapter(max_artifact_bytes=len(artifact) - 1)
    with pytest.raises(AppError) as exc:
        await adapter.load(artifact, contract)
    assert exc.value.code == "invalid_model_artifact"


async def test_onnx_predict_unknown_input_raises() -> None:
    """未知/缺失输入抛 model_failed，消息不泄露内部信息。"""
    pytest.importorskip("onnxruntime")
    artifact = _build_onnx_bytes()
    contract = _make_contract(executor={"type": "onnx"})
    adapter = OnnxModelAdapter()
    await adapter.load(artifact, contract)
    # 缺少 y 维度
    with pytest.raises(AppError) as exc:
        await adapter.predict({"x": 1.0})
    assert exc.value.code == "model_failed"
    assert "KeyError" not in exc.value.message


async def test_onnx_load_with_correct_sha256_succeeds() -> None:
    """正确 SHA-256 校验通过，加载成功。"""
    pytest.importorskip("onnxruntime")
    artifact = _build_onnx_bytes()
    correct_sha = hashlib.sha256(artifact).hexdigest()
    contract = _make_contract(
        executor={"type": "onnx"},
        artifact_sha256=correct_sha,
    )
    adapter = OnnxModelAdapter()
    loaded = await adapter.load(artifact, contract)
    assert loaded.metadata["adapter_type"] == "onnx"
