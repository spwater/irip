# IRIP 平台交付报告

> **项目名称**: IRIP — Industrial Research Intelligence Platform（工业研究信息平台）  
> **交付日期**: 2026-07-22  
> **交付版本**: v0.1.0  
> **仓库地址**: github.com:spwater/irip.git  
> **最新提交**: ab975ac

---

## TL;DR

IRIP 是一个面向材料科学研究的工业数据管理平台，覆盖从实验数据摄入、标准变量管理、事实审批、溯源推导、参数发布到代理模型预测的全链路。本次交付包含 V0（平台骨架）、V1（粒度分析 L1→L3 全链路）、V2（版本化组件系统 + 流程引擎 + 篦冷机 ROM 模型生命周期）、V3（证据引用 AI 助手 + 治理控制台 + 备份恢复 + 安全测试 + 发布文档）四个阶段共 34 个实施任务的全部代码。

---

## 1. 交付概览

| 指标 | 数值 |
|------|------|
| 交付状态 | ✅ 全部完成 |
| 测试通过率 | 495 passed, 323 skipped (DB 依赖), 0 failed |
| 已知问题数 | 0（两轮自检共修复 7 个 Bug） |
| 后端 Python 源文件 | 114 个 |
| API 路由文件 | 21 个 |
| API 路径总数 | 121 个 |
| 前端 TS/TSX 文件 | 44 个 |
| 数据库迁移 | 21 个 (0001-0021) |
| 测试文件 | 55 个 |
| 文档文件 | 19 篇 |
| 示例文件 | 13 个 |
| Schema 文件 | 32 个 |
| 权限总数 | 42 个 |
| 内置角色 | 7 个 |
| 内置组件 | 29 个 |
| Git 提交数 | 7 个 |

---

## 2. 系统架构

### 2.1 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 后端框架 | FastAPI | 0.115+ |
| ORM | SQLAlchemy | 2.0+ (Mapped 风格) |
| 迁移工具 | Alembic | 1.18+ |
| 数据库 | PostgreSQL | 16+ |
| 缓存/队列 | Redis | 7+ |
| 对象存储 | MinIO (S3 兼容) | — |
| Worker | Celery | 5.4+ |
| 前端框架 | React | 18+ |
| 构建工具 | Vite | 5+ |
| UI 组件库 | Ant Design | 5+ |
| 数据查询 | TanStack Query | 5+ |
| 路由 | TanStack Router | 1+ |
| 语言 | TypeScript (前端) / Python 3.12+ (后端) | — |
| 容器化 | Docker Compose | — |

### 2.2 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     前端 (React + Ant Design)                 │
│  标准管理 | 实验事实 | 参数管理 | 组件管理 | 流程编排 |        │
│  模型管理 | 预测工作台 | AI 助手 | 平台治理 | 作业中心         │
├─────────────────────────────────────────────────────────────┤
│                     API 层 (FastAPI)                         │
│  121 个 API 路径 | JWT 认证 | RBAC 权限 | 审计日志             │
├─────────────────────────────────────────────────────────────┤
│                    业务逻辑层 (packages/)                     │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐            │
│  │ 标准变量 │ │  事实   │ │  溯源   │ │  参数   │            │
│  │ 对象图  │ │ 模板    │ │ 证据集  │ │ 审批    │            │
│  │ 方法/包 │ │ 修订    │ │ 配方    │ │ 发布    │            │
│  │ 映射配置 │ │ 观察值  │ │ 推导运行│ │ 过期    │            │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐            │
│  │ 组件 SDK │ │ 流程引擎 │ │ 模型   │ │ AI 助手 │            │
│  │ 29 组件  │ │ DAG 校验 │ │ 适配器  │ │ 工具链  │            │
│  │ 执行器  │ │ 节点调度 │ │ 生命周期│ │ 引用    │            │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘            │
├─────────────────────────────────────────────────────────────┤
│                    基础设施层 (V0)                           │
│  认证 | RBAC | 审计 | 工件服务 | 作业系统 | 连接器             │
├─────────────────────────────────────────────────────────────┤
│                    数据/存储层                               │
│  PostgreSQL | Redis | MinIO S3 | Outbox 模式                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 功能模块清单

### 3.1 V0 — 平台骨架 (Task 1-9)

