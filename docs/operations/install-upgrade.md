# 安装与升级指南

> 适用版本：IRIP V0–V3
> 关联文档：`README.md`、`docs/operations/monitoring.md`、`docs/operations/backup-restore.md`

---

## 1. Docker Compose 部署

### 1.1 全量部署（推荐）

```bash
# 1. 克隆仓库
git clone <repo-url> irip && cd irip

# 2. 复制环境变量样例
cp .env.example .env
# 编辑 .env，修改生产环境配置（至少修改 IRIP_JWT_SECRET 和 IRIP_BOOTSTRAP_ADMIN_PASSWORD）

# 3. 构建并启动全部服务
docker compose up --build -d

# 4. 运行 Bootstrap（幂等初始化）
docker compose run --rm bootstrap

# 5. 验证幂等性（再次运行，应仍 exit 0）
docker compose run --rm bootstrap
```

### 1.2 服务清单

| 服务 | 端口 | 说明 |
|------|------|------|
| `postgres` | 5432 (内部) | PostgreSQL 16 + pgvector |
| `redis` | 6379 (内部) | Redis 7（Celery broker） |
| `minio` | 9000/9001 (内部) | MinIO 对象存储 |
| `api` | 8000 | FastAPI 单体 |
| `worker` | — | Celery Worker（推导/流程/模型/备份恢复） |
| `scheduler` | — | Celery Beat 定时调度 |
| `web` | 80 | nginx 反代 React 前端 |
| `bootstrap` | — | 一次性初始化（幂等） |
| `backup` | — | 备份作业容器 |
| `restore` | — | 恢复作业容器 |

### 1.3 本地开发部署

```bash
# 仅启动基础设施依赖（postgres + redis + minio）
docker compose -f compose.override.local.yaml up -d postgres redis minio

# 后端
python3 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"
.venv/bin/alembic upgrade head
.venv/bin/uvicorn apps.api.main:app --reload --port 8000

# Worker（另开终端）
.venv/bin/celery -A apps.worker.celery_app worker --loglevel=info

# 前端（另开终端）
cd apps/web && corepack enable pnpm && pnpm install && pnpm dev
```

---

## 2. 环境变量配置

核心环境变量（完整列表见 `.env.example`）：

### 2.1 数据库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRIP_DATABASE_URL` | （必填） | PostgreSQL 连接字符串 |
| `IRIP_DATABASE_USER` | `irip` | 数据库用户 |
| `IRIP_DATABASE_PASSWORD` | `irip_dev_password` | 数据库密码 |
| `IRIP_DATABASE_NAME` | `irip` | 数据库名 |

### 2.2 Redis

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRIP_REDIS_URL` | `redis://localhost:6379/0` | Redis 连接 |
| `IRIP_CELERY_BROKER_URL` | `redis://localhost:6379/0` | Celery broker |
| `IRIP_CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | Celery 结果后端 |

### 2.3 MinIO

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRIP_MINIO_ENDPOINT` | `http://localhost:9000` | MinIO 端点 |
| `IRIP_MINIO_ACCESS_KEY` | `irip` | 访问密钥 |
| `IRIP_MINIO_SECRET_KEY` | `irip_dev_password` | 秘密密钥 |
| `IRIP_MINIO_BUCKET` | `irip-artifacts` | Bucket 名称 |
| `IRIP_MINIO_REGION` | `us-east-1` | 区域 |

### 2.4 认证

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRIP_JWT_SECRET` | `dev_only_insecure_...` | JWT 签名密钥（生产必须修改） |
| `IRIP_BOOTSTRAP_ADMIN_PASSWORD` | `Admin-IRIP-2026` | 初始管理员密码 |

### 2.5 AI 助手

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRIP_AI_PROVIDER` | `offline` | AI 模式（offline / openai） |
| `IRIP_AI_BASE_URL` | （空） | OpenAI 兼容 API 基础 URL |
| `IRIP_AI_API_KEY` | （空） | OpenAI 兼容 API 密钥 |
| `IRIP_AI_MODEL` | `gpt-4o-mini` | 模型标识 |

### 2.6 备份

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRIP_BACKUP_OUTPUT_DIR` | `/backups` | 备份输出目录 |
| `IRIP_BACKUP_AGE_RECIPIENT` | （空） | age 加密公钥（不加密则留空） |
| `IRIP_BACKUP_HOST_DIR` | `./backups` | 宿主机备份目录挂载 |

---

## 3. 数据库迁移

### 3.1 执行迁移

```bash
# Docker Compose 环境
docker compose exec api alembic upgrade head

