# IRIP 领域不变量

> 版本：0.2.0 · 覆盖 Phase V0–V3
> 关联文档：`docs/architecture/system-overview.md`

本文档定义 IRIP 平台在演进过程中必须始终满足的约束基线。这些不变量是系统正确性的基石——任何变更不得违反。

---

## 1. L1 标准不可变性

**规则**：标准变量发布后版本不可修改，修改需创建新版本。

**实现**：
- `standard_variable`（稳定身份表）+ `standard_variable_version`（不可变版本表）两表设计。
- `standard_variable_version` 表只 INSERT 不 UPDATE，`status=published` 后禁止任何字段变更。
- 单位转换规则（仿射变换 `y = ax + b`）随版本绑定，版本不变则转换不变。

**校验**：
```sql
-- 不变量校验：published 版本的 modified_at 应等于 created_at
SELECT id FROM standard_variable_version
WHERE status = 'published' AND modified_at > created_at;
-- 期望：0 行
```

---

## 2. L2 事实修订不可变性

**规则**：事实修订发布后不可修改，修正需创建新修订版本。

**实现**：
- `fact`（稳定身份）+ `fact_revision`（不可变修订），每条修订携带 `revision` 递增序号。
- 修订内容（观察值、质量评估、工件链接）写入后只读。
- `fact_revision` 表禁止 UPDATE/DELETE（应用层约束 + 审计约束）。

**校验**：
```sql
-- 不变量校验：同一事实的多条修订 revision 严格递增
SELECT fact_id, COUNT(DISTINCT revision) as distinct_revs, COUNT(*) as total_revs
FROM fact_revision GROUP BY fact_id
HAVING COUNT(DISTINCT revision) != COUNT(*);
-- 期望：0 行
```

---

## 3. L2.5 证据集冻结

**规则**：证据集冻结后成员列表不可变，冻结操作不可撤销。

**实现**：
- `evidence_set`（稳定身份）+ `evidence_set_version`（不可变版本）。
- 冻结操作：`EvidenceSet.freeze()` → INSERT `evidence_set_version`（status=frozen）。
- 冻结后：不允许新增/移除成员、不允许修改配方引用、不允许重新冻结（单次操作）。
- 推导配方（`transformation_recipe` + `transformation_recipe_version`）同样不可变——版本发布后只读。

**校验**：
```python
# 冻结后状态不可回退
assert evidence_set_version.status == "frozen"
with pytest.raises(AppError, match="already_frozen"):
    await evidence_set_service.freeze(evidence_set_id)
```

---

## 4. L3 参数版本不可变性

**规则**：参数版本发布后不可修改，发布操作不可撤销。审批分离：提交者不能审批自己的候选。

**实现**：
- `parameter`（稳定身份）+ `parameter_version`（不可变版本）。
- `parameter_version` status: `draft → pending_review → published / rejected`，published 后只读。
- `parameter_candidate.submitted_by` != `parameter_candidate.reviewed_by`（self_approval_forbidden）。
- 发布指针 `parameter.current_version_id` 指向已发布版本，回滚仅更新指针（版本不可变）。

**校验**：
```sql
-- 审批分离：已发布候选的提交者和审批人不能相同
SELECT COUNT(*) FROM parameter_candidate
WHERE status = 'published' AND reviewed_by IS NOT NULL
  AND submitted_by = reviewed_by;
-- 期望：0 行
```

---

## 5. 组件版本不可变性

**规则**：组件版本发布后不可修改，修改需创建新版本。组件清单（YAML）附 SHA-256 校验和。

**实现**：
- `component`（稳定身份，status: draft→published→deprecated）+ `component_version`（不可变版本）。
- `component_version.manifest_sha256` = SHA-256(manifest_yaml)，确保清单内容完整性。
- 执行器通过 `(name, version)` 解析 manifest，版本不变则行为确定。