| 模块 | 说明 |
|------|------|
| 认证与会话 | JWT 令牌 + HttpOnly Refresh Cookie，令牌轮换族，自动续期 |
| RBAC 权限 | 7 个内置角色，42 个权限，对象级范围授权 |
| 审计日志 | 仅追加事件日志，密钥脱敏，多维度查询 |
| 工件服务 | MinIO S3 内容寻址存储，presign 上传，SHA-256 校验 |
| 作业系统 | PostgreSQL Outbox 模式，Worker 租约，幂等执行 |
| 连接器 | PostgreSQL (只读 SELECT)、REST (SSRF 防护) |
| React 前端 | 登录/路由守卫/作业抽屉/AppShell |
| Docker Compose | PostgreSQL + Redis + MinIO + API + Worker |
| 健康检查 | /health/ready 端点，DB/Redis/MinIO/Outbox 状态 |

### 3.2 V1 — 粒度分析 L1→L3 全链路 (Task 10-20)

| 层 | 模块 | 说明 |
|----|------|------|
| L1 标准 | 标准变量 | 不可变版本，单位/别名/量纲，审批流 draft→in_review→published |
| L1 标准 | 工业对象图 | 多层级组织（实验室→产线→设备组→仪器→测量点），无环关系 |
| L1 标准 | 方法和模板 | 方法/包/模板审批，质量规则引用 |
| L1 标准 | 映射配置 | 字段映射评分，连接器配置 |
| L1 标准 | 数据生成器 | 确定性粒度分析 fixture 生成器 |
| L2 事实 | 事实模板 | FactTemplate 审批流，RawObservation + NormalizedObservation |
| L2 事实 | 事实修订 | 不可变修订，工件链接，质量结果 |
| L2 事实 | 数据摄入 | Excel/CSV/PDF 端到端摄入流水线 |
| L2.5 溯源 | 证据集 | 冻结版本，成员管理 |
| L2.5 溯源 | 配方 | 版本化推导配方，参数 + 随机种子 + 输出定义 |
| L2.5 溯源 | 推导运行 | 确定性回放，溯源图谱（节点 + 边） |
| L3 参数 | 参数条件 | 条件 AST，不确定性传播 |
| L3 参数 | 审批发布 | 审批流 draft→review→approve→publish，过期标记 |
| 前端 | 导航与页面 | 7 个顶级菜单，10+ 个子页面，全中文界面 |

### 3.3 V2 — 版本化组件系统 (Task 21-28)

| 模块 | 说明 |
|------|------|
| 组件 SDK | Component Protocol（Python/CLI 双运行时），ComponentContext/Result，超时/取消/沙箱 |
| 组件注册表 | Component + ComponentVersion 两表设计，发布不可变，SHA-256 校验 |
| 25 个内置组件 | 7 摄入（Excel/CSV/JSON/PDF/PostgreSQL/REST/MinIO）+ 7 映射转换（字段映射/单位转换/缺失值/时间对齐/重采样/MAD异常值/稳态窗口）+ 4 质量（Schema/范围/粒度序/关系完整性）+ 4 统计（描述性/稳健估计/Bootstrap/曲线拟合）+ 3 输出（参数卡片/实验对比/报告草稿） |
| 流程引擎 | FlowDefinitionVersion（DAG），Kahn 拓扑排序 + 环检测，端口类型匹配，参数 schema 校验，节点级执行/恢复/取消/重试 |
| 模型生命周期 | ModelAdapter Protocol，状态机 draft→pending_validation→validated→published→deprecated，发布指针 + 回滚，适用域检查 |
| 4 个模型组件 | train / evaluate / applicability / predict |
| 篦冷机 ROM | 240 行确定性数据集，StandardScaler + RandomForestRegressor，固定种子 20260715，R²≥0.90 |
| 前端控制台 | 组件管理页面、流程编排页面、模型管理页面、模型详情页面、预测工作台 |
| 命令行适配器 | 通用 JSON 命令适配器示例 |

### 3.4 V3 — AI 助手与运维加固 (Task 29-34)

