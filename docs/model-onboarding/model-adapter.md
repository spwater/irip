# 模型上线指南 — 适配器开发（ModelAdapter）

> 适用版本：IRIP V2+
> 关联文档：`docs/user-guide/grate-cooler-rom.md`、`docs/architecture/domain-invariants.md`

本指南描述如何将外部模型接入 IRIP 平台：从 ModelAdapter 协议实现到模型契约定义、训练/验证/发布全流程。

---

## 1. 概述

模型上线流程：

```
ModelAdapter 协议实现 → 模型契约定义 → 训练组件接入 → 评估指标配置 → 发布流程 → 预测工作台可用
```

**ModelAdapter** 是模型执行的统一抽象协议，所有模型执行方式（CLI 命令行、Python 库、远程服务）必须实现此协议。IRIP 默认提供 `CLIModelAdapter`（命令行适配器），覆盖大多数外部模型接入场景。

---

## 2. ModelAdapter 协议

### 2.1 协议定义

```python
# packages/models/contracts.py

@runtime_checkable
class ModelAdapter(Protocol):
    """模型适配器协议。
    所有模型执行方式必须实现此协议。
    """

    def load(self, model_path: str, contract: ModelContract) -> None:
        """加载模型文件。验证模型文件存在且可读。"""
        ...

    def validate_input(self, inputs: dict) -> ValidationResult:
        """验证输入参数是否符合模型契约的 input_schema。"""
        ...

    def predict(self, inputs: dict) -> dict:
        """执行预测，返回输出参数。"""
        ...

    def healthcheck(self) -> bool:
        """健康检查：模型是否可正常加载和预测。"""
        ...
```

### 2.2 ModelContract 结构

模型契约是 frozen dataclass，从 JSON 文件解析：

```python
@dataclass(frozen=True)
class ModelContract:
    """模型契约（不可变值对象）。"""
    name: str                # 模型名称
    version: str             # 契约版本
    input_schema: dict       # 输入参数 JSON Schema（用于表单动态生成）
    output_schema: dict      # 输出参数 JSON Schema
    applicability_domain: dict  # 适用域定义（各输入维度 min/max）
    sha256: str              # 契约文件 SHA-256 摘要
```

---

## 3. 命令行适配器实现

### 3.1 CLIModelAdapter（平台内置）

IRIP 内置 `CLIModelAdapter`，通过 subprocess 调用外部模型程序：

```python
# packages/models/adapters.py

class CLIModelAdapter:
    """命令行模型适配器。
    通过 subprocess 调用外部模型程序：
    - stdin：传入输入参数 JSON
    - stdout：读取输出参数 JSON
    - 支持超时、取消、受限环境变量
    """
```

### 3.2 外部模型程序接口约定

外部模型程序需遵守 stdin/stdout JSON 通信协议：

**输入（stdin）**：
```json
{
  "action": "predict",
  "model_path": "/path/to/model.pkl",
  "inputs": {
    "grate_speed": 2.5,
    "bed_thickness": 400,
    "clinker_output": 150,
    "inlet_temp": 1200,
    "ambient_temp": 25
  }
}
```

**输出（stdout）**：
```json
{
  "outputs": {
    "secondary_air_temp": 950.2,
    "tertiary_air_temp": 820.5,
    "under_grate_pressure": 4.8,
    "above_grate_dp": 2.3
  }
}
```

**健康检查模式**：
```bash
# 外部模型程序需支持 --healthcheck 参数
python my_model.py --healthcheck
# 输出 {"status": "ok"} 或非零退出码
```

### 3.3 命令行适配器示例

参考 `examples/model-adapter-command/adapter.py`：

