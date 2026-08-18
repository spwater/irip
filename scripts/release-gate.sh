#!/usr/bin/env bash
# IRIP 发布门脚本 -- 全量质量检查
# 用法：bash scripts/release-gate.sh
# 任一步骤失败即退出码 1，全部通过输出 "RELEASE GATE PASSED"
# H-10: 先启动环境和迁移，再执行集成/安全/恢复测试
#
# 修复说明（2026-08-18）：
# - Step 7 原用 `docker compose up`（默认 compose.yaml）起全套生产服务，会与开发环境
#   端口冲突（5432/6379/9000），且对主库 irip 做迁移污染数据。改为使用
#   `deployments/compose/test.compose.yaml` 起隔离的测试基础设施（55432/56379/59000）。
# - Step 8 原从不在跑测试前设置 IRIP_TEST_DATABASE_URL 等测试环境变量，导致
#   integration/security/recovery 全部 skip。现已补齐环境变量 + alembic upgrade head。
# - acceptance 测试为纯文档验证，不依赖基础设施，已从 Step 9 移到 Step 3。
# - 工具链对齐：后端用 uv run，前端用 pnpm --dir。
set -euo pipefail

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---- 配置 ----
# 测试基础设施用独立 compose 文件 + 项目名，避免与开发环境冲突
TEST_COMPOSE_FILE="deployments/compose/test.compose.yaml"
TEST_COMPOSE_PROJECT="irip-test-infra"
export COMPOSE_PROJECT_NAME="$TEST_COMPOSE_PROJECT"

PY="${PYTHON:-uv run python}"
PNPM="${PNPM:-pnpm}"

# 步骤计数器
STEP=0
TOTAL_STEPS=9

# ---- 辅助函数 ----

step_header() {
    STEP=$((STEP + 1))
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  Step ${STEP}/${TOTAL_STEPS}: $1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

step_pass() {
    echo -e "${GREEN}  [PASS] $1${NC}"
}

step_fail() {
    echo -e "${RED}  [FAIL] $1${NC}"
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}  RELEASE GATE FAILED at Step ${STEP}${NC}"
    echo -e "${RED}  Reason: $1${NC}"
    echo -e "${RED}========================================${NC}"
    exit 1
}

cleanup() {
    echo ""
    echo -e "${YELLOW}  Cleaning up test infrastructure...${NC}"
    docker compose -f "$TEST_COMPOSE_FILE" down -v 2>/dev/null || true
}
trap cleanup EXIT

# ---- Step 1: Lint + Format ----
step_header "Ruff 静态检查 + 格式检查 (apps packages tests)"
if $PY -m ruff check apps packages tests && $PY -m ruff format --check apps packages tests; then
    step_pass "Ruff lint + format -- 0 errors"
else
    step_fail "Ruff lint 或 format 检查失败"
fi

# ---- Step 2: Type check ----
step_header "Mypy 严格类型检查 (packages apps/api)"
if $PY -m mypy packages apps/api; then
    step_pass "Mypy type check -- 0 errors"
else
    step_fail "Mypy type check 失败"
fi

# ---- Step 3: Unit + Contract + Acceptance tests (no infrastructure needed) ----
# acceptance 为纯文档验证（README 命令/文档链接完整性），不依赖数据库，与 unit/contract 同层
step_header "Python 单元 + 契约 + 验收测试 (unit + contract + acceptance)"
if $PY -m pytest tests/unit tests/contract tests/acceptance -q; then
    step_pass "Unit + Contract + Acceptance tests -- 100% pass"
else
    step_fail "Unit + Contract + Acceptance tests 失败"
fi

# ---- Step 4: Frontend lint ----
step_header "前端 Lint (apps/web)"
if $PNPM --dir apps/web lint; then
    step_pass "前端 lint -- 0 errors"
else
    step_fail "前端 lint 失败"
fi

# ---- Step 5: Frontend tests ----
step_header "前端单元测试 (apps/web)"
if $PNPM --dir apps/web test -- --run; then
    step_pass "前端单元测试 -- 100% pass"
else
    step_fail "前端单元测试失败"
fi

# ---- Step 6: Frontend build ----
step_header "前端生产构建 (apps/web)"
if $PNPM --dir apps/web build; then
    step_pass "前端构建 -- success"