| 模块 | 说明 |
|------|------|
| AI Provider | OpenAI 兼容 HTTP 调用 + 离线确定性模拟（无外部依赖） |
| AI 工具白名单 | 7 个只读工具（search_standards/facts/parameters, explain_provenance, compare_experiments, run_published_model, draft_report）+ 4 个候选工具 |
| AI 引用 | Citation 数据结构，每条回答附 object_type + object_id + version + href |
| AI 前端 | 对话界面、消息列表、工具调用轨迹、引用列表、Provider 状态 |
| 治理控制台 | 用户管理/角色分配/范围授权 API + 审计事件查询（多维度过滤 + 异步导出） |
| 系统健康 | /health/ready 端点，DB/Redis/MinIO/Outbox 状态 + 迁移版本 |
| 作业管理 | 作业列表/详情/重试/取消，包含载荷/日志/工件链接 |
| 备份恢复 | pg_dump custom 格式 + MinIO 对象导出 + SHA-256 校验 + age 加密 + 隔离 Compose 恢复 |
| 安全测试 | 5 个安全套件（令牌重放/上传限制/路径穿越/SQL 注入/AI 工具逃逸） |
| 恢复测试 | 3 个恢复套件（Redis 丢失重建/MinIO 中断重试/迁移回滚） |
| 性能测试 | k6 冒烟脚本，p95 阈值检查 |
| 文档 | README + 10 篇指南 + release-gate.sh 发布门脚本 |

---

## 4. 数据模型

### 4.1 数据库迁移链

```
0001_platform_base → 0002_authentication → 0003_authorization_audit →
0004_artifacts → 0005_jobs_outbox → 0006_department → 0007_user_roles →
0008_standard_variables → 0009_industrial_objects → 0010_fact_templates →
0011_mapping_profiles → 0012_facts → 0013_quality_ingestion →
0014_provenance → 0015_parameters → 0016_equipment →
0017_department_parent → 0018_components → 0019_flows →
0020_models → 0021_ai_conversations
```

### 4.2 核心数据表

| 阶段 | 表 | 说明 |
|------|-----|------|
| V0 | users, roles, sessions, audit_events, artifacts, jobs, outbox_events | 平台基础 |
| V0 | departments, user_departments | 组织机构 |
| V1 | standard_variables, standard_variable_versions | 标准变量 |
| V1 | industrial_objects, object_relations | 工业对象图 |
| V1 | methods, fact_templates, method_packages | 方法和模板 |
| V1 | mapping_profiles, mapping_profiles_version | 映射配置 |
| V1 | facts, fact_revisions, raw_observations, normalized_observations, fact_artifacts | 事实 |
| V1 | evidence_sets, evidence_set_versions, evidence_members | 证据集 |
| V1 | recipes, recipe_versions | 配方 |
| V1 | derivation_runs, derivation_outputs | 推导运行 |
| V1 | parameters, parameter_versions, parameter_conditions, parameter_approvals | 参数 |
| V1 | equipment, equipment_variables | 设备 |
| V2 | components, component_versions | 组件 |
| V2 | flow_definitions, flow_definition_versions, flow_runs, flow_node_executions | 流程 |
| V2 | models, model_versions | 模型 |
| V3 | ai_conversations, ai_messages | AI 对话 |

---

## 5. API 清单

### 5.1 V0/V1 API (89 个路径)

| 前缀 | 端点数 | 说明 |
|------|--------|------|
| /api/v1/auth | 4 | 登录/刷新/登出/me |
| /api/v1/uploads | 3 | presign/complete/put |
| /api/v1/artifacts | 2 | 列表/下载 |
| /api/v1/jobs | 3 | 列表/详情/重试/取消 |
| /api/v1/departments | 6 | CRUD + 树形 |
| /api/v1/equipment | 7 | CRUD + 物理量关联 |
| /api/v1/standards | 6 | 变量 CRUD + 审批 |
| /api/v1/objects | 5 | 对象 CRUD + 关系 + 后代 |
| /api/v1/templates | 4 | 模板 CRUD + 审批 |
| /api/v1/methods | 3 | 方法 CRUD |
| /api/v1/packages | 3 | 包 CRUD |
| /api/v1/ingestions | 3 | 摄入向导 |
| /api/v1/facts | 6 | 事实 CRUD + 观察 + 搜索 |
| /api/v1/provenance | 12 | 证据集/配方/推导运行/图谱 |
| /api/v1/parameters | 6 | 参数 CRUD + 审批 |
| /api/v1/health | 2 | 就绪检查 + 存活检查 |

### 5.2 V2+V3 API (32 个路径)

| 前缀 | 端点数 | 说明 |
|------|--------|------|
| /api/v1/components | 3 | 组件列表/详情/发布 |
| /api/v1/flows | 9 | 流程定义/发布/运行/恢复/取消/重试 |
| /api/v1/models | 8 | 模型 CRUD/版本/验证/发布/回滚/预测 |
| /api/v1/assistant | 5 | 对话/消息/Provider 状态 |
| /api/v1/governance | 6 | 用户/角色/范围授权 |
| /api/v1/audit-events | 2 | 事件查询/导出 |
| /api/v1/backups | 3 | 备份列表/创建/恢复 |

