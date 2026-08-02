# IRIP TODO List

> 生成时间：2026-08-03
> 基于项目已知技术债 + 2026-08-02 工作暴露的问题整理

---

## P0 — 上线前必须完成

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 1 | 迁移 squashing | 68 个迁移文件压缩到 3-5 个，空库重建速度快 10 倍。在空库上 squash，保留最近增量 | TODO |
| 2 | Worker 健康检查 + 自动恢复 | Docker healthcheck + restart policy + 启动脚本封装（不能手动拼环境变量）。当前 Worker 挂一次全站不可用 | TODO |
| 3 | 副本语义 UX 完善 | 数据层已落地（system_context 是静态快照），差的是：① 协作对话中加载私有数据时的警告弹窗；② 清除 system_context 后对话记录未同步清空的边界 case | TODO |
| 4 | 迁移后数据校验脚本 | 防止 department_id 挂 root 导致全员可见的问题。校验所有 fact/parameter/job 的 department_id 是否在合法部门树内 | TODO |
| 5 | 多租户端到端验证 | 代码已实现但未端到端跑过。需验证：不同角色用户的可见性、管理权、AI 会话隔离、橱窗共享全链路 | TODO |

## P1 — 质量提升

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 6 | 拆分 AssistantPage.tsx | 1130 行单文件，拆成 hooks（useMessages, useFactContext, useParticipants）+ 子组件 | TODO |
| 7 | 拆分 ai/service.py | 1900+ 行，拆成 conversation / message / showcase / participant / permission 等子 service | TODO |
| 8 | converter 协议 schema 校验 | 加 Pydantic 模型验证输出格式（metadata/points/series 结构），防止自创格式。在 `packages/plugins/protocol.py` 定义输出 schema | TODO |
| 9 | 消息列表换 WebSocket/SSE | 3 秒轮询改长连接，长对话场景性能更好。当前发送期间暂停轮询是临时方案 | TODO |
| 10 | system_context 按需传递 | 大数据量 series 不要全量塞给 LLM，只传 metadata + chart-ref 指令。chart-ref 已实现，system_context 仍是全量 | TODO |
| 11 | model 前端路由注册 | 页面存在但 router.tsx 没注册，用户访问不到 | TODO |
| 12 | derivation_run 接通 | values 空、parameter_version 值全 0，数据推导链路没打通 | TODO |
| 13 | 清除 system_context 时同步清空对话记录 | 前端 `handleClearFactContext` 设 factContext=null，但发消息时传 undefined，后端 `if system_context:` 不满足，旧值残留。应传空字符串触发后端清空 | TODO |

## P2 — 体验优化

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 14 | ProviderStatus 组件清理 | 已从 AssistantPage 移除引用，文件还在。确认无其他引用后删除 | TODO |
| 15 | 前端 null safety 统一 | `?.` 链下游的 `.find()`/`.map()` 系统性加 `(?? [])` 兜底。FactModal 的 `nodes.find` 崩溃是典型案例 | TODO |
| 16 | 对话标题自动生成 | 首条消息截取 30 字符做标题，目前可能是空标题 | TODO |
| 17 | 备份系统 11 个失败单测 | DB 连接问题导致 backup_no_plaintext 失败，与代码无关但要修掉 | TODO |
| 18 | 启动脚本封装 | `start_services.sh` 应包含 API + Worker + Beat 全量环境变量加载，避免手动 `source .env` | TODO |

## P3 — 远期

| # | 事项 | 说明 | 状态 |
|---|------|------|------|
| 19 | 周期性知识图谱 | 数据量上千后落地，graph_node + graph_edge 独立表，大了再迁 Neo4j。关系类型：统计/相似/溯源/文献 | TODO |
| 20 | fact → flow_definition 四层 JOIN 优化 | 加缓存或物化视图，数据量增长后查询性能下降 | TODO |
| 21 | 多租户 RLS 正式启用 | 目前 irip 角色 bypass RLS，应用层 `.in_()` 过滤是唯一防线。生产环境应启用 RLS 双保险 | TODO |
| 22 | RLS 策略改用 department_id 键 | ADR 定案：RLS 从 user_id 键换到 department_id 键。三阶段迁移（见 arch-department-tenant.md） | TODO |

---

## 已完成（参考）

| # | 事项 | 完成时间 |
|---|------|---------|
| - | 多租户迁移 cleanup（parent_id / owner_user_id / visibility_scope） | 2026-08-02 |
| - | 管理权模型（owner + 上级向下） | 2026-08-02 |
| - | 66 站点部门可见性修复 | 2026-08-02 |
| - | system_service 用户 for Worker | 2026-08-02 |
| - | raman_converter + tga_converter 插件 | 2026-08-02 |
| - | AI 助手流式渲染优化 | 2026-08-02 |
| - | 橱窗共享（创建者+参与者） | 2026-08-02 |
| - | chart-ref 引用式画图 | 2026-08-02 |
| - | JSON 紧凑序列化 | 2026-08-02 |
| - | 对话权限模型 + Tab 精简 | 2026-08-02 |
| - | system_context 每次更新到对话记录 | 2026-08-02 |
| - | 数据库备份系统 v1 + v2 PITR | 2026-08-01 |
| - | Converter 插件规范重构 | 2026-08-01 |
| - | 潮线 Tideline UI v2 | 2026-08-01 |
| - | AI 助手橱窗 + 协作 | 2026-07-30/31 |
