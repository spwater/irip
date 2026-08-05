# IRIP — 工业研究智能平台（Industrial Research Intelligence Platform）

> ⚠️ **当前版本状态：内部 Alpha / 功能原型 — 不可用于生产环境**
>
> 本版本存在已知的安全和数据完整性问题（详见代码审阅报告），在 P0 问题全部修复前禁止接入生产数据。

> 版本：0.2.0 · Phase V0–V3 全栈交付
> 架构师：高见远（Gao） · 工程师：寇豆码（Kou）

IRIP 是面向工业科研场景的"证据链驱动"智能平台。核心能力：从原始实验数据到发布参数的全链路追溯、模型生命周期管理、AI 助手引用可溯源问答、统一治理控制台。

---

## 核心能力一览

| 层级 | 能力 | 起始阶段 |
|------|------|---------|
| L1 标准层 | 标准变量注册、单位转换、不可变版本 | V1 |
| L2 事实层 | 事实创建、不可变修订、质量评估、证据集冻结 | V1 |
| L2.5 溯源层 | 证据集冻结、推导配方、确定性回放、溯源图 | V1 |
| L3 参数层 | 条件引擎、审批分离、不可变发布、过期检测 | V1 |
| 组件系统 | 25 个内置组件、组件版本不可变、CLI/Python 运行时 | V2 |
| 流程引擎 | DAG 校验、节点级可恢复执行、确定性输出摘要 | V2 |
| 模型生命周期 | 训练/评估/发布/回滚、适用域检查、预测写事实 | V2 |
| AI 助手 | Provider 抽象、7 个只读工具白名单、引用可溯源 | V3 |
| 治理控制台 | 用户/角色/授权/审计/作业/系统健康 | V3 |
| 备份恢复 | SHA-256 完整性校验、异步作业化、加密备份 | V3 |
| 安全韧性 | 5 类安全测试 + 3 类恢复测试 + 性能冒烟 | V3 |

---

## 前提条件

| 依赖 | 最低版本 | 用途 |
|------|---------|------|
| Python | 3.12+ | 后端 API + Worker + 备份脚本 |
| Node.js | 22+ | 前端构建 |
| pnpm | 9.15+ | 前端包管理（通过 corepack 启用） |
| PostgreSQL | 16（含 pgvector） | 唯一权威存储 |
| Redis | 7+ | Celery broker / 结果后端 |
| MinIO | 2024-11+ | S3 兼容对象存储（内容寻址） |
| Docker | 24+ / Docker Compose 2.24+ | 容器化部署 |

> 额外工具（发布门需要）：`k6`（性能测试）、`pg_dump`/`pg_restore`（备份恢复）、`aws-cli`（MinIO 同步）、`age`（加密备份，可选）。

---

## 全新安装

### 方式一：Docker Compose 一键部署（推荐）

```bash
# 1. 克隆仓库
git clone <repo-url> irip && cd irip

# 2. 复制环境变量样例并按需修改
cp .env.example .env

# 3. 构建并启动全部服务
docker compose up --build -d

# 4. 运行 Bootstrap（幂等初始化：组织/角色/管理员/MinIO bucket）
docker compose run --rm bootstrap

# 5. 再次运行 Bootstrap 验证幂等性（应仍 exit 0，管理员仅 1 行）
docker compose run --rm bootstrap
```

### 方式二：本地开发（Python venv + Node dev server）

```bash
# 后端
python3 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"

# 前端
cd apps/web && corepack enable pnpm && pnpm install && cd ..

# 启动基础设施依赖（仅 postgres + redis + minio）
docker compose -f compose.override.local.yaml up -d postgres redis minio

# 数据库迁移
.venv/bin/alembic upgrade head

# 启动 API
.venv/bin/uvicorn apps.api.main:app --reload --port 8000

# 启动 Worker（另开终端）
.venv/bin/celery -A apps.worker.celery_app worker --loglevel=info

# 启动前端（另开终端）
cd apps/web && pnpm dev
```

