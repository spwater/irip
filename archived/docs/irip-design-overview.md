# IRIP 工业研究智能平台 — 设计概要

> 文档日期：2026-08-03
> 项目版本：Alpha v0.8.0
> 仓库：https://github.com/spwater/irip.git
> 适用读者：新成员快速了解项目全貌、架构师回顾设计决策、外部评审了解系统能力

---

## 一、设计哲学

### 1.1 核心原则：能不例外，就不例外

2026-08-02 用户确立的这条原则是 IRIP 一切设计决策的度量尺——每条规则必须具名、有边界；看似特例的东西要么归入一条普适原则，要么删除。评审任何设计时先用这把尺子量。

这一原则在多租户可见性模型中得到了最充分的体现：

- **root 公共数据全员可见**不是特例，而是"对称层级（祖先链向上回溯）"的自然推论——root 是所有部门的祖先，由通道②自动推出。
- **管理员 = root 成员**不是特殊角色，而是层级穿透规则下的推论——root 的后代集 = 整棵树，管理员经通道①自动获得全量可见。
- **溯源链不豁免**——provenance_edge 归入"结构数据全员可读"分类，而非内容规则的"豁免"。
- **个人私有数据**是全系统唯一的具名例外，有明确边界：仅 owner 本人可见（含管理员），单向不可逆，DB 触发器兜底。

### 1.2 数据真实，拒绝伪造

UI 设计文档中反复强调："没有时间序列时不得绘制趋势线""没有全量总数时不得把当前页 items 数标成'总数'""空值、零值、未知和请求失败必须使用不同表达"。这是科研数据平台的底线——数据的准确性、可追溯性高于一切视觉效果。

### 1.3 克制的未来感

界面表达三个词：**流动、深邃、克制**。通过细线、轻磨砂、低透明度关系轨迹和精确数字表达未来感；不得使用高频闪烁、大量粒子、粗发光边框、持续旋转或大面积霓虹。装饰稀缺性原则——一种签名元素全平台只用一次，出现一次是签名，出现四次是贴纸。

### 1.4 后端务实，前端克制

- **后端**：代码/API/字段英文，UI 中文；UTC timestamptz；UUID 主键。采用"FastAPI 模块化单体 + Celery Worker + React 控制台"经典三段式，以 PostgreSQL 16 为唯一权威存储。不盲目追求微服务化，保持架构简单可靠。
- **前端**：不更换 React、Ant Design、TanStack 等技术栈；不借 UI 升级重构业务逻辑；UI 迁移允许重组页面内部布局，但不改变路由、API、权限、业务字段或核心操作流程。

### 1.5 渐进式演进

项目从 V0 骨架起步，逐步增加业务模块，持续清理技术债。迁移文件从 68 个 squash 到 8 个，DB 清理分批次删除冗余表，多租户改造分三阶段迁移——始终保持"可随时中止、可快速回滚"的演进策略。

---

## 二、设计架构

### 2.1 总体架构

```
┌─────────┐   HTTPS    ┌──────────────┐   SQL    ┌────────────────┐
│  React  │ ◄────────► │   FastAPI    │ ◄──────► │  PostgreSQL 16 │
│  (Web)  │            │   (API 单体) │          │  + pgvector    │
└─────────┘            └──────┬───────┘          └───────△────────┘
                              │                          │
                              │ S3 (boto3)               │ 租约/Outbox
                              ▼                          │
                       ┌──────────────┐                  │
                       │   MinIO      │                  │
                       │ (内容寻址)    │                  │
                       └──────────────┘                  │
                              ▲                          │
                              │ Celery (Redis broker)    │
                       ┌──────┴───────┐                  │
                       │ Celery Worker│ ◄────────────────┘
                       └──────────────┘
```

**经典三段式**：FastAPI 模块化单体 + Celery Worker + React 控制台。PostgreSQL 16 为唯一权威存储，通过 Outbox 模式 + 幂等提交保证异步作业的可恢复性。

