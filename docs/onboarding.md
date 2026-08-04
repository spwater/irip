# IRIP 新人上手指南

> 10 分钟读完这篇，你就知道这个项目是什么、怎么跑起来、关键概念怎么关联。
> 详细架构见 `docs/irip-design-overview.md`，编码规范见 `docs/conventions.md`。

---

## 1. IRIP 是什么

IRIP（Industrial Research Intelligence Platform）是一个**工业研究智能平台**——给实验室用的科研数据管理系统。

核心能力：实验数据采集 → 结构化存储 → 流程编排执行 → AI 分析对话 → 溯源追踪。

技术一句话：FastAPI 模块化单体 + Celery Worker + React 控制台，PostgreSQL 16 为唯一权威存储。

---

## 2. 怎么跑起来

```bash
# 1. Docker 基础设施（PostgreSQL + Redis + MinIO）
cd /Users/shuipei/Desktop/snowSP/irip
docker compose up -d postgres redis minio

# 2. 后端 API（端口 8000）
set -a && source .env && set +a
.venv/bin/uvicorn apps.api.main:app --reload --port 8000

# 3. Celery Worker（异步任务执行）+ Beat（定时调度）
bash start_worker.sh
bash start_beat.sh

# 4. 前端（端口 5173）
bash start_frontend.sh
```

打开 http://localhost:5173，用 `admin@irip.local` / `agsdgfsdg21r34sf` 登录。

**注意**：.env 中 `IRIP_BOOTSTRAP_ADMIN_PASSWORD` 和实际数据库密码可能不一致，以数据库中实际值为准。

---

## 3. 关键概念

### 3.1 数据从哪来：Component（接口/组件）

**Component** 是一个数据提取工具，绑定了实验对象和（可选）设备。

```
Component（接口）
├── 绑定一个 ExperimentalObject（实验对象，如"电池A"）
├── 可选绑定一台 Equipment（设备，如"XRD衍射仪"）
├── 有 manifest.yaml 定义输入参数和输出端口
└── 有版本（发布即不可变，可回滚到旧版本）
```

两种类型：
- **确定性插件**（如 `xrd_converter`）：用代码解析固定格式
- **LLM 插件**（`llm_converter`）：用大模型分类和提取非结构化文本

### 3.2 数据怎么来：Flow（流程/任务）

**Flow** 把一个或多个 Component 串成执行流程。

```
Flow（任务）
├── 关联一个 ExperimentalObject（实验对象）
├── 有版本（发布后可多次执行）
├── 每次执行产生一个 FlowRun
└── FlowRun 的输出可以持久化为 Fact
```

典型流程：上传文件 → Component 解析 → 产出结构化数据 → 存为 Fact。

### 3.3 数据存成什么：Fact（事实）

**Fact** 是一条不可变的实验记录。

```
Fact（事实）
├── fact_type: experiment_run / simulation_run / document_record / model_execution
├── 关联一个 ExperimentalObject
├── 有 FactDataIndex（键值对数据，如 D50=3.2μm）
├── 写入后不可编辑，只能 archive
└── 带快照字段（task_code, operator, equipment_name 等）
```

### 3.4 谁能看到什么：多租户可见性

```
root（公共，= 机构）
 ├── system（系统室，敏感数据，仅管理员可见）
 ├── 实验室A
 │    └── 课题组A1
 └── 实验室B
```

五条规则一句话版：
1. 数据归属部门（`department_id`）
2. 上下互见（父看子孙 + 子看祖先）+ 横向白名单
3. 管理员 = root 成员（不需要特殊角色）
4. 个人私有数据仅 owner 可见（含管理员也不可见），单向不可逆
5. 结构数据（部门树/溯源边/字典）全员可读

### 3.5 AI 能做什么

- **对话**：流式输出，支持 ECharts / Plotly 图表 / KaTeX 公式 / 表格
- **分析橱窗**：把重要内容块收藏到右栏，拖拽排序，定位原文，导出摘要
- **协作**：@提及，参与者可见，副本语义（看过即拥有）
- **chart-ref**：对话中用指令引用数据自动画图

---

## 4. 代码在哪看

### 后端

| 目录 | 看什么 |
|---|---|
| `packages/common/` | 通用内核：ID/时钟/错误/哈希/DB/可见性 |
| `packages/auth/` | 认证授权：用户/会话/角色/权限 |
| `packages/facts/` | 事实管理：CRUD + 搜索 |
| `packages/components/registry/` | 接口注册表：发布/列表/版本 |
| `packages/components/flow/` | 流程引擎：定义/运行/节点 |
| `packages/ai/` | AI 助手：对话/消息/橱窗/协作 |
| `packages/plugins/` | Converter 插件：路由/注册/OCR |
| `apps/api/routers/` | API 路由层 |
| `apps/worker/` | Celery Worker 任务 |
| `migrations/versions/` | Alembic 迁移（0001 基线 + 0062-0068 增量） |

### 前端

| 目录 | 看什么 |
|---|---|
| `src/features/components/FlowDetail.tsx` | 实验执行主页面（**待拆分，1300+ 行**） |
| `src/features/assistant/AssistantPage.tsx` | AI 助手三栏布局（**待拆分，1130+ 行**） |
| `src/features/facts/FactsPage.tsx` | 实验记录 |
| `src/features/governance/` | 治理（用户/审计/备份/部门） |
| `src/shared/ui/` | 共用视觉组件（OceanPanel/PageIntro 等） |
| `src/theme/` | 设计令牌 + Ant Design 主题 |
| `src/styles/` | 全局 CSS（ocean.css / motion.css） |

---

## 5. 改代码前必读

1. **`docs/conventions.md`** — 编码约定，命名规范，错误格式，分层规则
2. **`docs/decision-log.md`** — 关键设计决策，知道"为什么这么写"
3. **`docs/arch-department-tenant.md`** — 多租户模型（改任何数据可见性相关代码前必读）
4. **`docs/todo-list.md`** — 当前技术债和待办

### 几个容易踩坑的地方

- **查询可见性**：不要用 `== self._dept_id` 硬过滤，必须用 `compute_visible_dept_ids()` 做上下对称过滤
- **Worker 需手动重启**：改了 `packages/` 下的代码，Worker 不会自动 reload
- **.env 空格值**：值含空格必须加双引号，否则 `set -e` 脚本会退出
- **fact 不可编辑**：不要给 `FactService` 加 `update()` 方法
- **AI 会话不进部门树**：不要把 `ai_conversation` 加到 `compute_visible_dept_ids` 规则里

---

## 6. 质量检查

```bash
# 后端
.venv/bin/ruff check apps packages tests
.venv/bin/pytest tests/ -x -q

# 前端
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
```

---

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/irip-design-overview.md` | 完整设计概要（哲学/架构/特色功能） |
| `docs/conventions.md` | 编码约定 |
| `docs/decision-log.md` | 关键决策记录 |
| `docs/todo-list.md` | P0-P3 技术债清单 |
| `docs/arch-v0.md` | V0 骨架架构 |
| `docs/arch-department-tenant.md` | 多租户 ADR |
| `docs/arch-ai-showcase.md` | AI 橱窗架构 |
| `docs/arch-converter-refactor.md` | Converter 插件架构 |
| `docs/tideline-design-language.md` | UI 设计语言 |