```python
#!/usr/bin/env python3
"""命令行模型适配器示例。
演示如何通过 CLI 运行时接入外部模型。
"""
import json
import sys
import argparse


def predict(inputs: dict) -> dict:
    """执行预测。"""
    # 示例：简单的线性模型
    outputs = {
        "secondary_air_temp": 800 + inputs["inlet_temp"] * 0.1,
        "tertiary_air_temp": 700 + inputs["inlet_temp"] * 0.08,
        "under_grate_pressure": inputs["grate_speed"] * 2.0,
        "above_grate_dp": inputs["grate_speed"] * 0.9,
    }
    return {"outputs": outputs}


def healthcheck() -> dict:
    """健康检查。"""
    return {"status": "ok"}


def main() -> None:
    parser = argparse.ArgumentParser(description="命令行模型适配器")
    parser.add_argument("--healthcheck", action="store_true", help="健康检查")
    args = parser.parse_args()

    if args.healthcheck:
        print(json.dumps(healthcheck()))
        return

    # 从 stdin 读取输入 JSON
    request = json.loads(sys.stdin.read())
    result = predict(request["inputs"])
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

---

## 4. 模型契约定义

### 4.1 契约 JSON 文件

模型契约定义输入/输出参数结构和适用域，存储为 JSON 文件：

```json
// examples/grate-cooler-rom/contract.json
{
  "name": "grate_cooler_rom",
  "version": "1.0.0",
  "input_schema": {
    "type": "object",
    "properties": {
      "grate_speed":     {"type": "number", "minimum": 0.5, "maximum": 5.0,  "description": "篦床风速 (m/s)"},
      "bed_thickness":   {"type": "number", "minimum": 100, "maximum": 800,  "description": "料层厚度 (mm)"},
      "clinker_output":  {"type": "number", "minimum": 50,  "maximum": 300,  "description": "熟料产量 (t/h)"},
      "inlet_temp":      {"type": "number", "minimum": 800, "maximum": 1400, "description": "入料温度 (℃)"},
      "ambient_temp":    {"type": "number", "minimum": -20, "maximum": 45,   "description": "环境温度 (℃)"}
    },
    "required": ["grate_speed", "bed_thickness", "clinker_output", "inlet_temp", "ambient_temp"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "secondary_air_temp": {"type": "number", "description": "二次风温 (℃)"},
      "tertiary_air_temp":  {"type": "number", "description": "三次风温 (℃)"},
      "under_grate_pressure": {"type": "number", "description": "篦下压力 (kPa)"},
      "above_grate_dp":     {"type": "number", "description": "篦上压差 (kPa)"}
    }
  },
  "applicability_domain": {
    "grate_speed":     {"min": 0.5, "max": 5.0},
    "bed_thickness":   {"min": 100, "max": 800},
    "clinker_output":  {"min": 50,  "max": 300},
    "inlet_temp":      {"min": 800, "max": 1400},
    "ambient_temp":    {"min": -20, "max": 45}
  }
}
```

### 4.2 契约验证

契约文件通过 JSON Schema v1 验证（`schemas/model-contract/v1.schema.json`）：

```python
from packages.models.contracts import ModelContract
from pathlib import Path
import json

# 解析契约
contract_text = Path("examples/grate-cooler-rom/contract.json").read_text()
contract = ModelContract.from_json(contract_text)

# 验证
assert contract.input_schema["type"] == "object"
assert "applicability_domain" in contract.__dict__ or hasattr(contract, "applicability_domain")
assert contract.sha256  # 自动计算 SHA-256
```

---

## 5. 训练 / 验证 / 发布流程

### 5.1 训练

```bash
# 创建模型（稳定身份）
curl -X POST http://localhost:8000/api/v1/models \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"code": "grate_cooler_rom", "display_name": "篦冷机 ROM 模型"}'

# 触发训练（异步作业）
curl -X POST http://localhost:8000/api/v1/models/{model_id}/train \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_artifact_id": "<dataset_artifact_id>",
    "params": {"n_estimators": 100, "random_state": 20260715}
  }'
```

训练完成后：
- 模型文件上传到 MinIO（内容寻址，SHA-256 去重）。
- `model_version` 记录（artifact_id 指向 MinIO 对象，status=draft）。
- 模型状态 `draft → pending_validation`。

### 5.2 验证

```bash
# 评估模型版本
curl -X POST http://localhost:8000/api/v1/models/{model_version_id}/evaluate \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"test_artifact_id": "<test_dataset_artifact_id>"}'
```

评估完成后：
- 计算 R²/RMSE/MAE 指标，写入 `model_version.metrics_json`。
- 模型状态 `pending_validation → validated`。

### 5.3 发布

> 审批分离：训练者不能自己发布。需 reviewer 角色用户发布。

```bash
# 发布模型版本
curl -X POST http://localhost:8000/api/v1/models/{model_version_id}/publish \
  -H "Authorization: Bearer <jwt>"
