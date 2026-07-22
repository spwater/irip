# 命令行模型适配器示例（Command Model Adapter）

## 概述

这是一个**自包含、零依赖**的命令行模型适配器示例，演示如何将一个
简单线性回归模型接入 IRIP 模型生命周期（V2-T04）。它与
`packages/models/adapters.py` 中的 `CommandModelAdapter` 协议完全兼容，
同时支持标准流模式以便独立调试与管道调用。

模型形式（多元线性回归）：

```
y_j = bias_j + Σ_i ( weight[j][i] × x_i )
```

## 两种调用模式

### 1. 文件协议模式（与 IRIP 平台对接）

`CommandModelAdapter` 会将模型工件写入隔离工作目录，并以下列参数调用适配器：

```
python adapter.py <workdir> <input_path> <output_path>
```

| 参数 | 说明 |
|------|------|
| `workdir` | 隔离工作目录，模型工件 `model.artifact` 已写入其中 |
| `input_path` | 输入 JSON 文件，内容为 `{"inputs": {...}}` |
| `output_path` | 输出 JSON 文件路径，适配器需写入结果 |

输入文件示例（`input.json`）：

```json
{"inputs": {"x1": 1.0, "x2": 2.0}}
```

适配器会将结果写入 `output_path`：

```json
{"predictions": {"y": 5.8}, "metadata": {"model_type": "linear_regression", "adapter": "cli", "framework": "pure-python"}}
```

### 2. 标准流模式（独立调试）

```bash
echo '{"inputs": {"x1": 1.0, "x2": 2.0}}' | python examples/model-adapter-command/adapter.py
```

输出（stdout）：

```json
{"predictions": {"y": 5.8}, "metadata": {"model_type": "linear_regression", "adapter": "cli", "framework": "pure-python"}}
```

## 模型工件（model.artifact）

适配器优先从 `workdir/model.artifact` 读取 JSON 格式的模型定义。
若文件不存在或解析失败，则回退到内置默认模型。

模型工件 JSON 格式：

```json
{
  "inputs": ["x1", "x2"],
  "outputs": ["y"],
  "weights": {"y": {"x1": 2.5, "x2": 1.5}},
  "bias": {"y": 0.3}
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `inputs` | `string[]` | 输入字段名列表 |
| `outputs` | `string[]` | 输出字段名列表 |
| `weights` | `Record<output, Record<input, number>>` | 权重矩阵 |
| `bias` | `Record<output, number>` | 各输出的偏置 |

### 内置默认模型

当无 `model.artifact` 时，使用默认 2 输入 → 1 输出模型：

```
y = 0.3 + 2.5 × x1 + 1.5 × x2
```

## 快速验证

```bash
# 标准流模式
echo '{"inputs": {"x1": 1.0, "x2": 2.0}}' | python examples/model-adapter-command/adapter.py
# 预期输出: {"predictions": {"y": 5.8}, "metadata": {...}}

# 文件协议模式
mkdir -p /tmp/irip-adapter-demo
echo '{"inputs": {"x1": 3.0, "x2": 4.0}}' > /tmp/irip-adapter-demo/input.json
python examples/model-adapter-command/adapter.py /tmp/irip-adapter-demo \
  /tmp/irip-adapter-demo/input.json \
  /tmp/irip-adapter-demo/output.json
cat /tmp/irip-adapter-demo/output.json
# 预期输出: {"predictions": {"y": 14.3}, "metadata": {...}}

# 使用自定义模型工件
echo '{"inputs":["a","b"],"outputs":["z"],"weights":{"z":{"a":1.0,"b":2.0}},"bias":{"z":0.5}}' \
  > /tmp/irip-adapter-demo/model.artifact
echo '{"inputs": {"a": 10.0, "b": 20.0}}' > /tmp/irip-adapter-demo/input.json
python examples/model-adapter-command/adapter.py /tmp/irip-adapter-demo \
  /tmp/irip-adapter-demo/input.json \
  /tmp/irip-adapter-demo/output.json
cat /tmp/irip-adapter-demo/output.json
# 预期输出: {"predictions": {"z": 50.5}, "metadata": {...}}
```

## 接入 IRIP 平台

1. 在模型契约（contract）中将 `executor.type` 设为 `"cli"`；
2. 将 `executor.command` 设为
   `["python", "examples/model-adapter-command/adapter.py"]`；
3. 平台会通过 `CommandModelAdapter` 在隔离工作目录中执行该命令，
   自动传入 `workdir`、`input_path`、`output_path` 三个位置参数。

契约片段示例：

```json
{
  "executor": {
    "type": "cli",
    "command": ["python", "examples/model-adapter-command/adapter.py"],
    "timeout_seconds": 300
  }
}
```

## 依赖

仅使用 Python 标准库（`json` / `sys` / `pathlib`），无需 numpy / sklearn。
兼容 Python 3.9+。