### 2.2 技术栈

| 层 | 技术选型 | 说明 |
|---|---|---|
| **后端语言** | Python 3.12+ | 放开上限支持 3.13 |
| **Web 框架** | FastAPI 0.115+ | 异步、原生 OpenAPI |
| **ORM/迁移** | SQLAlchemy 2.0 (async) + psycopg 3 + Alembic | |
| **任务队列** | Celery 5.4 + Redis 7 | Redis 仅作 broker，不作权威存储 |
| **对象存储** | MinIO (S3 兼容, boto3) | 内容寻址去重 |
| **数据库** | PostgreSQL 16 + pgvector | 唯一权威存储 |
| **认证** | PyJWT + Argon2id + refresh token 家族旋转 | |
| **日志** | structlog | 结构化 JSON 输出 |
| **前端框架** | React 18 + TypeScript 5.7 | |
| **构建** | Vite 5 | |
| **UI 组件** | Ant Design 5 | 中文优先 |
| **路由/数据** | TanStack Router + TanStack Query | |
| **状态管理** | Zustand | 轻量内存态 |
| **图表** | ECharts 5 + Plotly | 科研图表用 Plotly |
| **关系图** | React Flow 11 | 溯源可视化 |
| **包管理** | uv (后端) / pnpm 11 (前端) | |
| **测试** | pytest / Vitest + Testing Library + Playwright | |
| **镜像源** | pip 中科大 / npm 阿里云 | 国内加速 |

### 2.3 后端分层架构

```
entities.py (ORM 层) → repository.py (数据访问层) → service.py (业务编排层) → routers/*.py (API 层)
```

- 值对象（dataclass frozen）在服务层与 API 层之间传递
- Composition Root 依赖注入（ApplicationContainer）
- 每模块独立 packages/<domain>/，遵循 entities → repository → service 模式
- 迁移管理：Alembic，当前 13 个文件（squash 后 0001 基线 + 0062-0073 增量）

### 2.4 前端四层 UI 架构

```
业务页面层  — 现有页面、hooks、TanStack Query、Router、权限和表单逻辑
    ↓ 使用
共用组件层  — PageIntro、DataHero、OceanPanel、DataTableShell、StatusMark 等
    ↓ 映射
视觉基础设施层 — 背景、内容框架、全局 CSS、动效、响应式、图表主题
    ↓ 基于
主题基础层  — Data Ocean / 潮线 Tideline 语义令牌 + Ant Design ConfigProvider
```

共用视觉组件不直接调用业务 API，不保存服务端实体，不自行判断业务权限——只接收展示属性并输出结构和样式。

### 2.5 核心技术挑战与应对

| 挑战 | 应对策略 |
|---|---|
| 异步作业可恢复 | PostgreSQL Outbox + 唯一幂等键 + Worker 租约（含过期与心跳）+ 同事务提交 |
| 权限细粒度 | RBAC 7 角色 + department 树层级可见性 + 横向白名单 + 个人私有 |
| 刷新令牌安全 | 仅持久化 SHA-256 摘要 + 家族 ID + 单用途旋转 + 重放即家族撤销 |
| 审计不可篡改 | audit_event 仅追加；应用数据库角色 REVOKE UPDATE, DELETE |
| 大文件去重 | MinIO 对象键 sha256/<前2位>/<digest>，blob 表与 artifact 表分离 |
| 时钟一致性 | Clock Protocol 注入；生产 SystemClock，测试 FixedClock；全 UTC |
| Worker 健康 | worker_process_init signal 自动启动 9100 端口 /health；Docker healthcheck |

### 2.6 数据接口提取标准

统一三类固定结构，所有 converter 插件输出格式一致：

```json
{
  "metadata": {},      // 标头单值
  "points": [],        // 每行一条 fact_data_index
  "series": []         // 整组一条 observation
}
```

### 2.7 多租户可见性模型

IRIP 的多租户隔离从 organization 迁移到 department，形成了完整的可见性体系：