---

## Bootstrap 凭据

首次 `docker compose run --rm bootstrap` 后，系统自动创建：

| 凭据 | 值 | 说明 |
|------|-----|------|
| 管理员邮箱 | `admin@irip.local` | 内置平台管理员 |
| 管理员密码 | `Admin-IRIP-2026` | 可通过 `IRIP_BOOTSTRAP_ADMIN_PASSWORD` 环境变量覆盖 |

> 生产环境请务必修改 `IRIP_BOOTSTRAP_ADMIN_PASSWORD` 和 `IRIP_JWT_SECRET`。

---

## 示例数据加载

### 粒度分析示例（V1）

```bash
# 生成确定性粒度数据集并播种
.venv/bin/python examples/particle-size/generate.py
.venv/bin/python examples/particle-size/seed.py
```

### 篦冷机 ROM 示例（V2）

```bash
# 生成确定性数据集（240 行，固定种子 20260715）
.venv/bin/python examples/grate-cooler-rom/generate.py

# 训练 ROM 模型
.venv/bin/python examples/grate-cooler-rom/train.py
```

---

## 服务 URL

| 服务 | URL | 说明 |
|------|-----|------|
| API | http://localhost:8000 | FastAPI 单体（OpenAPI 文档 /docs） |
| Web 控制台 | http://localhost:5173 | Vite 开发服务器（本地开发） |
| Web 控制台 | http://localhost:80 | nginx 反代（Docker Compose） |
| MinIO 控制台 | http://localhost:9001 | 对象存储管理界面 |
| PostgreSQL | localhost:5432 | 数据库（用户 irip / 库 irip） |
| Redis | localhost:6379 | Celery broker |

> 健康检查端点：`GET /api/v1/health/live`（存活探针）、`GET /api/v1/health/ready`（就绪探针）。

---

## 停止 / 启动

```bash
# 停止全部服务（保留数据卷）
docker compose stop

# 启动已停止的服务
docker compose start

# 停止并移除容器（保留数据卷）
docker compose down

# 停止并移除容器 + 数据卷（谨慎！会丢失数据）
docker compose down -v

# 前端开发停止：在运行 pnpm dev 的终端按 Ctrl+C
```

---

## 测试命令

```bash
# Lint（ruff 静态检查）
make lint                          # 或: .venv/bin/python -m ruff check apps packages tests

# 类型检查（mypy 严格模式）
make typecheck                     # 或: .venv/bin/python -m mypy packages/common

# 单元测试
make test-unit                     # 或: .venv/bin/python -m pytest tests/unit -v

# 集成测试（需要 Docker 依赖容器）
make test-integration              # 或: .venv/bin/python -m pytest tests/integration -v

# 前端单元测试
make web-test                      # 或: pnpm --dir apps/web test -- --run

# 前端生产构建
make web-build                     # 或: pnpm --dir apps/web build

# 全量测试套件（单元 + 属性 + 契约 + 集成 + 安全 + 恢复 + 验收）
.venv/bin/python -m pytest tests/unit tests/property tests/contract tests/integration tests/security tests/recovery tests/acceptance -v

# 发布门（全量质量门，详见 scripts/release-gate.sh）
bash scripts/release-gate.sh
```

---

## 文档索引

### 架构文档
| 文档 | 内容 |
|------|------|
| [系统架构概览](docs/architecture/system-overview.md) | 模块化单体 + Worker 架构、基础设施、V0–V3 各层职责、数据流 |
| [领域不变量](docs/architecture/domain-invariants.md) | 不可变版本化、确定性回放、证据链完整性、AI 工具只读边界等约束基线 |
| [V0 架构设计](docs/arch-v0.md) | 平台骨架设计（认证/授权/工件/作业/Outbox） |
| [V2 架构设计](docs/arch/v2-architecture.md) | 组件系统 + 流程引擎 + 模型生命周期设计 |
| [V3 架构设计](docs/arch/v3-architecture.md) | AI 助手 + 治理控制台 + 备份恢复设计 |

