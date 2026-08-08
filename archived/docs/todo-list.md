# IRIP TODO List

> 最近更新：2026-08-04
> 来源：MEMORY.md「已知技术债」段 + 2026-08-02 工作日志遗留 + 2026-08-03 todo 生成
> 说明：MEMORY.md 旧版「已知技术债」段混入了大量已定案/已实现的设计决策（租户键方向、可见性模型、管理权模型等），它们已归入「已完成」区，此处只保留真正待处理的债。

---

## P0 — 上线前必须完成

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 1 | 迁移 squashing | 68 个迁移文件压缩到 8 个（1 基线 + 7 增量），空库重建速度快 10 倍 | ✅ DONE |
| 2 | Worker 健康检查 + 自动恢复 | Docker healthcheck + restart policy + 启动脚本封装 | ✅ DONE |
| 3 | 副本语义 UX 完善 | 数据层已落地（system_context 是静态快照），差：① 加载私有数据时的警告弹窗；② 清除 system_context 后对话记录未同步清空的边界 case | TODO |
| 4 | 迁移后数据校验脚本 | `scripts/validate_department_tenant.py`：6 类校验（哨兵/NULL/挂root/孤儿/树完整性/system成员）。55 PASS / 0 WARN / 0 FAIL / 4 INFO。admin 属 root 部门产生的 job/audit_event/app_user 挂 root 属对称可见性设计意图 | ✅ DONE |
| 5 | 多租户端到端验证 | 代码已实现但未端到端跑过。需验证：不同角色可见性、管理权、AI 会话隔离、橱窗共享全链路 | TODO |

## P1 — 质量提升

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 6 | 拆分 AssistantPage.tsx | 1130 行单文件，拆成 hooks（useMessages/useFactContext/useParticipants）+ 子组件 | TODO |
| 7 | 拆分 ai/service.py | 1900+ 行，拆成 conversation/message/showcase/participant/permission 子 service | TODO |
| 8 | converter 协议 schema 校验 | 加 Pydantic 模型验证输出格式（metadata/points/series），防止自创格式。定义在 `packages/plugins/protocol.py` | TODO |
| 9 | 消息列表换 WebSocket/SSE | 3 秒轮询改长连接，长对话性能更好。当前发送期间暂停轮询是临时方案 | TODO |
| 10 | system_context 按需传递 | 大数据量 series 不要全量塞给 LLM，只传 metadata + chart-ref 指令。chart-ref 已实现，system_context 仍全量 | TODO |
| 11 | model 前端路由注册 | 页面存在但 router.tsx 没注册，用户访问不到 | TODO |
| 12 | derivation_run 接通 | values 空、parameter_version 值全 0，数据推导链路没打通 | TODO |
| 13 | 清除 system_context 同步清空对话记录 | 前端 handleClearFactContext 设 factContext=null，但发消息时传 undefined，后端 `if system_context:` 不满足，旧值残留。应传空字符串触发后端清空 | TODO |
| 14 | retention_cleanup 未设 dept GUC | `delete_expired` 任务未 SET app.current_dept_id，service 层需适配。Worker 用 system_service 用户跑，但清理路径未走多租户 GUC，可能导致跨租户清理越权 | TODO |
| 15 | owner_user_id 不可改缺 DB 触发器保护 | `forbid_reprivatize` 触发器只检查 visibility_scope 字段，未校验 owner_user_id 是否被篡改。owner_user_id 不可改是设计约束，需 DB 层兜底 | TODO |

## P2 — 体验优化

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 16 | ProviderStatus 组件清理 | 已从 AssistantPage 移除引用，文件还在。确认无其他引用后删除 | TODO |
| 17 | 前端 null safety 统一 | `?.` 链下游的 `.find()`/`.map()` 系统性加 `(?? [])` 兜底。FactModal 的 `nodes.find` 崩溃是典型案例 | TODO |
| 18 | 对话标题自动生成 | 首条消息截取 30 字符做标题，目前可能是空标题 | TODO |
| 19 | 备份系统失败单测 | backup_no_plaintext 11 个失败（DB 连接问题，与代码无关）。08-01 Docker 测试环境已修至 0 失败，但本地环境仍可能失败，需统一 conftest | TODO |
| 20 | 启动脚本封装 | start_services.sh 包含 API + Worker + Beat 全量环境变量加载 | ✅ DONE |

