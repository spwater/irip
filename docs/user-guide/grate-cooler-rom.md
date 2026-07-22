# 篦冷机 ROM 用户指南

> 适用版本：IRIP V2+
> 关联文档：`docs/architecture/system-overview.md`、`docs/architecture/domain-invariants.md`、`docs/model-onboarding/model-adapter.md`

本指南描述篦冷机 ROM（Reduced Order Model）从训练到预测、回滚的完整操作流程。篦冷机 ROM 是 IRIP V2 的标志性场景，演示模型全生命周期管理。

---

## 1. 概述

篦冷机 ROM 使用 RandomForestRegressor 多输出回归模型，建立 5 个输入参数到 4 个输出参数的映射：

| 输入参数 | 单位 | 输出参数 | 单位 |
|---------|------|---------|------|
| 篦床风速 | m/s | 二次风温 | ℃ |
| 料层厚度 | mm | 三次风温 | ℃ |
| 熟料产量 | t/h | 篦下压力 | kPa |
| 入料温度 | ℃ | 篦上压差 | kPa |
| 环境温度 | ℃ | | |

数据集为确定性生成（240 行，固定种子 `20260715`，80/20 训练测试分割）。

**角色分工**：

| 角色 | 职责 |
|------|------|
| 模型工程师 (model_engineer) | 训练、评估模型 |
| 审查者 (reviewer) | 发布/回滚模型 |
| 研究员 (researcher) | 使用预测工作台 |

---

## 2. 模型训练

### 2.1 生成确定性数据集

```bash
# 生成 240 行篦冷机数据集（固定种子，可复现）
.venv/bin/python examples/grate-cooler-rom/generate.py
# 输出：dataset.csv（240 行，5 输入 × 4 输出）
```

数据集特点：
- 固定随机种子 `random.Random(20260715)`，确保每次生成结果一致。
- 80% 训练集 + 20% 测试集分割。
- 输出值基于物理关系式 + 小幅噪声模拟，覆盖典型工况范围。

### 2.2 训练 ROM 模型

```bash
# 使用 sklearn RandomForestRegressor 训练多输出回归模型
.venv/bin/python examples/grate-cooler-rom/train.py
# 输出：model.pkl（模型文件）+ metadata.json（元数据 + SHA-256）
```

训练过程：
1. 加载数据集，分割训练/测试集。
2. `RandomForestRegressor(n_estimators=100, random_state=20260715)` 多输出训练。
3. 保存模型文件（joblib 序列化）。
4. 计算模型文件 SHA-256 校验和。
5. 上传模型文件到 MinIO（内容寻址存储）。

### 2.3 通过 API 训练

```bash
# 创建模型
curl -X POST http://localhost:8000/api/v1/models \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"code": "grate_cooler_rom", "display_name": "篦冷机 ROM 模型"}'

# 触发训练（异步作业，返回 202 Accepted + job_id）
curl -X POST http://localhost:8000/api/v1/models/{model_id}/train \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"dataset_artifact_id": "<artifact_id>", "params": {"n_estimators": 100, "random_state": 20260715}}'
```

训练完成后：
- 模型版本状态 `draft` → 模型状态 `pending_validation`。
- 模型文件上传到 MinIO，`model_version.artifact_id` 指向内容寻址对象。

---

## 3. 验证和发布

### 3.1 评估模型

```bash
# 评估模型版本（计算 R²/RMSE/MAE 指标）
curl -X POST http://localhost:8000/api/v1/models/{model_version_id}/evaluate \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"test_artifact_id": "<test_dataset_artifact_id>"}'
```

评估过程：
1. 下载测试集。
2. 模型预测测试集。
3. 计算各输出维度的 R²、RMSE、MAE。
4. 写入 `model_version.metrics_json`。
5. 模型状态 `pending_validation` → `validated`。

**预期指标阈值**（见 `examples/grate-cooler-rom/expected_metrics.json`）：

| 指标 | 阈值 | 说明 |
|------|------|------|
| R² | ≥ 0.85 | 拟合优度 |
| RMSE | ≤ 5.0 | 均方根误差 |
| MAE | ≤ 3.5 | 平均绝对误差 |

### 3.2 发布模型

> 仅审查者 (reviewer) 可发布。审批分离：训练者不能自己发布。

```bash
# 发布模型版本（更新发布指针）
curl -X POST http://localhost:8000/api/v1/models/{model_version_id}/publish \
  -H "Authorization: Bearer <jwt>"
```

发布过程：
1. 模型版本状态 → `published`。
2. 模型发布指针 `model.current_version_id` 指向已发布版本。
3. 记录审计事件（`model.publish`）。
4. 发布后版本不可变，预测工作台可选择此模型。

---

## 4. 预测工作台使用

### 4.1 选择模型

1. 进入 **预测工作台** 页面（`/workbench`）。
2. 模型选择器下拉仅展示 `published` 状态模型（名称 + 版本）。
3. 选择"篦冷机 ROM 模型 v1"。

### 4.2 输入参数

1. 系统根据模型契约 `contract.input_schema` 动态生成参数输入表单：
   - 篦床风速（m/s）：数值输入，范围 [0.5, 5.0]
   - 料层厚度（mm）：数值输入，范围 [100, 800]
   - 熟料产量（t/h）：数值输入，范围 [50, 300]
   - 入料温度（℃）：数值输入，范围 [800, 1400]
   - 环境温度（℃）：数值输入，范围 [-20, 45]
