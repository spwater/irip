# IRIP 阶段性交付文档

> 更新时间：2026-08-08（Asia/Shanghai）
> 代码基线：`main`，整理前最新业务提交 `62c4711`
> 项目版本：`0.8.0`
> 当前定位：内部 Alpha / 功能原型，不作为生产发布批准

## 1. 这份文档怎么用

这是 IRIP 后续开发的默认上下文入口。开始新任务时，先读本文件，再按任务需要读取代码和对应的常驻操作文档；不要把 `archived/` 中的历史 PRD、架构稿或评审结论直接当成当前事实。

事实优先级如下：

1. 当前代码、数据库迁移、配置和测试；
2. 当前 Git 状态、近期提交和最新实测；
3. 常驻操作文档；
4. `archived/` 中的历史材料。

本文件不替代接口源码或运维 Runbook。它负责说明“系统现在是什么、做到哪里、如何验证、有哪些风险、下一步从哪里开始”。

## 2. 当前阶段结论

IRIP 已完成从 V0 到 V3 的主体功能实现，并在 2026-08-07 至 2026-08-08 经历了集中修复和重构：部门租户隔离、研究工作台、AI 数值工具、连接器密钥加密、服务和路由拆分、异常日志、CSP、前端可访问性、类型检查以及 CI/E2E 配置均有实际代码提交。

当前更准确的判断是：

- 产品能力覆盖较完整，已经不是只有页面和接口骨架的演示仓库；
- 静态质量门在本机可通过，后端非基础设施测试已有一部分实测通过；
- 前端生产构建可通过，但前端单测存在依赖模块加载问题；
- 全量集成、安全、恢复和验收测试依赖 PostgreSQL、Redis、MinIO、Docker 镜像及专用环境变量，本次没有形成完整绿灯；
- 因此仍应保持“内部 Alpha / 不批准生产”的状态，下一阶段重点应是恢复可重复的全量质量门，而不是继续横向增加功能。

## 3. 产品目标与核心边界

IRIP 是面向工业科研场景的证据链驱动平台。它把原始数据、标准变量、实验事实、推导过程、参数发布、组件流程、模型生命周期和 AI 辅助分析放在同一套可审计的数据与权限边界内。

核心目标不是通用 BI，也不是让 AI 直接修改科研事实。平台强调：

- PostgreSQL 是权威状态存储；Redis 只承担队列、缓存和协调；MinIO 保存内容寻址工件；
- 事实、证据集、参数版本、组件版本和模型版本采用不可变或追加式演进；
- 派生结果应能追溯输入、配方、代码或模型版本，并支持确定性校验；
- 多租户边界以 `department_id` 为主，关键表使用 PostgreSQL RLS；
- AI 工具受白名单与只读边界约束，回答中的结论应关联可追溯引用；
- 审批、发布、审计和备份恢复属于产品能力，不是上线后再补的外围功能。

领域不变量的完整定义见 [领域不变量](architecture/domain-invariants.md)。

## 4. 已交付能力

### 4.1 平台骨架与治理

- 用户认证、JWT 与刷新令牌轮换；
- 角色、ScopeGrant、部门成员关系和部门树可见性；
- 审计事件追加写入与敏感字段脱敏；
- 通用错误码、分页、ID、时钟、哈希、对象存储和数据库访问设施；
- 异步作业、Outbox、租约、幂等提交、Celery Worker 与 Beat；
- 健康检查、Prometheus 指标、结构化日志和治理控制台；
- PostgreSQL/Redis/MinIO 的 Compose 部署、Bootstrap、备份与恢复工具。

### 4.2 科研数据链路

- L1 标准变量与单位体系；
- L2 事实、修订、观察值和质量评估；
- L2.5 证据集、推导配方、推导运行和溯源图；
- L3 参数候选、审批分离、发布与过期检测；
- PostgreSQL、REST 和文件连接器，以及字段映射和摄入流程；
- 设备、对象类型、研究对象和实验项目管理。

### 4.3 组件、流程与模型