```
root（根部门 = 原 organization 哨兵）
 ├── system（系统室 = 敏感数据专区，仅 root 成员可见）
 ├── 实验室 A
 │    └── 课题组 A1
 └── 实验室 B
```

**五条核心规则：**

1. **归属**：每张数据表带 department_id NOT NULL，归属稳定不随树调整变化
2. **可见性 = 对称层级 + 横向白名单**：
   - 向下穿透：父部门自动可见子孙部门数据
   - 向上回溯：每个部门可见直系祖先链数据
   - 横向白名单：旁系部门间通过 visible_departments JSONB 精确匹配
3. **管理员 = root 成员**：无需 is_admin 开关或 bypass 角色
4. **个人私有数据**：visibility_scope + owner_user_id，仅 owner 可见，单向不可逆（private → tree），DB 触发器兜底
5. **结构数据全员可读**：department 树、provenance_edge、object_relation、字典——结构/内容二分类

**管理权模型**：所有者 + 上级向下。数据所有者可管理自己的；上级部门管下级（严格后代不含本部门）；同部门非所有者只有信息权没有管理权；root/平台管理员不受限。

### 2.8 Converter 插件化架构

```
调用方（ez_scan_extractor / component_preview）
    │
    ├─ 直接调用：registry.get(tool_type).execute(params)     ← 现有模式
    │
    └─ 路由调用：router.route_and_convert(file_path, params)  ← 新增
         │
         ├─ 后缀映射 → 专用插件（xrd_converter 确定性）
         ├─ 专用插件成功 → 返回
         └─ 专用插件失败 → fallback 到 llm_converter（LLM 分类兜底）
```

- 每插件一个文件 `converters/<name>/converter.py` + `__init__.py`
- 对外仅 2 个插件：`xrd_converter`（确定性）+ `llm_converter`（LLM 兜底）
- 公共模块：`common/text_extractor.py`（图片走 PaddleOCR、PDF 文字不足走 OCR）+ `common/llm_utils.py`
- 新增插件三步：写 converter.py → registry.py 注册 → ai_tool 表插 category=ingestion；主系统零改动

---

## 三、特色功能

### 3.1 AI 助手分析橱窗

三栏布局的 AI 对话体验：左栏对话列表（260px）+ 中栏消息流（flex）+ 右栏分析橱窗（360px，可收起至 48px）。

**核心能力：**
- **内容块化渲染**：AI 回复中的 ECharts 图表、Plotly 科研图表、表格、结论、公式（KaTeX）均作为独立可操作块，每个块右上角有悬浮操作按钮
- **一键加入橱窗**：将重要内容块收藏到右栏橱窗，持久化存储（ai_showcase_item 表），切换对话自动恢复
- **拖拽排序**：@dnd-kit 实现橱窗卡片拖拽，乐观更新 + 失败回滚
- **定位原文**：点击橱窗卡片可滚动定位到原始消息位置并高亮 2.5 秒
- **摘要导出**：一键生成 Markdown 摘要，可复制、可下载 .md
- **Plotly 科研图表**：支持误差棒图、箱线图、三维散点图、热力图等 ECharts 难以胜任的科研图表
- **块去重**：后端唯一索引 (conversation_id, source_message_id, source_block_index) 防重复加入

### 3.2 AI 协作功能

- **@提及**：conversation_participant 表支持多人协作，ai_message 加 mentions + sender 冗余字段
- **app_user 加 avatar_url**：用户头像支持
- **轮询通知**：30s/10s 轮询通知机制
- **副本语义（copy-on-share）**：消息文本与橱窗块为发送时副本，带进会话 = 向所有参与者公开副本；不随源数据更新而刷新，不可回收
- **AI 会话不进部门树**：可见者 = 创建者 + 参与者，避免上级链翻看所有 AI 对话

### 3.3 数据库备份系统