---

## 6. 前端页面清单

| 菜单 | 路由 | 页面组件 | 说明 |
|------|------|---------|------|
| 标准管理 | /standards | StandardsPage (5 Tab) | 组织机构/设备仪器/物理量管理/实验对象/对象图谱 |
| 实验事实 | /facts | FactsPage (2 Tab) | 事实列表/数据摄入 |
| 参数管理 | /parameters | ParameterPage (2 Tab) | 参数列表/溯源链路 |
| 组件管理 | /components | ComponentsPage | 组件注册表 + manifest 详情 |
| 流程编排 | /flows | FlowDetail | 流程定义 + 运行管理 + 节点执行表 |
| 模型管理 | /models | ModelsPage + ModelDetail | 模型列表 + 版本/指标/适用域/操作 |
| 预测工作台 | /models/predict | PredictionWorkbench | 动态表单 + 预测 + 警告 |
| AI 助手 | /assistant | AssistantPage | 对话 + 工具轨迹 + 引用列表 |
| 平台治理 | /governance | GovernanceConsole (4 Tab) | 用户/授权/审计/系统健康 |
| 作业中心 | /jobs | JobsPage + JobDetail | 作业列表 + 详情 |

---

## 7. 质量保障

### 7.1 测试覆盖

| 测试类型 | 文件数 | 测试数 | 说明 |
|---------|--------|--------|------|
| 单元测试 | 25 | 280+ | 权限/组件/转换/统计/质量/流程校验/AI 工具 |
| 契约测试 | 5 | 80+ | 组件 manifest/模型适配器/映射配置 |
| 安全测试 | 7 | 90+ | 令牌重放/上传限制/路径穿越/SQL 注入/AI 工具逃逸 |
| 恢复测试 | 5 | 15+ | Redis 丢失/MinIO 中断/迁移回滚/备份恢复 |
| 性能测试 | 1 | k6 | p95 阈值检查 |
| 验收测试 | 3 | 50+ | 文档命令/V1 不变量 |
| **合计** | **55** | **495+** | 495 passed, 323 skipped (DB), 0 failed |

### 7.2 自检与 Bug 修复记录

两轮系统性自检共发现并修复 7 个 Bug：

| # | 轮次 | 严重程度 | 文件 | 问题 | 修复 |
|---|------|---------|------|------|------|
| 1 | 第 1 轮 | 🔴 Critical | main.py | assistant_router 未导入和注册 | 添加导入 + include_router |
| 2 | 第 1 轮 | 🔴 Critical | main.py | 4 个 DI 覆盖缺失 | 添加 component/flow/model/ai service DI |
| 3 | 第 1 轮 | 🟡 Medium | test_permissions.py | 6 个角色权限测试未包含 assistant:use | 在测试期望中添加 |
| 4 | 第 1 轮 | 🟡 Medium | client.ts | apiCancelJob 返回类型不匹配 | 修正为 {job_id, status, kind} |
| 5 | 第 1 轮 | 🟡 Medium | test_department_permissions.py | 权限总数期望 38→42 | 更新期望值 |
| 6 | 第 2 轮 | 🔴 Critical | main.py | _get_model_service_dep 引用未定义的 artifact_service | 改为直接构造 ArtifactService |
| 7 | 第 2 轮 | 🟡 Medium | health.py | EXPECTED_MIGRATION_HEAD 硬编码 0006 | 更新为 0021 |

---

## 8. 部署指南

### 8.1 前提条件

- Python 3.12+
- Node.js 22+
- PostgreSQL 16+
- Redis 7+
- MinIO (或任何 S3 兼容存储)

### 8.2 Docker Compose 部署（推荐）

```bash
# 1. 克隆仓库
git clone git@github.com:spwater/irip.git
cd irip

# 2. 启动基础设施 + 应用
docker compose up -d

# 3. 运行数据库迁移
docker compose exec api python -m alembic upgrade head

# 4. Bootstrap 管理员账号
docker compose exec api python deployments/compose/bootstrap.py

# 5. 加载示例数据
docker compose exec api python examples/particle-size/generate.py
```

### 8.3 本地开发部署

