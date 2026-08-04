# IRIP 关键决策记录

> 记录"为什么这么设计"的关键决策。每条 3-5 行，说清决策+理由。
> 完整 ADR 见 `docs/arch-department-tenant.md` 等独立文档。
> 最后更新：2026-08-04

---

## 架构层

### D-001 模块化单体而非微服务

**决策**：FastAPI 单体 + Celery Worker，不做微服务拆分。
**理由**：项目处于 Alpha 阶段，团队小，微服务的运维成本远大于收益。单体足够支撑当前规模，未来可按模块独立部署。
**影响**：所有 `packages/` 模块在同一进程内运行，通过函数调用而非 RPC 通信。

### D-002 PostgreSQL 为唯一权威存储

**决策**：不引入 MongoDB / Elasticsearch / Neo4j，所有数据存 PostgreSQL 16 + pgvector。
**理由**：减少运维复杂度。PG 的 JSONB、pgvector、RLS、CTE 能覆盖当前所有需求。知识图谱等远期需求大了再迁。
**影响**：全文搜索用 `tsvector`，向量搜索用 pgvector，关系图用独立表（`graph_node + graph_edge`，未落地）。

### D-003 Outbox 模式保证异步可靠性

**决策**：写业务表 + 写 `outbox_event` 在同一事务，dispatcher 轮询投递到 Celery。
**理由**：避免"业务写成功但消息没发出去"的不一致。比直接 `celery.send_task()` 多一次 DB 写，但换来可恢复性。
**影响**：Worker 崩溃后重启，未投递的 outbox 事件会自动补发。

---

## 数据层

### D-004 fact 不可编辑

**决策**：实验数据（fact）写入后不可修改，只可 archive。
**理由**：科研数据的可信度建立在"写入即冻结"上。如果可以改实验结果，溯源链就断了。
**影响**：DB 触发器阻止 UPDATE/DELETE；`FactService` 没有 `update()` 方法，只有 `create()` 和 `archive()`。

### D-005 删除 fact 版本链（0055 迁移）

**决策**：删除 fact_revision / raw_observation / normalized_observation 等 7 张表，字段合并回 fact。
**理由**：版本链设计了但从未使用多版本（50 条 fact 每条恰好 1 个 revision，1:1）。维护成本高，收益为零。
**影响**：`FactDataIndex` FK 从 `fact_revision_id` 改为 `fact_id`；`FactRef` 替代 `FactRevisionRef`。

### D-006 删除标准层 10 张表（0057 迁移）

**决策**：删除 variable / template / package / mapping_profile / fact_template 等标准层表。
**理由**：标准层是过度设计——实验室不需要这么重的元数据管理层。`fact_type` 直接在 `facts/service.py` 里硬编码即可。
**影响**：IngestionWizard 从 7 步砍到 5 步；`method_refs` 改为 JSONB 直接存 fact 上。

### D-007 department_id 扶正为多租户隔离键

**决策**：`department_id` 替代 `organization_id` 成为全系统唯一的多租户隔离键，organization 退役。
**理由**：organization 全库仅 1 个常量，RLS 过滤永远为真，是恒真开销。department 有完整 CRUD + UI，用户可感知。
**影响**：见 `docs/arch-department-tenant.md`，五条核心规则 + 三阶段迁移。

### D-008 对称层级可见性

**决策**：可见性 = 向下穿透（父看子孙）+ 向上回溯（子看祖先），不是只向下。
**理由**：用户确立"信任是相互的"——课题组能看到实验室本级数据，和实验室能看到课题组数据是对称的。
**影响**：root 公共数据全员可见是推论不是特例（root 是所有部门祖先链终点）；管理员 = root 成员无需 bypass 角色。

### D-009 个人私有数据是唯一具名例外

**决策**：`visibility_scope='private'` + `owner_user_id`，私有数据仅 owner 可见（含管理员），单向不可逆。
**理由**：科研人员需要空间做未公开的探索。隐私语义强于管理便利是有意取舍。
**影响**：DB 触发器 `forbid_reprivatize` 禁止 `tree → private` 回退；`owner_user_id` 不可改。