**v1 pg_dump 方案**：
- backup_record 表 + ORM + Service + 6 API + Celery beat + 前端 /governance
- 三种备份类型：daily（14天）/ milestone（永久）/ pre_restore（7天）
- 回滚前自动 pre_restore 备份

**v2 PITR 升级**：
- pg_basebackup + WAL 归档 + mc mirror 联合备份
- manifest format_version v1/v2 路由
- 联合恢复：MinIO → PG
- archive_command 幂等
- 前端备份方法标签（pitr=蓝色 / pg_dump=灰色）+ 恢复时间点选择器

### 3.4 潮线 Tideline 设计语言

**水光版（v2）**——数据之海的第二版视觉语言：

- **审美红线**：不要斜切硬几何（clip-path/skew 一律禁止）、不要大面积实色块撞色、一种签名装饰全平台只用一次
- **色彩**：ocean.abyss（#0B4A6F 深潮蓝，仅文字/线条/渐变）+ ocean.current（#17B8CE/#4FE0EC 潮流青，数据流与光泽）+ action 主色 #0E5B84
- **签名类**：.ocean-watermark（大号描边水印）/ .ocean-flow-text（渐变文字）/ .ocean-title-ribbon / .ocean-water-glass（水光玻璃）/ .ocean-sider-menu / .ocean-tide-enter（阶梯入场动画 d1-d4）
- **特色组件**：GradLine（渐变直线，左深右浅）、EcgLine（心电图，stretch 大图模式）、SiderClock（侧栏大号时钟，27px 时间 + 12px 日期）
- **导航选中**：水光玻璃 + 左缘圆头渐变条；页签圆润胶囊
- **登录页**：DATA/OCEAN 双水印 + 渐变 IRIP 大字 + 圆角玻璃卡 + 渐变按钮

### 3.5 科研数据摄入向导

IngestionWizard 5 步流程（从 7 步精简），支持：
- 文件上传 + Prompt 推荐 + 预览 + 发布 + 归档
- 版本操作与回滚
- 组件版本支持 active_version_id 回滚 + 执行窗口 prompt 动态加载

### 3.6 流程编排引擎

- 可视化流程定义与执行
- 运行中状态使用 FlowTrack 和真实 Progress
- 失败节点显示原因、时间和可执行操作
- 批量执行、取消、恢复、重试
- 事实自动持久化（persist_run_as_fact）

### 3.7 溯源体系

- provenance_edge + derivation_run 构建血缘关系图
- React Flow 可视化节点和边
- 保留结构化表格作为准确读取和可访问性回退
- 不可见端点显示"无权限节点"占位而非断链
- 结构数据全员可读，内容数据按归属受控

### 3.8 迁移 Squashing

68 个迁移文件压缩后当前 13 个（0001_squashed_baseline + 0062-0073），空库重建速度快 10 倍。基线 1511 行包含：
- 40 表 / 65 索引 / 3 扩展（citext, pgcrypto, vector）/ 4 角色
- 62 RLS 策略配置 / 89 GRANT 语句 / 1 触发器函数

### 3.9 Worker 健康检查与自动恢复

- `worker_process_init` signal 自动启动 9100 端口 `/health`
- Docker compose healthcheck + restart policy
- 独立启动脚本 `start_worker.sh` / `start_beat.sh` 自动加载 .env
- Beat 健康检查用 celerybeat-schedule 文件时间戳

---

## 四、数据表分类与模块概览

### 4.1 数据表四分类（多租户改造后）

| 分类 | 特征 | 代表表 |
|---|---|---|
| **A 类** | 补 department_id + visible_departments（归属 + 可共享） | fact, parameter, evidence_set, artifact, model, component, flow_definition, industrial_object, equipment |
| **B 类** | 只补 department_id（归属，不开放共享） | job, flow_run, derivation_run, audit_event, secret, backup_record, connector 各表, app_user |
| **C 类** | 结构数据，全员可读，无租户列 | provenance_edge, object_relation, object_type_dict, department 自身 |
| **D 类** | 退役 | organization 表 + 所有 organization_id 列 |