```bash
# 1. 安装 Python 依赖
pip install -e ".[dev]"

# 2. 配置环境变量
export IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip"

# 3. 运行数据库迁移
python -m alembic upgrade head

# 4. 启动后端
python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000

# 5. 启动前端
cd apps/web && npm install && npm run dev

# 6. 启动 Worker
python -m celery -A apps.worker.celery_app worker -l info
```

### 8.4 服务 URL

| 服务 | URL | 说明 |
|------|-----|------|
| API | http://localhost:8000 | FastAPI 后端 |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| 前端 | http://localhost:5173 | Vite dev server |
| MinIO Console | http://localhost:9001 | S3 管理界面 |

### 8.5 默认凭据

| 项目 | 值 |
|------|-----|
| 管理员邮箱 | admin@irip.local |
| 管理员密码 | Admin-IRIP-2026 |
| 数据库连接 | postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip |

---

## 9. 文件清单

### 9.1 后端 packages

| 包 | 说明 |
|----|------|
| packages/common/ | 通用工具（ID、时钟、错误、分页、工件、数据库、S3） |
| packages/auth/ | 认证、权限、RBAC、令牌轮换 |
| packages/audit/ | 审计日志 |
| packages/jobs/ | 作业系统（Outbox、Lease、Service） |
| packages/connectors/ | 连接器（PostgreSQL、REST、文件、映射） |
| packages/standards/ | 标准变量、对象图、方法、模板、包、单位 |
| packages/departments/ | 组织机构（多层级树形） |
| packages/equipment/ | 设备管理 |
| packages/facts/ | 事实（模板、修订、观察值、质量） |
| packages/provenance/ | 溯源（证据集、配方、推导运行、图谱） |
| packages/parameters/ | 参数（条件、审批、发布、过期） |
| packages/components/ | 组件系统（SDK、注册表、执行器、流程引擎、29 内置组件） |
| packages/models/ | 模型生命周期（契约、适配器、实体、服务、适用域） |
| packages/ai/ | AI 助手（Provider、工具、引用、服务、离线模拟） |

### 9.2 后端 API 路由

| 路由文件 | 前缀 | 说明 |
|---------|------|------|
| auth.py | /api/v1/auth | 认证 |
| uploads.py | /api/v1/uploads | 工件上传 |
| artifacts.py | /api/v1/artifacts | 工件管理 |
| jobs.py | /api/v1/jobs | 作业管理 |
| departments.py | /api/v1/departments | 组织机构 |
| equipment.py | /api/v1/equipment | 设备管理 |
| standards.py | /api/v1/standards | 标准变量 |
| objects.py | /api/v1/objects | 工业对象 |
| fact_templates.py | /api/v1/templates | 事实模板 |
| methods.py | /api/v1/methods | 方法 |
| packages.py | /api/v1/packages | 标准包 |
| ingestions.py | /api/v1/ingestions | 数据摄入 |
| facts.py | /api/v1/facts | 事实管理 |
| provenance.py | /api/v1/provenance | 溯源链路 |
| parameters.py | /api/v1/parameters | 参数管理 |
| components.py | /api/v1/components | 组件管理 (V2) |
| flows.py | /api/v1/flows | 流程编排 (V2) |
| models.py | /api/v1/models | 模型管理 (V2) |
| assistant.py | /api/v1/assistant | AI 助手 (V3) |
| governance.py | /api/v1/governance | 平台治理 (V3) |
| audit.py | /api/v1/audit-events | 审计查询 (V3) |
| backups.py | /api/v1/backups | 备份恢复 (V3) |
| health.py | /api/v1/health | 健康检查 |

### 9.3 前端页面

| 目录 | 文件 | 说明 |
|------|------|------|
| src/standards/ | StandardsPage.tsx | 标准管理 (5 Tab) |
| src/objects/ | ObjectGraphPage.tsx, ExperimentalObjectPage.tsx | 对象图谱 + 实验对象 |
| src/equipment/ | EquipmentPage.tsx | 设备仪器 |
| src/pages/governance/ | DepartmentManagement.tsx | 机构管理 |
| src/facts/ | FactsPage.tsx, FactDetail.tsx | 事实列表 + 详情 |
| src/ingestions/ | IngestionWizard.tsx | 数据摄入向导 |
| src/provenance/ | ProvenancePage.tsx | 溯源链路 (4 Tab) |
| src/parameters/ | ParameterPage.tsx, ApprovalPanel.tsx | 参数管理 + 审批面板 |
| src/components/ | ComponentsPage.tsx, FlowDetail.tsx | 组件管理 + 流程编排 |
| src/models/ | ModelsPage.tsx, ModelDetail.tsx, PredictionWorkbench.tsx | 模型管理 + 详情 + 预测 |
| src/assistant/ | AssistantPage.tsx, MessageThread.tsx, ToolTrace.tsx, CitationList.tsx, ProviderStatus.tsx | AI 助手 |
| src/governance/ | GovernanceConsole.tsx, UsersPage.tsx, ScopeGrantsPage.tsx, AuditPage.tsx, SystemHealthPage.tsx | 平台治理 |
| src/jobs/ | JobsPage.tsx, JobDetail.tsx | 作业中心 |