- 组件协议、Manifest、注册表、Runner 和内置数据处理组件；
- 流程定义、DAG 校验、序列化、节点执行、运行状态和输出摘要；
- 模型契约、CLI 适配器、训练、评估、发布、回滚、适用域检查和预测事实写回；
- 粒度分析与篦冷机 ROM 示例，用于演示从数据到事实/模型的链路。

### 4.4 AI 与研究工作台

- AI Provider 抽象、OpenAI-compatible Provider 与离线 Provider；
- 对话、引用、取消、协作会话、展示和工具执行；
- 数值表达式、统计、单位处理和数据解析工具；
- AI 配置与工具管理接口，Secret 值通过应用层加密保存；
- 研究工作区、计划确认与执行、研究产物、发布和血缘导航；
- 研究模块受 `RESEARCH_MODULE_ENABLED` 控制，当前默认开启，修改环境变量后需重启后端。

## 5. 当前系统结构

IRIP 采用“模块化单体 API + 异步 Worker + 单页 Web 应用”的结构：

```text
Browser / React
       |
       v
FastAPI routers -> composition/providers -> domain services/repositories
       |                                      |
       |                                      v
       |                               PostgreSQL + RLS
       |
       +-> Outbox / Celery -> Worker -> MinIO / Redis / PostgreSQL
       |
       +-> AI provider -> read-only tools -> facts/models/research context
```

### 5.1 运行单元

| 单元 | 路径 | 职责 |
|---|---|---|
| API | `apps/api/` | FastAPI 装配、认证依赖、路由和 HTTP/SSE 边界 |
| Worker | `apps/worker/` | 推导、流程、模型、研究和系统异步任务 |
| Web | `apps/web/` | React 18、Ant Design、TanStack Router/Query 前端 |
| 领域包 | `packages/` | 19 个业务与基础设施包，承载实体、服务、仓储与协议 |
| 数据库迁移 | `migrations/` | Alembic 基线与增量迁移 |
| 部署 | `deployments/compose/` | 镜像、Nginx、Bootstrap、备份恢复 |

### 5.2 后端领域包

`packages/` 当前包含：

- 基础设施：`common`、`auth`、`audit`、`jobs`、`backups`；
- 数据链路：`connectors`、`standards`、`facts`、`provenance`、`parameters`；
- 业务对象：`departments`、`equipment`、`experiment_project`；
- 计算运行：`components`、`models`、`plugins`；
- 智能与研究：`ai`、`research`；
- 管理入口：`governance`。

近期已将多个过大的 repository、service、expression、products 和 orchestrator 文件拆为职责更清晰的子模块。后续修改应沿用现有边界，不把已拆出的逻辑重新堆回聚合文件。

### 5.3 API 装配

`apps/api/main.py` 当前注册认证、账户、上传、工件、作业、部门、设备、对象、摄入、事实、溯源、参数、组件、流程、实验项目、模型、健康、治理、审计、备份、助手、展示、AI 配置、AI 工具、文件、组件预览和协作等路由。研究、执行、产物、发布和血缘路由由研究功能开关控制。

### 5.4 数据库迁移

Alembic 当前唯一 head 为 `0082`。迁移序列由压缩基线 `0001_squashed_baseline.py` 和 `0062`–`0082` 的增量组成。近期增量的主要内容包括：

- 部门字段补齐、回填、非空约束、RLS 切换和旧 organization 退役；
- AI meta prompt 与会话配置；
- 实验项目、研究基础、可信执行、研究产物、发布和血缘；
- RLS 扩展到溯源边、对象组件关系；
- AI 数值工具、thinking 配置拆分；
- 审计事件写权限收紧；
- Secret 值加密迁移。

迁移涉及租户隔离和加密，不应通过手工改表绕开。开发环境也应使用 Alembic 升级到 head。

## 6. 关键数据流与不变量

### 6.1 数据到参数

```text
文件/数据库/API
  -> Connector / MappingProfile
  -> 标准变量与单位映射
  -> Fact + Observation + Quality
  -> 冻结 EvidenceSet
  -> DerivationRecipe / DerivationRun
  -> ParameterCandidate
  -> 独立审批
  -> 不可变 ParameterVersion
```