### 4.2 后端模块（packages/）

| 模块 | 职责 |
|---|---|
| common | 通用内核：ID/时钟/错误/哈希/分页/数据库/工件 |
| auth | 认证 + 授权：用户/会话/角色/权限 |
| audit | 审计：仅追加事件 + 脱敏 |
| jobs | 异步作业：Outbox + 租约 + 幂等 |
| facts | 事实管理：CRUD + 搜索 + FactDataIndex |
| parameters | 参数管理：版本 + 审批 + 证据 |
| provenance | 溯源：血缘图 + 证据集 + 推导 |
| models | 模型管理：全链路 + 执行 |
| ai | AI 助手：对话 + 消息 + 橱窗 + 协作 |
| flows | 流程引擎：定义 + 运行 + 节点 |
| connectors | 数据摄入：ingestion_service |
| plugins | 转换器插件：router + registry + tools |

### 4.3 前端导航

| 路由 | 模块 | 说明 |
|---|---|---|
| /standards | 研发看板 | 组织机构 / 设备仪器 / 实验对象 |
| /lab-ops | 实验室运营 | 实验执行 / 实验记录 / 数据接口 |
| /platform | 平台应用 | AI 助手 / AI 工具 / 数据接口 |
| /governance | 治理 | 系统配置 / 用户管理 / 审计事件 / 作业中心 / 备份管理 / 数据移交 |

---

## 五、已知技术债与远期计划

### 5.1 当前技术债

- 多租户端到端验证待完成（代码已实现，API/前端未端到端测试）
- derivation_run 未接通 fact_data_index（values 空、parameter_version 值全 0）
- model 前端无路由注册（页面存在但 router.tsx 未注册）
- AssistantPage.tsx（1130 行）和 ai/service.py（1900+ 行）需拆分
- 消息列表 3 秒轮询待换 WebSocket/SSE
- system_context 全量传 LLM，大数据量 series 待按需传递

### 5.2 远期计划

**周期性知识图谱**：非实时，周期性从 DB 提取关系独立存储。起步用 PG 独立表（graph_node + graph_edge），数据量大了再迁 Neo4j。关系类型：统计/相似/溯源/文献。触发条件：数据量上千、用户问"A 和 B 什么关系"时落地。

**RLS 正式启用**：目前 irip 角色 bypass RLS，应用层 .in() 过滤是唯一防线。生产环境应启用 RLS 双保险，按 ADR 三阶段迁移从 organization_id 键切换到 department_id 键。

---

## 六、关键设计文档索引

| 文档 | 路径 | 说明 |
|---|---|---|
| V0 系统架构 | `docs/arch-v0.md` | 平台骨架设计：认证/授权/审计/工件/作业 |
| Fact 版本链清理 | `docs/system_design.md` | 0055 迁移：删 7 表 + 字段合并 + FK 改造 |
| 多租户 ADR | `docs/arch-department-tenant.md` | department 扶正为隔离键、organization 退役 |
| AI 橱窗架构 | `docs/arch-ai-showcase.md` | 三栏布局 + 块化渲染 + 橱窗持久化 |
| AI 协作架构 | `docs/arch-ai-collab.md` | 参与者 + @提及 + 副本语义 |
| Converter 重构 | `docs/arch-converter-refactor.md` | 插件化 + PaddleOCR + 路由 fallback |
| DB 备份 v1 | `docs/arch-db-backup.md` | pg_dump 方案 |
| DB 备份 PITR | `docs/arch-db-backup-pitr-upgrade.md` | pg_basebackup + WAL 归档 |
| Data Ocean UI v1 | `2026-07-28-data-ocean-ui-upgrade-design.md` | 极地雾蓝设计语言 |
| 潮线 Tideline v2 | `docs/tideline-design-language.md` | 水光版设计语言（取代 v1） |
| TODO List | `docs/todo-list.md` | P0-P3 任务清单 |