**校验**：
```python
# 版本不可变：重复发布同版本号应被拒绝
with pytest.raises(AppError, match="version_exists"):
    await registry_service.publish(manifest_with_same_version)
```

---

## 6. 流程定义版本不可变性

**规则**：流程定义版本发布后不可修改，流程运行确定性回放。

**实现**：
- `flow_definition`（稳定身份）+ `flow_definition_version`（不可变版本，含 nodes/edges/random_seed/digest）。
- `flow_definition_version.digest` = SHA-256(所有节点 + 边 + 随机种子的有序序列化)。
- `flow_run.output_digest` = SHA-256(所有节点输出摘要的有序拼接)。
- 相同输入（组件版本 + 参数 + 输入快照）→ 相同 output_digest（确定性回放）。

**校验**：
```python
# 相同输入两次执行，output_digest 应一致
run1 = await flow_runtime.execute(flow_version_id, inputs)
run2 = await flow_runtime.execute(flow_version_id, inputs)
assert run1.output_digest == run2.output_digest
```

---

## 7. 模型版本不可变性

**规则**：模型版本发布后不可修改，模型文件附 SHA-256 校验和。发布指针回滚不修改版本内容。

**实现**：
- `model`（稳定身份，status: draft→pending_validation→validated→published→deprecated）+ `model_version`（不可变版本）。
- `model_version.artifact_id` 指向 MinIO 内容寻址对象（SHA-256 去重）。
- `model.current_version_id` 发布指针，回滚仅更新指针（`UPDATE model SET current_version_id = target`），版本内容不变。
- 模型训练/评估/预测可复现：篦冷机数据集固定种子 `20260715`。

**校验**：
```sql
-- 回滚后版本内容不变：回滚前后的 model_version 行应完全一致
SELECT contract_json, artifact_id, metrics_json
FROM model_version WHERE id = :target_version_id;
-- 回滚前后查询结果应完全相同
```

---

## 8. 确定性回放

**规则**：相同输入 → 相同输出。推导运行、流程运行、模型评估均可复现。

**实现**：
- **推导运行**：`DerivationRun.output_digest` = SHA-256(所有配方步骤输出的有序拼接)，相同 evidence_set_version + recipe_version → 相同 digest。
- **流程运行**：`FlowRun.output_digest` = SHA-256(所有节点输出摘要的有序拼接)，相同 flow_version + input_snapshot → 相同 digest。
- **模型评估**：固定种子 + 固定数据集 → 相同指标（R²/RMSE/MAE）。
- **离线 AI**：OfflineProvider 相同输入 → 相同响应，无网络依赖。

**约束**：
- 所有涉及随机的操作必须显式注入 `random.Random(seed)`，禁止使用全局 `random()`。
- 所有时间戳通过 `Clock` 协议注入（`SystemClock` 生产 / `FixedClock` 测试），禁止 `datetime.now()` 直接调用。

---

## 9. 审计仅追加

**规则**：审计事件表只允许 INSERT + SELECT，禁止 UPDATE/DELETE。

**实现**：
- `audit_event` 表数据库角色 `REVOKE UPDATE, DELETE`。
- `AuditRecorder.record()` 仅执行 INSERT。
- payload 写入前经 `redact()` 脱敏（password/token/secret/key/authorization → `[REDACTED]`）。

**校验**：
```sql
-- 确认权限已撤销
REVOKE UPDATE, DELETE ON audit_event FROM irip_app;
-- 尝试 UPDATE 应报权限错误
```

---

## 10. AI 工具只读边界

**规则**：AI 助手只能调用预定义的只读查询工具，禁止任意代码执行/数据修改。

**实现**：
- `ToolRegistry` 维护 7 个白名单工具（search_facts/get_fact/list_parameters/get_parameter/list_models/get_model_detail/get_provenance），均为只读查询。
- 候选工具（search_standards/list_components/get_flow_run）默认 `enabled=False`，需显式配置启用。
- 每次 tool_call 在执行前必须经 `ToolRegistry.is_allowed(tool_name)` 校验，非白名单工具抛 `AppError(code="tool_not_allowed")`。
- AI 工具执行时携带 `organization_id + user_id`，查询范围限定在当前用户组织内（跨组织数据不可见）。