### D-010 AI 会话不进部门树

**决策**：ai_conversation 可见者 = 创建者 + 参与者，不走 department 树规则。
**理由**：如果走向上通道，上级链能翻看所有下属的 AI 对话，构成非预期行为变更。
**影响**：`conversation_participant` 是唯一跨人共享通道；副本语义 copy-on-share（看过即拥有，不可回收）。

---

## 前端层

### D-011 潮线 Tideline 水光版取代极地雾蓝

**决策**：推翻 v1（斜切几何 + 撞色块），全面转向曲线/渐变/透明光泽。
**理由**：用户否决了斜切版——"太生硬，我要的是流动感"。深蓝实色块在浅底上是突兀的"胎记"，不是深度。
**影响**：三条审美红线成为所有 UI 开发的约束；签名元素只用一次。

### D-012 不引入新 UI 框架

**决策**：继续用 Ant Design 5 + ConfigProvider 主题桥接，不换 Radix / Mantine / shadcn。
**理由**：换框架的迁移成本远大于主题定制。现有组件库已覆盖 90% 需求。
**影响**：共用视觉组件层在 Ant Design 之上封装（`OceanPanel`、`PageIntro` 等）。

### D-013 Plotly 用原生 newPlot API

**决策**：不用 `react-plotly.js`，直接调 `Plotly.newPlot()` + `React.useRef`。
**理由**：`react-plotly.js` 报 "Cannot call a class as a function"，是已知的类调用 bug。
**影响**：`PlotlyBlock.tsx` 手动管理生命周期；橱窗缩略图用占位提示，展开才渲染三维图。

### D-014 KaTeX 绕开 rehype-katex

**决策**：用 `katex.renderToString()` 直接生成 HTML，不走 `rehype-katex` 插件链。
**理由**：rehype 插件顺序 + sanitize 策略导致 KaTeX 元素被过滤。直接渲染绕开整条处理链。
**影响**：`normalizeLatexMath` 做 `\[...\]` → `$$...$$` 转换；公式块独立加入橱窗。

---

## 运维层

### D-015 迁移 Squashing（68 → 8）

**决策**：0001-0061 压缩为单个 `0001_squashed_baseline`，保留 0062-0068 增量。
**理由**：68 个迁移文件导致空库建库极慢。squash 后基线用 raw SQL 保留 RLS/角色/触发器。
**影响**：空库 `alembic upgrade head` 快 10 倍；`_split_sql()` 处理 psycopg3 async 多语句限制。

### D-016 Worker 健康检查用 worker_process_init signal

**决策**：用 `worker_process_init` 而非 `worker_ready` 启动 9100 端口 healthcheck。
**理由**：`worker_process_init` 比 `worker_ready` 更早触发；prefork 多进程时端口冲突静默跳过。
**影响**：Docker compose healthcheck + restart policy；Beat 用 celerybeat-schedule 文件时间戳检查。

### D-017 .env 值含空格必须加引号

**决策**：`.env` 文件中值含空格的必须用双引号包裹。
**理由**：`IRIP_BOOTSTRAP_ORG_NAME=IRIP 演示组织` 未加引号，`source .env` 时被 shell 解释为命令，配合 `set -e` 导致启动脚本退出。
**影响**：`.env` 和 `.env.example` 已修复；所有启动脚本 `set -a && source .env && set +a`。

---

## 插件层

### D-018 对外仅 2 个 Converter 插件

**决策**：确定性插件 `xrd_converter` + 兜底 `llm_converter`，其他格式走 LLM 分类。原计划 6 个插件合并为 2 个。
**理由**：确定性插件维护成本高（每种格式单独写解析器），LLM 兜底已覆盖大部分格式。确定性插件只给高频且有明确格式的场景（如 XRD）。
**影响**：新增 `raman_converter` 和 `tga_converter` 是后续追加的确定性插件，走相同模式。