任何跳过证据集、推导记录或审批分离的快捷实现，都会破坏平台的主要产品价值。

### 6.2 流程与模型

流程定义先做 DAG 与 Manifest 校验，再由作业系统调度节点执行；结果摘要用于可重复性判断。模型以稳定身份管理多个不可变版本，训练与评估产物进入对象存储，发布和回滚只移动发布指针，不改写旧版本。

### 6.3 租户与权限

应用层依赖负责解析主体、权限和部门范围；数据库连接设置租户 GUC，RLS 是最终隔离边界。集成测试必须使用受限的 `irip_app` 角色验证隔离，不能用 superuser 得出 RLS 已生效的结论。

### 6.4 AI 边界

AI 只能通过注册并允许的工具读取或计算上下文。工具参数、超时、取消、引用和错误映射属于安全边界。任何新增工具都要检查：是否只读、是否继承部门范围、输出是否可引用、是否可能发生 SSRF/SQL 注入/路径穿越或大输入 DoS。

## 7. 开发与运行基线

### 7.1 版本要求

| 组件 | 项目声明 |
|---|---|
| Python | `>=3.12`，CI 覆盖 3.12 和 3.13 |
| Node.js | 22+ |
| pnpm | README 写 9.15+；前端锁文件可由 Corepack 管理 |
| PostgreSQL | 16 + pgvector |
| Redis | 7+ |
| MinIO | 2024-11+ |

### 7.2 推荐本地安装

```bash
uv sync --frozen --extra dev
pnpm --dir apps/web install --frozen-lockfile
```

本次实测确认：只运行 `uv run --frozen ...` 时，环境可能没有安装 `dev` extra，pytest 会因 `sqlparse` 缺失而在收集阶段退出。先执行上面的 `uv sync` 可以避免这个误判。

项目同时存在 `uv.toml` 和 `pyproject.toml` 中的 `tool.uv` 配置，uv 会警告 `uv.toml` 优先。这不阻止安装，但应在下一阶段统一配置来源。

### 7.3 常用命令

```bash
# 后端静态检查
uv run --frozen --extra dev ruff check apps packages tests
uv run --frozen --extra dev ruff format --check apps packages tests
uv run --frozen --extra dev mypy packages apps/api apps/worker

# 后端测试
uv run --frozen --extra dev pytest tests/unit tests/test_*.py -m "not integration"
uv run --frozen --extra dev pytest tests/contract

# 前端
pnpm --dir apps/web test --run
pnpm --dir apps/web build

# 需要完整基础设施的发布门
bash scripts/release-gate.sh
```

部署、升级和服务启动以 [安装与升级指南](operations/install-upgrade.md) 为准；备份恢复以 [备份恢复手册](operations/backup-restore.md) 为准。

## 8. 2026-08-08 实测质量基线

以下结果来自本次文档整理期间的本机命令，不沿用历史报告数字。

| 检查 | 结果 | 说明 |
|---|---|---|
| Ruff lint | 通过 | `apps packages tests`：All checks passed |
| Ruff format check | 通过 | 492 files already formatted |
| mypy | 通过 | 354 个源文件，0 issues |
| Alembic heads | 通过 | 唯一 head：`0082` |
| 后端全量 pytest | 未完成 | 进入集成测试前已有 95 passed、2 skipped；随后 Testcontainers 拉取 `testcontainers/ryuk:0.8.1` 受阻，人工中止 |
| 前端生产构建 | 通过 | TypeScript 检查和 Vite 构建完成；存在动态/静态混合导入提示及大 chunk |
| 前端 Vitest | 未通过 | 12 个文件中 6 passed、6 failed；29 tests passed，失败文件在收集/运行阶段触发 Ant Design colors 的 ESM/CJS 加载错误 |
| 全量发布门 | 未执行 | 需要完整 PostgreSQL、Redis、MinIO、Docker、k6 等环境 |

