# Research Timeline 运维手册

## 切换流程 (Cutover)

1. **基础设施备份** — 记录 backup ID
2. 设置 `RESEARCH_TIMELINE_ENABLED=false`
3. 停止旧 Research 写入，等待活跃 Run 终止
4. 记录各旧表行数（用于验证）
5. 执行 migration `0084`（一次性破坏性删除旧业务数据 + 创建新表）
6. 验证保留表行数/哈希未变（fact、app_user、department、audit_event 等）
7. 部署新 API/Worker/Web
8. Smoke 测试全链路：创建 → 快照 → 推荐 → Turn → 方案 → 执行 → 候选 → 结论 → 综合
9. 设置 `RESEARCH_TIMELINE_ENABLED=true`

## 回滚

- 回滚只回退应用和 schema，**不恢复已删除的业务数据**
- 若必须恢复，走整库恢复（影响同时段其他域，需重大变更审批）

## 日常运维

### Reconciler（每 30 秒自动运行）

- `queued` + 2 分钟无 delivery → 补写 Outbox
- `running` + 10 分钟无 heartbeat → 标记 `task_lost`

### 手动重试

- 推荐失败：`POST /workspaces/{id}/recommendation-batches/{batch_id}/retry`
- 候选提取失败：`POST /workspaces/{id}/turns/{turn_id}/candidate-extraction/retry`
- 分析失败：`POST /workspaces/{id}/turns/{turn_id}/runs`（相同输入重试）

### 告警阈值

- 候选队列 backlog >50 持续 10 分钟
- running heartbeat >10 分钟
- 推荐失败率 >20%/15 分钟
- SSE fallback >30%/15 分钟

## 指标

```
irip_research_recommendation_total{status,count_bucket}
irip_research_recommendation_adoption_total{origin}
irip_research_turn_total{kind,status}
irip_research_turn_duration_seconds{kind}
irip_research_candidate_extraction_total{status}
irip_research_candidate_extraction_duration_seconds
irip_research_timeline_page_seconds
irip_research_active_run_conflict_total
irip_research_context_revision_count
```
