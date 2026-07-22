# 粒度分析用户指南

> 适用版本：IRIP V1+
> 关联文档：`docs/architecture/system-overview.md`、`docs/architecture/domain-invariants.md`

本指南描述粒度实验数据从原始文件到发布参数的完整操作流程。粒度分析是 IRIP 的核心场景之一，覆盖 L1 标准层 → L2 事实层 → L2.5 溯源层 → L3 参数层全链路。

---

## 1. 概述

粒度分析数据流：

```
原始文件 (Excel/CSV/PDF) → 数据摄入 → 标准变量映射 → 事实创建 → 质量评估
    → 证据集冻结 → 推导运行 → 参数候选 → 审批 → 参数发布
```

**角色分工**：

| 角色 | 职责 |
|------|------|
| 标准管理员 (standard_owner) | 标准变量注册、单位转换配置 |
| 数据管理员 (data_steward) | 数据摄入、字段映射、事实录入 |
| 研究员 (researcher) | 证据集冻结、推导运行 |
| 审查者 (reviewer) | 参数候选审批、发布 |

---

## 2. 数据摄入流程

### 2.1 上传数据文件

支持格式：Excel (.xlsx)、CSV、PDF（表格提取）。

**操作步骤**：

1. 进入 **数据摄入** 页面（`/ingestions`）。
2. 点击"上传文件"，选择原始实验数据文件。
3. 系统自动解析文件，展示数据预览表。
4. 进入字段映射环节。

**API 等效操作**：
```bash
# 预签名上传
curl -X POST http://localhost:8000/api/v1/artifacts/presign-upload \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"filename": "experiment-001.xlsx", "size": 102400, "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "sha256": "<file_sha256>"}'

# 确认上传完成
curl -X POST http://localhost:8000/api/v1/artifacts/complete \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"object_key": "<object_key>", "sha256": "<file_sha256>"}'
```

### 2.2 字段映射评分

上传后系统自动对源字段与 L1 标准变量进行映射评分：

1. 系统展示"字段映射评分"面板，每个源字段列出 Top-3 匹配候选标准变量及置信度评分。
2. 数据管理员逐一确认映射：
   - 评分 > 0.9：自动选中（可手动调整）。
   - 评分 0.6–0.9：需人工确认。
   - 评分 < 0.6：需手动指定标准变量或标记为"不映射"。
3. **映射确认门控**：未确认所有映射时，"确认并导入"按钮禁用。

### 2.3 质量校验与导入

1. 确认映射后点击"确认并导入"。
2. 系统自动执行质量检查（Schema 检查、范围检查、粒度序检查）。
3. 检查通过 → 创建事实（Fact + FactRevision 不可变）。
4. 检查不通过 → 展示 DiagnosticReport，可修正后重试。

---

## 3. 标准变量注册

> 仅标准管理员 (standard_owner) 可操作。

### 3.1 创建标准变量

1. 进入 **标准管理** 页面（`/standards`）。
2. 点击"新建变量"。
3. 填写：
   - 变量代码（如 `particle.d50`）
   - 显示名称（如"粒度 D50 中位径"）
   - 数据类型（scalar / observation_table）
   - 单位（如 `μm`）
   - 单位转换规则（仿射变换：`y = ax + b`）
4. 提交后变量状态为 `draft`。

### 3.2 发布标准变量版本

1. 在标准变量详情页点击"发布版本"。
2. 系统创建不可变版本（`standard_variable_version`，status=published）。
3. 发布后版本不可修改，修改需创建新版本。

**API 等效操作**：
```bash
# 创建标准变量
curl -X POST http://localhost:8000/api/v1/standards/variables \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"code": "particle.d50", "display_name": "粒度 D50 中位径", "data_type": "scalar", "unit": "μm"}'

# 发布版本
curl -X POST http://localhost:8000/api/v1/standards/variables/{id}/publish \
  -H "Authorization: Bearer <jwt>"
```

---

## 4. 事实创建和审批

### 4.1 查看实验事实

1. 进入 **实验事实** 页面（`/facts`）。
2. 使用搜索框按关键词搜索，或按状态/类型筛选。
3. 点击事实行查看详情：
   - 观察值（原始值 ↔ 标准化值对照）
   - 质量评估结果（overall_status: pass/warning/fail）
   - 修订历史（revision 列表，不可变）
   - 原始工件链接（→ MinIO 预签名下载）

### 4.2 事实修订

- 事实创建后，修正数据需创建新修订（`fact_revision`），旧修订保留不可变。
- 每条修订携带递增 `revision` 序号和 `created_at` 时间戳。