后端 pytest 的 2 个 skip 是因为未设置 `IRIP_TEST_DATABASE_URL`。本次中止点不是业务断言失败，而是本机缺少 Testcontainers Ryuk 镜像且拉取没有完成。该结果只能说明已运行部分通过，不能写成全量测试通过。

前端构建成功不等于前端测试成功。构建输出中的主要信号是：

- `research.ts` 和 `researchProducts.ts` 同时被动态与静态导入，不能按预期拆 chunk；
- `plotly.min` 约 4.84 MB，`LabOpsPage`、Ant Design vendor 等 chunk 仍较大；
- Vitest 使用的当前依赖树含 Ant Design 6 icons/colors 与 Ant Design 5 主包组合，部分测试加载 ESM 文件时失败；
- `MentionInput` 仍使用已弃用的 Dropdown `overlay` API，并触发 React `findDOMNode` 警告；
- jsdom 对带伪元素参数的 `getComputedStyle` 支持不完整，摄入向导测试会输出噪声。

## 9. 多轮开发与调试形成的工程结论

这些结论来自当前代码、测试结构和近期修复提交，后续修改应继续遵守。

### 9.1 RLS 验证必须使用真实受限角色

曾出现 E2E 使用 superuser 后安全断言失真的问题。RLS 测试要通过 `irip_app` 或等价受限角色运行，并显式设置部门 GUC。只验证 API 返回过滤结果不足以证明数据库隔离有效。

### 9.2 测试环境和生产迁移要明确分流

迁移 `0082` 涉及 Secret 加密，测试环境通过 `IRIP_ENV=test` 处理可重复迁移条件。迁移代码中的环境分支必须保持窄范围、可解释，不能让测试路径掩盖生产升级风险。

### 9.3 异步测试容易受事件循环与外部设施影响

恢复测试曾修复 asyncio 事件循环冲突；全量套件还会启动 Testcontainers。排查失败时先区分收集失败、业务断言失败、事件循环问题、服务不可达和镜像拉取失败，不要直接修改业务逻辑来“修测试”。

### 9.4 前后端功能开关必须一致

研究模块默认开启，但旧 E2E 仍按 `flows/parameters/models` 的 Tab 结构断言，近期通过在 E2E 中关闭研究模块恢复兼容。新增功能开关时必须同步 API 路由、composition、`/me` 返回和前端条件渲染，并为开/关两种状态保留测试。

### 9.5 大文件拆分后要守住依赖方向

近期重构拆分了 repository、publication、expression、plan service、products 和 orchestrator。聚合模块可以保留兼容导出，但新逻辑应进入职责明确的子模块；路由负责 HTTP，composition 负责装配，领域服务不应反向依赖 FastAPI。

### 9.6 静默异常会掩盖数据和安全问题

项目已将多处 `except Exception: pass` 改为日志记录。可选展示数据可以降级，但必须带上下文记录；事务、权限、加密、上传完成、备份恢复和作业状态错误不得静默吞掉。

### 9.7 前端包体优化不能牺牲能力

Plotly 曾切到 basic 发行版降低体积，随后因缺少 3D、geo 和 financial 图表恢复完整版。下一轮优化应使用按需加载、页面边界和真实业务图表清单，不应仅替换发行包后依赖构建成功判断兼容。

### 9.8 CI 固定依赖也要验证引用有效

GitHub Actions 使用 SHA 固定以降低供应链风险，但固定 SHA 本身可能失效；近期已修复 `actions/download-artifact` 引用。升级 Action 时既要固定可信提交，也要验证该提交可下载并与目标 major 版本一致。

## 10. 已知风险与技术债

按下一阶段处理优先级排序：

### P0：恢复可信发布门

1. 修复前端 Vitest 的 Ant Design icons/colors ESM/CJS 加载问题，并在项目声明的 Node/pnpm 版本下复测全部 12 个测试文件。
2. 准备可重复的 Docker/Testcontainers 环境，预拉或镜像代理 Ryuk、pgvector、Redis、MinIO，跑完 integration/security/recovery/acceptance。
3. 使用 CI 等价的 `IRIP_TEST_DATABASE_URL` 和受限数据库角色验证 RLS；生成新的 JUnit/覆盖率结果。
4. 跑通 `scripts/release-gate.sh` 后再讨论解除 Alpha 状态。