2. 填入参数值，点击"预测"。

### 4.3 查看预测结果

1. 展示预测输出值（二次风温/三次风温/篦下压力/篦上压差）。
2. 展示适用域检查结果（在域/越界标记）。
3. 展示预测溯源链接（→ model_execution 事实 → 溯源图）。

**API 等效操作**：
```bash
curl -X POST http://localhost:8000/api/v1/models/{model_version_id}/predict \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": {
      "grate_speed": 2.5,
      "bed_thickness": 400,
      "clinker_output": 150,
      "inlet_temp": 1200,
      "ambient_temp": 25
    }
  }'
```

返回结构：
```json
{
  "outputs": {
    "secondary_air_temp": 950.2,
    "tertiary_air_temp": 820.5,
    "under_grate_pressure": 4.8,
    "above_grate_dp": 2.3
  },
  "applicability": {
    "in_domain": true,
    "violations": [],
    "per_dimension": {
      "grate_speed": {"in_domain": true, "min": 0.5, "max": 5.0}
    }
  },
  "fact_ref": {"fact_id": "<uuid>", "revision": 1},
  "provenance_link": "/provenance?fact_id=<uuid>"
}
```

### 4.4 预测事实写回

每次预测自动创建 `model_execution` 事实：
- `fact_type = model_execution`
- `subject_id = model_id`
- `value = outputs`
- `conditions = inputs`（输入快照）
- `derivation_ref = model_version_id`

写入后不可变，可通过溯源图导航。

---

## 5. 适用域检查

### 5.1 边界检查

适用域检查器对每个输入维度进行 min/max 边界检查：

```python
# 适用域定义（来自模型契约 contract.json）
"applicability_domain": {
  "grate_speed":     {"min": 0.5, "max": 5.0},
  "bed_thickness":   {"min": 100, "max": 800},
  "clinker_output":  {"min": 50,  "max": 300},
  "inlet_temp":      {"min": 800, "max": 1400},
  "ambient_temp":    {"min": -20, "max": 45}
}
```

### 5.2 越界标记

- 输入超出适用域边界时，`ApplicabilityResult.in_domain = false`。
- 越界维度列入 `violations`（如 `"grate_speed: 6.0 exceeds max 5.0"`）。
- **越界不阻止预测**——预测仍可执行，但结果标记为"越域预测"，提示用户谨慎使用。

### 5.3 逐维度状态

工作台展示每个输入维度的状态指示灯：
- 🟢 在域内：输入值在 [min, max] 范围内。
- 🟡 接近边界：输入值在边界 ±10% 范围内。
- 🔴 越界：输入值超出适用域。

---

## 6. 回滚操作

### 6.1 回滚到历史版本

当新版本模型出现问题，可回滚到历史已发布版本：

```bash
# 回滚模型发布指针（不修改版本内容，仅更新指针）
curl -X POST http://localhost:8000/api/v1/models/{model_id}/rollback \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"target_version_id": "<previous_model_version_id>"}'
```

回滚过程：
1. 验证目标版本存在且状态为 `published`。
2. 更新 `model.current_version_id` 指向目标版本。
3. 记录审计事件（`model.rollback`，含回滚前后指针）。
4. **版本内容不变**——回滚仅移动发布指针，历史版本数据完全保留。

### 6.2 废弃模型

当模型完全不再使用：

```bash
# 废弃模型（状态 → deprecated）
curl -X POST http://localhost:8000/api/v1/models/{model_id}/deprecate \
  -H "Authorization: Bearer <jwt>"
```

废弃后：
- 模型状态 → `deprecated`。
- 预测工作台不再展示此模型。
- 历史预测记录和事实保留不变。

---

## 7. 模型详情页

模型详情页（`/models/{model_id}`）展示：

1. **状态机时间线**：`draft → pending_validation → validated → published → deprecated`，每步含操作人/时间/审计链接。
2. **版本管理**：版本列表，标记当前发布指针版本，支持回滚操作。
3. **评估指标**：R²/RMSE/MAE，多输出按维度展示。
4. **适用域配置**：展示各输入维度的 min/max 边界。

---

## 8. 端到端验收

```bash
# V2 模型执行验收测试
.venv/bin/python -m pytest tests/acceptance/test_v2_model_execution.py -v

# E2E 测试（Playwright）
pnpm --dir apps/web e2e tests/e2e/grate-cooler-rom.spec.ts
```

E2E 验收路径：
1. 组件注册 → 流程编排执行（数据摄入→映射转换→质量统计）
2. 模型训练发布
3. 预测工作台推理
4. 预测事实溯源

---

## 9. 常见问题

### Q: 训练后模型指标不达标怎么办？
A: 调整 RandomForestRegressor 超参数（增加 n_estimators、调整 max_depth），或检查数据集质量。重新训练会创建新版本，旧版本保留不变。

### Q: 预测结果标记为"越域预测"是否可用？
A: 越域预测仍可使用，但需谨慎。适用域检查不阻止预测，仅标记越界状态。建议尽量在适用域范围内使用模型。

### Q: 回滚后旧版本的数据还在吗？
A: 是的。回滚仅更新发布指针，版本内容完全不变。可在模型详情页查看所有历史版本及其指标。

### Q: 废弃模型后历史预测记录会丢失吗？
A: 不会。废弃仅标记模型状态，历史预测记录和 model_execution 事实保留不变，可通过溯源图查询。
