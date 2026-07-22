#!/usr/bin/env python3
"""IRIP 命令行模型适配器示例（V2-T05）。

这是一个**自包含**的命令行模型适配器，演示如何将一个简单线性回归
模型接入 IRIP 模型生命周期（V2-T04）。它支持两种调用模式：

1. **文件协议模式**（与 ``packages.models.adapters.CommandModelAdapter`` 对接）::

       python adapter.py <workdir> <input_path> <output_path>

   - ``workdir``      : 隔离工作目录（模型工件 ``model.artifact`` 已写入其中）
   - ``input_path``   : 输入 JSON 文件，内容为 ``{"inputs": {...}}``
   - ``output_path``  : 输出 JSON 文件路径，需写入 ``{"predictions": {...}, "metadata": {...}}``

2. **标准流模式**（便于独立调试 / 管道调用）::

       echo '{"inputs": {"x1": 1.0, "x2": 2.0}}' | python adapter.py

   - 从 stdin 读取 JSON（``{"inputs": {...}}`` 或裸输入字典）
   - 向 stdout 输出 JSON 结果

模型定义（线性回归）::

    y_j = bias_j + sum_i ( weight[j][i] * x_i )

权重可由工作目录中的 ``model.artifact``（JSON）提供；若不存在则使用
内置默认模型（2 输入 → 1 输出）。

依赖：仅使用 Python 标准库，无需 numpy / sklearn。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: 默认模型定义（2 输入 x1/x2 → 1 输出 y）。
_DEFAULT_MODEL: dict[str, Any] = {
    "inputs": ["x1", "x2"],
    "outputs": ["y"],
    "weights": {"y": {"x1": 2.5, "x2": 1.5}},
    "bias": {"y": 0.3},
}

#: 适配器元数据。
_ADAPTER_METADATA: dict[str, str] = {
    "model_type": "linear_regression",
    "adapter": "cli",
    "framework": "pure-python",
}


def load_model(workdir: Path | None) -> dict[str, Any]:
    """加载模型定义。

    优先从 ``workdir/model.artifact`` 读取 JSON 模型定义；若文件不存在
    或解析失败，则回退到内置默认模型。

    Args:
        workdir: 隔离工作目录（可能包含 ``model.artifact``）。

    Returns:
        模型定义字典，包含 ``inputs`` / ``outputs`` / ``weights`` / ``bias``。
    """
    if workdir is not None:
        artifact_path = workdir / "model.artifact"
        if artifact_path.exists():
            try:
                raw = artifact_path.read_text(encoding="utf-8")
                model = json.loads(raw)
                # 基本字段校验
                if (
                    isinstance(model, dict)
                    and "outputs" in model
                    and "weights" in model
                ):
                    return model
            except (json.JSONDecodeError, OSError):
                # 解析失败，回退默认模型
                pass
    return _DEFAULT_MODEL


def predict(
    model: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """执行线性回归预测。

    Args:
        model: 模型定义（含 outputs / weights / bias）。
        inputs: 输入参数字典，键为输入字段名，值为数值。

    Returns:
        预测结果字典 ``{output_name: value}``。

    Raises:
        ValueError: 当缺少必需输入字段或输入非数值时。
    """
    outputs: list[str] = list(model.get("outputs", []))
    weights: dict[str, Any] = model.get("weights", {})
    bias: dict[str, Any] = model.get("bias", {})

    predictions: dict[str, Any] = {}
    for out_name in outputs:
        out_weights: dict[str, float] = weights.get(out_name, {})
        out_bias: float = float(bias.get(out_name, 0.0))
        total = out_bias
        for in_name, coeff in out_weights.items():
            if in_name not in inputs:
                raise ValueError(f"缺少必需输入字段: {in_name}")
            raw_value = inputs[in_name]
            try:
                num_value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"输入字段 {in_name} 非数值: {raw_value!r}"
                ) from exc
            total += float(coeff) * num_value
        predictions[out_name] = round(total, 6)
    return predictions


def run_file_mode(workdir: Path, input_path: Path, output_path: Path) -> None:
    """文件协议模式：读取 input.json，写入 output.json。

    Args:
        workdir: 隔离工作目录。
        input_path: 输入 JSON 文件路径。
        output_path: 输出 JSON 文件路径。
    """
    model = load_model(workdir)

    input_data = json.loads(input_path.read_text(encoding="utf-8"))
    # 输入格式兼容 {"inputs": {...}} 与裸字典 {...}
    inputs: dict[str, Any] = (
        input_data.get("inputs", input_data)
        if isinstance(input_data, dict)
        else {}
    )

    predictions = predict(model, inputs)
    output = {"predictions": predictions, "metadata": dict(_ADAPTER_METADATA)}
    output_path.write_text(
        json.dumps(output, ensure_ascii=False),
        encoding="utf-8",
    )


def run_stdio_mode() -> None:
    """标准流模式：从 stdin 读取 JSON，向 stdout 输出 JSON。"""
    model = load_model(None)

    raw_stdin = sys.stdin.read()
    if not raw_stdin.strip():
        sys.stderr.write("错误: stdin 为空，请提供 JSON 输入\n")
        sys.exit(1)

    input_data = json.loads(raw_stdin)
    inputs: dict[str, Any] = (
        input_data.get("inputs", input_data)
        if isinstance(input_data, dict)
        else {}
    )

    predictions = predict(model, inputs)
    output = {"predictions": predictions, "metadata": dict(_ADAPTER_METADATA)}
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    sys.stdout.write("\n")


def main(argv: list[str]) -> int:
    """主入口：根据参数数量选择运行模式。

    Args:
        argv: 命令行参数（不含脚本名）。

    Returns:
        进程退出码（0 成功，非 0 失败）。
    """
    if len(argv) == 0:
        # 标准流模式
        try:
            run_stdio_mode()
        except Exception as exc:  # noqa: BLE001 — 顶层兜底
            sys.stderr.write(f"预测失败: {exc}\n")
            return 1
        return 0

    if len(argv) == 3:
        # 文件协议模式: workdir input_path output_path
        workdir = Path(argv[0])
        input_path = Path(argv[1])
        output_path = Path(argv[2])
        try:
            run_file_mode(workdir, input_path, output_path)
        except Exception as exc:  # noqa: BLE001 — 顶层兜底
            sys.stderr.write(f"预测失败: {exc}\n")
            return 1
        return 0

    sys.stderr.write(
        "用法:\n"
        "  文件协议模式: python adapter.py <workdir> <input_path> <output_path>\n"
        "  标准流模式:   echo '{\"inputs\": {...}}' | python adapter.py\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
