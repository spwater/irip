# IRIP — Industrial Research Intelligence Platform

Phase V0 · Platform Skeleton（平台骨架）

## 环境要求

- Python ≥ 3.12（本机验证 3.13.12）
- Node 22 + corepack（启用 pnpm：`corepack enable pnpm`）
- Docker 24+ / Docker Compose 2.24+（T03 起需要）

## 快速开始

```bash
# 1. 创建虚拟环境
python3 -m venv .venv

# 2. 安装依赖（国内镜像）
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"

# 3. 质量入口
make lint           # ruff
make test-unit      # 单元测试
make typecheck      # mypy strict（packages/common）

# 4. 前端
cd apps/web && corepack enable pnpm && pnpm install
pnpm dev            # 开发服务器
pnpm test --run     # 单元测试
```

## 目录结构

```
apps/api/       FastAPI 单体（T04+ 填充）
apps/worker/    Celery Worker（T07 填充）
apps/web/       React 控制台（T08 填充）
packages/common/ 通用内核（ID/时钟/错误/哈希/分页）
tests/          单元 / 集成 / 安全 / 恢复测试
docs/           PRD 与架构设计
```

## 约定

- 稳定代码 / 错误码 / API 字段：英文；UI 显示文本：中文
- 时间戳一律 UTC `timestamptz`；ID 一律 UUID
- 错误格式：`{error: {code, message, retryable, fields}}`（见 `docs/arch-v0.md` §7.2）