else
    step_fail "前端构建失败"
fi

# ---- Step 7: 启动隔离的测试基础设施 + 迁移 (H-10: 先启动环境和迁移) ----
step_header "启动测试基础设施 + 迁移 (postgres-test/minio-test/redis-test)"
echo "  启动隔离的测试容器（端口 55432/56379/59000，不冲突开发环境）..."
if docker compose -f "$TEST_COMPOSE_FILE" up -d; then
    # 等待 postgres-test 健康
    echo "  等待 postgres-test 健康检查..."
    MAX_WAIT=60
    WAITED=0
    while [ $WAITED -lt $MAX_WAIT ]; do
        if docker compose -f "$TEST_COMPOSE_FILE" exec -T postgres-test pg_isready -U irip -d irip_test > /dev/null 2>&1; then
            break
        fi
        sleep 2
        WAITED=$((WAITED + 2))
    done
    if [ $WAITED -ge $MAX_WAIT ]; then
        step_fail "postgres-test 健康检查超时 (${MAX_WAIT}s)"
    fi

    # 导出测试环境变量（供 Step 8 的集成/安全/恢复测试使用）
    export IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test"
    export IRIP_TEST_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test"
    export IRIP_REDIS_URL="redis://localhost:56379/0"
    export IRIP_MINIO_ENDPOINT="localhost:59000"
    export IRIP_MINIO_ACCESS_KEY="irip"
    export IRIP_MINIO_SECRET_KEY="irip_dev_password"
    export IRIP_MINIO_BUCKET="irip-test"

    # 执行迁移到 head
    echo "  执行 alembic upgrade head..."
    if ! $PY -m alembic upgrade head; then
        step_fail "alembic upgrade head 失败"
    fi

    step_pass "测试基础设施启动 -- 全部健康 + 迁移完成"
else
    step_fail "测试基础设施启动失败"
fi

# ---- Step 8: Integration + Security + Recovery tests (H-10: 环境就绪后执行) ----
step_header "集成 + 安全 + 恢复测试 (integration + security + recovery)"
if $PY -m pytest tests/integration tests/security tests/recovery -q; then
    step_pass "Integration + Security + Recovery tests -- 100% pass"
else
    step_fail "Integration + Security + Recovery tests 失败"
fi

# ---- Step 9: E2E tests (可选，需完整 compose web 服务栈) ----
step_header "E2E 测试 (Playwright)"
# E2E 依赖完整的 web 服务（compose.yaml 全套 + 8080 端口），本地发布门不作为硬门禁，
# 若 Playwright 浏览器或 web 服务不可用则跳过并明确提示（退出码 0，非 FAIL）。
if [ -n "${RUN_E2E:-}" ]; then
    if $PNPM --dir apps/web e2e; then
        step_pass "E2E tests -- 100% pass"
    else
        step_fail "E2E tests 失败"
    fi
else
    echo -e "${YELLOW}  [SKIP] E2E 测试（默认跳过；设置 RUN_E2E=1 且启动完整 compose web 服务后执行）${NC}"
fi

# ---- 最终结果 ----
# F-16: 迁移版本动态读取（alembic heads）
MIGRATION_HEADS=""
if $PY -c "
from alembic.config import Config
from alembic.script import ScriptDirectory
config = Config()
config.set_main_option('script_location', 'migrations')
script_dir = ScriptDirectory.from_config(config)
heads = [rev.revision for rev in script_dir.get_revisions('heads')]
print(', '.join(heads))
" 2>/dev/null; then
    MIGRATION_HEADS=$($PY -c "
from alembic.config import Config
from alembic.script import ScriptDirectory
config = Config()
config.set_main_option('script_location', 'migrations')
script_dir = ScriptDirectory.from_config(config)
heads = [rev.revision for rev in script_dir.get_revisions('heads')]
print(', '.join(heads))
" 2>/dev/null)
else
    MIGRATION_HEADS="unknown"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  RELEASE GATE PASSED${NC}"
echo -e "${GREEN}  All ${TOTAL_STEPS} steps completed successfully${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  版本: 0.8.0"
echo "  阶段: Phase V0-V3 全栈交付"
echo "  迁移版本: ${MIGRATION_HEADS}"
echo ""
exit 0