## P3 — 远期

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 21 | 周期性知识图谱 | 数据量上千后落地，graph_node + graph_edge 独立表，大了再迁 Neo4j。关系类型：统计/相似/溯源/文献 | TODO |
| 22 | fact → flow_definition 四层 JOIN 优化 | 加缓存或物化视图，数据量增长后查询性能下降 | TODO |
| 23 | 多租户 RLS 正式启用 | 迁移 0071：irip NOBYPASSRLS + irip_app 运行时连接 + 12 处 Worker/Beat GUC 补全 + system_service 挂 root。fail-closed 验证通过 | ✅ DONE |
| 24 | RLS 策略改用 department_id 键 | ADR 定案：RLS 从 user_id 键换到 department_id 键。三阶段迁移（见 arch-department-tenant.md）—— 0062-0065 已完成，0071 通电 | ✅ DONE |
| 25 | 阶段3退役迁移（0066） | 阶段1加列回填+阶段2切换RLS 已完成，阶段3 DROP org_id 列需观察阶段2稳定后执行 | TODO |

---

## 已完成（参考）

| # | 事项 | 完成时间 |
|---|------|---------|
| - | 迁移 squashing（68→8 文件） | 2026-08-03 |
| - | Worker 健康检查 + 自动恢复 | 2026-08-03 |
| - | 启动脚本封装（start_worker.sh + start_beat.sh + start_services.sh） | 2026-08-03 |
| - | 维护文档（conventions.md + decision-log.md + onboarding.md） | 2026-08-04 |
| - | 多租户迁移 cleanup（parent_id / owner_user_id / visibility_scope） | 2026-08-02 |
| - | 管理权模型（owner + 上级向下） | 2026-08-02 |
| - | 66 处部门可见性硬过滤修复 | 2026-08-02 |
| - | system_service 用户 for Worker | 2026-08-02 |
| - | raman_converter + tga_converter 插件 | 2026-08-02 |
| - | AI 助手流式渲染优化 | 2026-08-02 |
| - | 橱窗共享（创建者+参与者） | 2026-08-02 |
| - | chart-ref 引用式画图 | 2026-08-02 |
| - | JSON 紧凑序列化 | 2026-08-02 |
| - | 对话权限模型 + Tab 精简 | 2026-08-02 |
| - | system_context 每次更新到对话记录 | 2026-08-02 |
| - | 租户键方向定案（ADR 可冻结） | 2026-08-02 |
| - | 哨兵保护改版（放宽 display_name 等） | 2026-08-02 |
| - | P1 功能增强 6 项（数据移交/root监控等） | 2026-08-02 |
| - | 可见性模型（对称层级）拍板 | 2026-08-02 |
| - | 管理权模型确立 | 2026-08-02 |
| - | 多部门用户可见性修复 | 2026-08-02 |
| - | 刷新登出修复 | 2026-08-02 |
| - | 数据库备份系统 v1 + v2 PITR | 2026-08-01 |
| - | Converter 插件规范重构 | 2026-08-01 |
| - | 潮线 Tideline UI v2 | 2026-08-01 |
| - | AI 助手橱窗 + 协作 | 2026-07-30/31 |

---

## 备注：MEMORY.md「已知技术债」段需更新

MEMORY.md 当前「已知技术债」段约 12 行，其中 **8 项实为已定案/已实现的设计决策**（租户键方向、哨兵保护改版、P1功能增强、可见性模型、管理权模型、多部门可见性修复、刷新登出修复、scope_grant表删除），不属于"技术债"。建议下次更新 MEMORY.md 时将这 8 项移出「已知技术债」段（它们已在「已完成」区归档），仅保留真正待处理的 3 条：
- 现状对照：代码已实现但未端到端验证
- model 前端无路由注册
- derivation_run 未接通 fact_data_index