# 本地开发
.venv/bin/alembic upgrade head
```

### 3.2 查看当前版本

```bash
.venv/bin/alembic current
```

### 3.3 迁移版本清单

| 版本 | 内容 | 阶段 |
|------|------|------|
| 0001 | 平台基础表 | V0 |
| 0002 | 认证 | V0 |
| 0003 | 授权 + 审计 | V0 |
| 0004 | 工件 | V0 |
| 0005 | 作业 + Outbox | V0 |
| 0006 | 部门 | V0 |
| 0007 | 用户角色 | V0 |
| 0008 | 标准变量 | V1 |
| 0009 | 工业对象 | V1 |
| 0010 | 事实模板 | V1 |
| 0011 | MappingProfile | V1 |
| 0012 | 事实 | V1 |
| 0013 | 质量摄入 | V1 |
| 0014 | 溯源 | V1 |
| 0015 | 参数 | V1 |
| 0016 | 设备 | V1 |
| 0017 | 部门父级 | V1 |
| 0018 | 组件 | V2 |
| 0019 | 流程 | V2 |
| 0020 | 模型 | V2 |
| 0021 | AI 对话 | V3 |
| 0022 | 审计事件 | V3 |
| 0023 | 备份恢复 | V3 |
| 0024-0031 | RLS/角色/不可变表/部门等 | V3 |
| 0032 | RLS 策略 | V3 |
| 0033 | 不可变表触发器 | V3 |
| 0034 | 数据库角色分离 | V3 |
| 0035-0046 | 角色/部门/AI工具/组件等优化 | V3 |
| 0047 | 修复数据库角色顺序 | V3 |
| 0048 | 强制 RLS | V3 |
| 0049 | 修复不可变表 | V3 |
| 0050 | 组件活跃版本 | V3 |

> 完整迁移清单请运行 `alembic history` 查看。

### 3.4 迁移回滚

```bash
# 回滚一个版本
.venv/bin/alembic downgrade -1

# 回滚到指定版本
.venv/bin/alembic downgrade 0020

# 回滚到初始
.venv/bin/alembic downgrade base
```

> **注意**：回滚会删除对应表和数据。生产环境回滚前必须备份（见 `docs/operations/backup-restore.md`）。

---

## 4. 版本升级流程

### 4.1 标准升级流程

```bash
# 1. 升级前备份（强制！）
docker compose run --rm backup

# 2. 拉取新版本代码
git pull origin main

# 3. 构建新镜像
docker compose build

# 4. 执行数据库迁移
docker compose run --rm api alembic upgrade head

# 5. 重启服务
docker compose up -d

# 6. 验证健康
curl http://localhost:8000/api/v1/health/ready
```

### 4.2 滚动升级

```bash
# 逐个重启服务（减少停机时间）
docker compose up -d --no-deps --build api
docker compose up -d --no-deps --build worker
docker compose up -d --no-deps --build web
```

### 4.3 升级前检查

- [ ] 已完成备份（`docker compose run --rm backup`）
- [ ] 备份完整性验证通过（`BackupManifestValidator`）
- [ ] 查看迁移版本变更日志
- [ ] 确认无正在执行的异步作业（`GET /api/v1/jobs?status=running`）
- [ ] 通知用户维护窗口

---

## 5. 回滚程序

### 5.1 代码回滚 + 数据回滚

```bash
# 1. 停止服务
docker compose down

# 2. 恢复备份（见 docs/operations/backup-restore.md）
docker compose run --rm restore --backup-dir /backups/<backup_timestamp>

# 3. 回退代码版本
git checkout <previous_version_tag>

# 4. 重建并启动
docker compose up --build -d
```

### 5.2 仅代码回滚（不回滚数据）

```bash
# 适用场景：新版本代码有 bug，但数据库迁移前向兼容
git checkout <previous_version_tag>
docker compose build
docker compose up -d
```

### 5.3 迁移降级（谨慎！）

```bash
# 仅当备份迁移版本 > 当前代码版本时需要降级
# 恢复时使用 --skip-migrations 跳过自动迁移
docker compose run --rm restore --backup-dir /backups/<backup_timestamp> --skip-migrations
```

> **降级风险**：降级迁移可能导致数据丢失。强烈建议先在隔离环境验证（见 `docs/operations/backup-restore.md` §6.4）。

---

## 6. 外部工具安装

### 6.1 k6（性能测试）

```bash
# macOS
brew install k6

# Linux (Debian/Ubuntu)
sudo apt install k6

# Docker（无需安装）
docker run --rm -i grafana/k6 run - < tests/performance/k6-smoke.js
```

### 6.2 pg_dump / pg_restore（备份恢复）

```bash
# PostgreSQL 客户端工具（随 PostgreSQL 安装）
# macOS
brew install postgresql@16

# Linux
sudo apt install postgresql-client-16
```

### 6.3 aws-cli（MinIO 同步）

```bash
# macOS
brew install awscli

# Linux
sudo apt install awscli

# 配置 MinIO 兼容端点
aws configure set aws_access_key_id irip
aws configure set aws_secret_access_key irip_dev_password
aws configure set endpoint_url http://localhost:9000
```

### 6.4 age（加密备份，可选）

```bash
# macOS
brew install age

# Linux
sudo apt install age

# 生成密钥对
age-keygen -o /keys/identity.txt
```

---

## 7. 健康检查

```bash
# 存活探针（进程是否运行）
curl http://localhost:8000/api/v1/health/live
# 期望：200 {"status": "live"}

# 就绪探针（依赖是否就绪）
curl http://localhost:8000/api/v1/health/ready
# 期望：200 {"status": "ready", "checks": {"db": "ok", "redis": "ok", "minio": "ok"}}
```

详细监控配置见 `docs/operations/monitoring.md`。

---

## 8. 故障排查

### 8.1 服务无法启动

```bash
# 查看日志
docker compose logs api
docker compose logs worker

# 检查依赖健康
docker compose ps
```

### 8.2 数据库连接失败

```bash
# 检查 PostgreSQL 容器
docker compose ps postgres
docker compose logs postgres

# 验证连接
docker compose exec postgres psql -U irip -d irip -c "SELECT 1;"
```

### 8.3 迁移失败

```bash
# 查看当前迁移状态
docker compose exec api alembic current

# 查看迁移历史
docker compose exec api alembic history

# 手动修复后继续
docker compose exec api alembic upgrade head
```