**校验**：
```python
# 非白名单工具被拒绝
assert not tool_registry.is_allowed("delete_all_facts")
with pytest.raises(AppError, match="tool_not_allowed"):
    await tool_registry.execute_tool("delete_all_facts", {}, org_id, user_id)
```

---

## 11. 备份校验和完整性

**规则**：每个备份组件（PostgreSQL + MinIO）附带 SHA-256 校验和，恢复前必须验证。

**实现**：
- `BackupManifest` 携带 `database_sha256`（dump 文件 SHA-256）+ `objects_sha256`（MinIO 对象元数据聚合 SHA-256）+ `object_count`。
- `BackupManifestValidator.validate()` 恢复前重算校验和与 manifest 比对，任一不匹配则中止恢复。
- `manifest.migration_version` 记录 Alembic 版本，恢复时用于前向兼容判断（降级场景拒绝自动迁移）。
- Redis 不纳入备份（仅缓存/队列，任务可重放）。

**校验**：
```python
# 篡改备份后恢复应被拒绝
with pytest.raises(AppError, match="database_sha256_mismatch"):
    await restore_service.restore(tampered_backup_dir)
```

---

## 12. 作业幂等提交

**规则**：重复投递的作业不产生副作用，幂等键唯一约束拦截重复执行。

**实现**：
- `job.idempotency_key` + `organization_id` 组成 UNIQUE 约束。
- Worker 租约（TTL 30s + 心跳 10s）保证同一作业不被并发执行。
- Worker 崩溃 → 租约过期 → `reaper` 重新入队 → 重新执行时幂等键拦截重复提交。

**校验**：
```python
# 重复投递同一幂等键返回同一 job
job1 = await job_service.accept("echo", payload, key="abc-123")
job2 = await job_service.accept("echo", payload, key="abc-123")
assert job1.id == job2.id  # 幂等：返回同一作业
```

---

## 13. 工件内容寻址去重

**规则**：相同内容的工件共享同一存储对象，不同业务引用可指向同一 blob。

**实现**：
- `artifact_blob`（SHA-256 主键 + `object_key = sha256/<前2位>/<digest>`）。
- `artifact`（业务引用，指向 `artifact_blob.sha256`）。
- 上传时先查 `artifact_blob` 是否已存在（秒传），存在则仅 INSERT `artifact`，不再上传。

**校验**：
```sql
-- 同一文件上传两次：artifact_blob 仅 1 行，artifact 2 行
SELECT COUNT(*) FROM artifact_blob WHERE sha256 = :digest;  -- 期望 1
SELECT COUNT(*) FROM artifact WHERE sha256 = :digest;      -- 期望 2
```

---

## 不变量优先级

| 优先级 | 不变量 | 违反后果 |
|--------|--------|---------|
| P0 | 事实修订不可变 | 证据链完整性破坏，溯源失效 |
| P0 | 证据集冻结 | 推导可复现性丧失 |
| P0 | 参数版本不可变 | 发布参数被篡改，下游模型受影响 |
| P0 | 审计仅追加 | 安全合规违规 |
| P0 | 备份校验和 | 灾难恢复数据损坏 |
| P1 | 标准不可变 | 单位转换不一致 |
| P1 | 组件版本不可变 | 流程确定性丧失 |
| P1 | 流程定义不可变 | 确定性回放失效 |
| P1 | 模型版本不可变 | 预测可复现性丧失 |
| P1 | 确定性回放 | 实验可复现性丧失 |
| P1 | 作业幂等提交 | 重复执行副作用 |
| P1 | 工件内容寻址 | 存储浪费 |
| P2 | AI 工具只读边界 | AI 越权操作风险 |