### 用户指南
| 文档 | 内容 |
|------|------|
| [粒度分析用户指南](docs/user-guide/particle-size.md) | 标准变量注册 → 事实创建审批 → 推导运行 → 参数发布 |
| [篦冷机 ROM 用户指南](docs/user-guide/grate-cooler-rom.md) | 模型训练 → 验证发布 → 预测工作台 → 适用域检查 → 回滚 |

### 上线指南
| 文档 | 内容 |
|------|------|
| [数据上线 — 映射配置](docs/data-onboarding/mapping-profile.md) | MappingProfile 创建、字段映射、连接器配置 |
| [模型上线 — 适配器开发](docs/model-onboarding/model-adapter.md) | ModelAdapter 协议、命令行适配器、模型契约、训练/验证/发布 |

### 运维指南
| 文档 | 内容 |
|------|------|
| [安装与升级](docs/operations/install-upgrade.md) | Docker Compose 部署、数据库迁移、版本升级、回滚 |
| [监控运维](docs/operations/monitoring.md) | 健康检查端点、日志收集、性能指标、告警配置 |
| [备份恢复](docs/operations/backup-restore.md) | 备份策略、完整性校验、灾难恢复 Runbook |

### 验收文档
| 文档 | 内容 |
|------|------|
| [V3 最终发布验收](docs/acceptance/final-release.md) | V0–V3 功能清单、验收测试结果、已知限制、发布版本号 |
| [V1 粒度验收报告](docs/acceptance/v1-particle-size.md) | L1→L3 证据链验收门 |
| [安全恢复验收报告](docs/acceptance/security-recovery.md) | V3-T04 安全/恢复/性能测试套件验收 |

---

## 约定

- **语言**：稳定代码 / 错误码 / API 字段使用英文；UI 显示文本使用中文。
- **时间**：一律 UTC `timestamptz`；ID 一律 UUID。
- **错误格式**：`{error: {code, message, retryable, fields}}`（见 `docs/arch-v0.md` §7.2）。
- **审计**：仅追加，`REVOKE UPDATE, DELETE ON audit_event`。
- **备份**：每个组件附带 SHA-256 校验和，恢复前必须验证。

---

## 目录结构

```
apps/api/          FastAPI 单体（认证/标准/事实/参数/组件/流程/模型/AI/治理/审计/备份/健康）
apps/worker/       Celery Worker（推导/流程/模型/备份恢复异步作业）
apps/web/          React 控制台（Ant Design + TanStack Router/Query）
packages/common/   通用内核（ID/时钟/错误/哈希/分页/数据库/工件）
packages/auth/     认证 + 授权（AppUser/Role/ScopeGrant/JWT/刷新旋转）
packages/audit/    审计事件（仅追加 + 脱敏）
packages/jobs/     异步作业（Outbox + 租约 + 幂等提交）
packages/connectors/  数据连接器（PostgreSQL/REST/File）
packages/standards/    L1 标准层（变量/单位/不可变版本）
packages/facts/       L2 事实层（事实/修订/观察值/质量评估）
packages/provenance/  L2.5 溯源层（证据集/配方/推导/溯源图）
packages/parameters/  L3 参数层（候选/审批/发布/过期）
packages/components/ 组件系统（SDK/注册表/执行器/流程引擎）
packages/models/      模型生命周期（契约/适配器/服务/适用域）
packages/ai/          AI 助手（Provider/工具白名单/引用/服务）
deployments/compose/  Docker 部署（Dockerfile/Compose/bootstrap/backup/restore）
docs/                 架构设计、用户指南、运维指南、验收文档
tests/                单元/集成/契约/安全/恢复/性能/验收测试
scripts/              发布门脚本
examples/             粒度分析 + 篦冷机 ROM 示例
```