---

## 5. 证据集冻结

> 研究员 (researcher) 可操作。

### 5.1 创建证据集

1. 进入 **溯源** 页面（`/provenance`）。
2. 选择事实，点击"创建证据集"。
3. 添加事实成员（可跨多个实验批次）。
4. 证据集状态为 `open`（可增删成员）。

### 5.2 冻结证据集

1. 确认成员列表后点击"冻结"。
2. 系统创建不可变证据集版本（`evidence_set_version`，status=frozen）。
3. **冻结后不可逆**：
   - 不可新增/移除成员。
   - 不可修改配方引用。
   - 不可重新冻结。

```bash
# 冻结证据集
curl -X POST http://localhost:8000/api/v1/provenance/evidence-sets/{id}/freeze \
  -H "Authorization: Bearer <jwt>"
```

---

## 6. 推导运行

### 6.1 创建推导配方

1. 在溯源页面选择已冻结的证据集。
2. 点击"创建推导配方"。
3. 配置转换步骤（选择转换组件 + 参数）。
4. 发布配方版本（不可变）。

### 6.2 执行推导运行

1. 选择已发布配方 + 已冻结证据集。
2. 点击"执行推导"。
3. 系统异步执行（Celery Worker），创建 `DerivationRun`。
4. 执行完成后产出 `output_digest`（SHA-256，确定性回放）。
5. 生成参数候选（`ParameterCandidate`）。

### 6.3 确定性回放

- 相同 evidence_set_version + recipe_version → 相同 output_digest。
- 可重复执行验证确定性：
  ```bash
  curl -X POST http://localhost:8000/api/v1/provenance/derivation-runs \
    -H "Authorization: Bearer <jwt>" \
    -d '{"evidence_set_version_id": "<id>", "recipe_version_id": "<id>"}'
  ```

---

## 7. 参数审批和发布

### 7.1 参数候选审批

> 审查者 (reviewer) 可操作。审批分离：提交者不能审批自己的候选。

1. 进入 **参数管理** 页面（`/parameters`）。
2. 查看参数列表（代码、状态、版本、证据数、过期状态）。
3. 点击"候选审批"面板。
4. 每个候选展示：
   - 版本标签、值、置信区间
   - 证据数、质量等级
   - 状态（pending_review / published / rejected）
   - 适用条件
   - 提交者
5. 点击"查看完整来源"→ 跳转溯源图。

### 7.2 审批分离约束

- **self_approval_forbidden**：如果当前用户是候选的提交者，则"批准发布"和"驳回"按钮不显示。
- 提交者只能查看，审批必须由其他 reviewer 角色用户执行。

### 7.3 发布参数

1. reviewer 审批通过 → 参数版本状态变为 `published`（不可变）。
2. 参数发布指针（`parameter.current_version_id`）指向已发布版本。
3. 发布后下游模型/流程可引用此参数版本。

### 7.4 参数过期检测

- 参数可配置有效期（`effective_from` / `effective_to`）。
- 过期后状态标记为 `expired`，下游引用时提示警告。

---

## 8. 溯源导航

完成全链路后，审查者可从任何发布参数导航到原始数据：

```
参数版本 → 推导运行 → 配方 → 证据集成员 → 精确事实修订 → 原始字段 → 原始工件（MinIO）
```

1. 在参数详情页点击"溯源图"。
2. 系统执行 BFS 溯源，展示完整证据链。
3. 点击任意节点可跳转到对应详情页。
4. 点击原始工件节点可预签名下载原始文件。

**验证命令**：
```bash
# 全量验收测试（含溯源不变量）
.venv/bin/python -m pytest tests/acceptance/test_v1_invariants.py -v
```

---

## 9. 常见问题

### Q: 上传后字段映射评分很低怎么办？
A: 检查源字段命名是否与标准变量代码接近；手动指定映射目标；或先注册对应的标准变量。

### Q: 证据集冻结后发现遗漏了事实怎么办？
A: 冻结不可逆。创建新的证据集，添加遗漏事实，重新执行推导。

### Q: 审批时看不到"批准发布"按钮？
A: 你是该候选的提交者。审批分离约束（self_approval_forbidden）禁止提交者审批自己的候选。请让其他 reviewer 角色用户审批。

### Q: 推导运行两次 output_digest 不同？
A: 检查配方是否引用了未固定版本的组件，或是否存在随机操作未注入固定种子。所有随机操作必须使用 `random.Random(seed)`。