```

发布完成后：
- 模型版本状态 → `published`（不可变）。
- 发布指针 `model.current_version_id` 指向已发布版本。
- 记录审计事件。
- 预测工作台可选择此模型。

---

## 6. 适配器配置

### 6.1 CLI 适配器配置

在创建模型版本时指定适配器类型和配置：

```json
{
  "adapter_type": "cli",
  "adapter_config": {
    "command": "python",
    "args": ["/models/grate_cooler/adapter.py"],
    "timeout": 30,
    "env": {
      "MODEL_PATH": "/models/grate_cooler/model.pkl"
    }
  }
}
```

| 配置项 | 说明 |
|--------|------|
| `command` | 执行命令（如 `python`） |
| `args` | 命令参数列表 |
| `timeout` | 超时时间（秒） |
| `env` | 环境变量（受限，不传递父进程全部环境） |

### 6.2 安全约束

- CLI 适配器通过 subprocess + 超时 + 受限环境变量执行。
- 不传递父进程的全部环境变量（仅传递显式配置的 env）。
- 超时后发送 SIGTERM 终止子进程。
- 模型文件路径由平台控制（ArtifactService 内容寻址），不接受用户任意路径。

---

## 7. 在流程中编排模型

模型组件可在流程（Flow）中编排：

```
[train(数据集)] → [evaluate(测试集)] → [applicability_check] → [predict(输入)]
```

| 模型组件 | 职责 |
|---------|------|
| `train` | 封装 ModelService.train 为组件 |
| `evaluate` | 封装 ModelService.evaluate 为组件 |
| `applicability` | 封装 ApplicabilityChecker 为组件 |
| `predict` | 封装 ModelService.predict 为组件，写 model_execution 事实 |

---

## 8. 适用域检查

### 8.1 边界检查规则

适用域检查器对每个输入维度进行 min/max 边界检查：

```python
from packages.models.applicability import ApplicabilityChecker

checker = ApplicabilityChecker()
result = checker.check(
    inputs={"grate_speed": 6.0, "bed_thickness": 400, ...},
    domain={"grate_speed": {"min": 0.5, "max": 5.0}, ...}
)
# result.in_domain = False
# result.violations = ("grate_speed: 6.0 exceeds max 5.0",)
# result.per_dimension = {"grate_speed": {"in_domain": False, ...}, ...}
```

### 8.2 越界行为

- 越界 **不阻止** 预测——预测仍可执行。
- 越界结果在预测返回中标记 `in_domain = false`。
- 前端展示越界警告，提示用户谨慎使用。

---

## 9. 预测事实写回

每次预测自动创建 `model_execution` 事实，纳入 V1 证据链：

```python
# ModelService.predict() 内部逻辑
fact = await fact_service.create_fact(
    fact_type="model_execution",
    subject_id=model_id,
    value=outputs,           # 预测输出
    conditions=inputs,       # 输入快照
    derivation_ref=model_version_id  # 模型版本引用
)
```

写入后：
- 事实不可变（FactRevision 只追加）。
- 可通过溯源图从预测结果导航到模型版本。
- 审查者可验证预测的可追溯性。

---

## 10. 常见问题

### Q: 如何接入非 Python 编写的模型？
A: 使用 CLI 适配器。编写一个命令行程序，stdin 接收 JSON 输入，stdout 返回 JSON 输出。支持 `--healthcheck` 参数即可。

### Q: 模型文件如何上传到平台？
A: 通过 Artifact API 上传文件到 MinIO（内容寻址存储）。训练时系统自动上传模型文件；手动上传可通过 `/api/v1/artifacts/presign-upload` + `/api/v1/artifacts/complete` 完成。

### Q: 适用域检查不通过会阻止预测吗？
A: 不会。适用域检查仅标记越界状态，不阻止预测执行。越域预测结果仍返回，但附带 `in_domain = false` 标记。

### Q: 如何回滚到历史模型版本？
A: 调用 `POST /api/v1/models/{id}/rollback`，指定目标版本 ID。回滚仅更新发布指针，版本内容不变。详见 `docs/user-guide/grate-cooler-rom.md` §6。

### Q: 适配器的环境变量是否安全？
A: CLI 适配器仅传递显式配置的环境变量，不继承父进程的全部环境。凭据应通过 `secret_id` 引用，不内联明文。