### P1：消除环境和性能漂移

1. 合并 `uv.toml` 与 `pyproject.toml` 中重复的 uv 索引配置，保留单一来源。
2. 明确并锁定开发/CI 的 Node 与 pnpm 组合，避免本机自动安装依赖后出现与 CI 不同的模块解析结果。
3. 处理研究模块混合导入和超大前端 chunk，建立包体预算；Plotly 必须保留实际使用的图表类型。
4. 替换 Ant Design 已弃用 API，减少 React 严格模式和 jsdom 警告。
5. 将覆盖率门槛从当前 30% 按模块逐步提高，优先覆盖权限、RLS、备份恢复、作业幂等和 AI 工具边界。

### P2：持续收敛架构

1. 定期检查聚合文件是否重新膨胀，保持 router/composition/service/repository 的依赖方向。
2. 为状态字段补足数据库 CHECK 约束，并保持应用枚举与迁移一致。
3. 继续减少宽泛 `type: ignore`、第三方无类型调用和可避免的 `Any`，但以清晰边界为目标，不做机械清零。
4. 校准操作文档中的命令、端口、迁移清单和示例数据路径；代码变更时同步更新。

## 11. 下一阶段建议执行顺序

后续任务建议从以下顺序开始，每完成一项都更新本文件的实测基线：

1. 固化 Node/pnpm 版本并修复 6 个前端测试文件加载失败；
2. 建立可复用的本地或 CI 基础设施测试环境；
3. 跑通后端 unit、contract、integration、security、recovery、acceptance 全矩阵；
4. 跑通发布门并生成新的验收快照；
5. 复核 README 的“不可生产”判断，给出明确的进入试点环境条件；
6. 发布门稳定后，再从真实用户反馈选择下一批功能。

## 12. 常驻文档

这些文档仍保留在活跃目录，按需读取：

| 文档 | 用途 |
|---|---|
| [README](../README.md) | 项目入口、安装、服务和常用命令 |
| [系统架构概览](architecture/system-overview.md) | 稳定架构与数据流 |
| [领域不变量](architecture/domain-invariants.md) | 不可破坏的业务和安全约束 |
| [新人上手](onboarding.md) | 开发者快速理解代码和启动环境 |
| [编码约定](conventions.md) | 后端、数据库、前端、测试和 Git 约定 |
| [设计语言](tideline-design-language.md) | Tideline UI 视觉与交互规则 |
| [数据上线](data-onboarding/mapping-profile.md) | MappingProfile 与连接器操作 |
| [模型上线](model-onboarding/model-adapter.md) | ModelAdapter 与模型生命周期接入 |
| [安装升级](operations/install-upgrade.md) | Compose、环境变量和迁移操作 |
| [监控运维](operations/monitoring.md) | 健康、日志、指标和告警 |
| [备份恢复](operations/backup-restore.md) | 备份、校验和恢复 Runbook |
| [粒度分析指南](user-guide/particle-size.md) | V1 端到端业务操作 |
| [篦冷机 ROM 指南](user-guide/grate-cooler-rom.md) | V2 模型示例操作 |

历史 PRD、架构稿、评审、发布报告、验收快照和旧计划统一见 [历史资料索引](../archived/README.md)。历史文档用于追溯“为什么曾经这样设计”，不用于判断当前实现。

## 13. 更新规则

后续每次阶段性交付或重要修复，应直接更新本文件，而不是再新增一份平行的“最终报告”：

- 更新顶部日期、代码基线和版本；
- 用新实测替换第 8 节结果，保留失败与未执行项；
- 把已解决风险从第 10 节移入第 9 节工程结论，写清修复方式；
- 调整下一阶段顺序，不保留已经失效的 TODO；
- 新增长期有效的操作说明时放入对应常驻文档，并从本文件链接；
- 阶段性分析、一次性评审和验收快照完成后直接归档到 `archived/`。