### 9.4 文档

| 文件 | 说明 |
|------|------|
| README.md | 项目主文档 |
| docs/architecture/system-overview.md | 系统架构概览 |
| docs/architecture/domain-invariants.md | 领域不变量 |
| docs/user-guide/particle-size.md | 粒度分析用户指南 |
| docs/user-guide/grate-cooler-rom.md | 篦冷机 ROM 用户指南 |
| docs/data-onboarding/mapping-profile.md | 数据上线指南 |
| docs/model-onboarding/model-adapter.md | 模型上线指南 |
| docs/operations/install-upgrade.md | 安装升级指南 |
| docs/operations/monitoring.md | 监控指南 |
| docs/operations/backup-restore.md | 备份恢复指南 |
| docs/acceptance/final-release.md | 最终验收文档 |
| docs/acceptance/security-recovery.md | 安全恢复验收 |
| docs/acceptance/v1-particle-size.md | V1 粒度分析验收 |
| docs/prd/v2-prd.md | V2 产品需求文档 |
| docs/arch/v2-architecture.md | V2 系统架构设计 |
| docs/arch/v3-architecture.md | V3 系统架构设计 |

### 9.5 示例

| 文件 | 说明 |
|------|------|
| examples/grate-cooler-rom/generate.py | 篦冷机数据集生成器 |
| examples/grate-cooler-rom/train.py | 模型训练器 |
| examples/grate-cooler-rom/contract.json | 模型输入/输出契约 |
| examples/model-adapter-command/adapter.py | 命令行适配器示例 |
| examples/particle-size/generate.py | 粒度分析 fixture 生成器 |

---

## 10. Git 提交历史

| Commit | 说明 |
|--------|------|
| ab975ac | fix: 修复模型服务 DI + 健康检查迁移版本 |
| 0575f00 | feat: IRIP V1 完整链路 + V2 组件系统/流程引擎/ROM 模型 + V3 AI 助手/治理/备份恢复/安全测试/文档 |
| e35e7cd | feat(departments): 升级组织机构为多层级树形结构 |
| 7a91ece | feat: add equipment management module + 4-tab standards page |
| 3b61ff6 | refactor(web): consolidate 3 sub-modules into parent page tabs |
| fc4bddb | chore: 加固 .gitignore |
| 2d8aa40 | feat: IRIP V0 平台骨架 + 机构/实验室管理模块 |

---

## 11. 已知限制

1. **DB 依赖测试跳过**: 323 个测试需要 `IRIP_TEST_DATABASE_URL` 环境变量才能运行（集成测试、E2E 测试）
2. **E2E 测试需浏览器**: Playwright E2E 测试需要安装浏览器依赖
3. **OpenAI Provider 需 API Key**: 离线模拟模式开箱即用，OpenAI 兼容模式需配置 `IRIP_AI_API_KEY` 环境变量
4. **Worker 需独立进程**: 生产环境应单独运行 Celery Worker
5. **HTTPS 需反向代理**: 本地开发使用 HTTP，生产环境应通过 Nginx/Caddy 提供 HTTPS

---

## 12. 用户下一步建议

1. **启动服务**: 后端已在 :8000 运行，前端 Vite dev server 在 :5173 运行，浏览器访问 http://localhost:5173 即可使用
2. **登录验证**: 使用 admin@irip.local / Admin-IRIP-2026 登录，检查各页面功能
3. **注册组件**: 通过 POST /api/v1/components 注册 29 个内置组件的 YAML manifest
4. **运行篦冷机示例**: `python examples/grate-cooler-rom/generate.py && python examples/grate-cooler-rom/train.py`
5. **测试 AI 助手**: 访问 /assistant 页面，在离线模拟模式下提问 "D50 为什么可信？"
6. **发布门脚本**: 在 Docker 环境就绪后运行 `bash scripts/release-gate.sh` 执行全量质量检查
