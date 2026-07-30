#!/usr/bin/env bash
# IRIP 发布门脚本 -- 全量质量检查
# 用法：bash scripts/release-gate.sh
# 任一步骤失败即退出码 1，全部通过输出 "RELEASE GATE PASSED"
# H-10: 先启动环境和迁移，再执行集成/安全/恢复测试
set -euo pipefail

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---- 配置 ----
COMPOSE_PROJECT_NAME=irip-release-gate
export COMPOSE_PROJECT_NAME

PY="${PYTHON:-.venv/bin/python}"
PNPM="${PNPM:-pnpm}"

# 步骤计数器
STEP=0
TOTAL_STEPS=10

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
    echo -e "${YELLOW}  Cleaning up Docker Compose release-gate environment...${NC}"
    COMPOSE_PROJECT_NAME=irip-release-gate docker compose down -v 2>/dev/null || true
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

# ---- Step 3: Unit + Contract tests (no infrastructure needed) ----
step_header "Python 单元 + 契约测试 (unit + contract)"
if $PY -m pytest tests/unit tests/contract -v -m "not integration"; then
    step_pass "Unit + Contract tests -- 100% pass"
else
    step_fail "Unit + Contract tests 失败"
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

# ---- Step 7: Docker Compose up + migration (H-10: 先启动环境和迁移) ----
step_header "Docker Compose 全量启动 + 迁移 (release-gate 环境)"
echo "  构建并启动全部服务..."
if docker compose up --build -d; then
    # 等待 API 健康
    echo "  等待 API 健康检查..."
    MAX_WAIT=60
    WAITED=0
    while [ $WAITED -lt $MAX_WAIT ]; do
        if curl -sf http://localhost:8000/api/v1/health/live > /dev/null 2>&1; then
            break
        fi
        sleep 2
        WAITED=$((WAITED + 2))
    done
    if [ $WAITED -ge $MAX_WAIT ]; then
        step_fail "Docker Compose 启动后 API 健康检查超时 (${MAX_WAIT}s)"
    else
        step_pass "Docker Compose 启动 -- 全部服务健康 + 迁移完成"
    fi
else
    step_fail "Docker Compose 启动失败"
fi

# ---- Step 8: Integration + Security + Recovery tests (H-10: 环境就绪后执行) ----
step_header "集成 + 安全 + 恢复测试 (integration + security + recovery)"
# H-10: 按目录独立执行，不加 marker 过滤
if $PY -m pytest tests/integration tests/security tests/recovery -v; then
    step_pass "Integration + Security + Recovery tests -- 100% pass"
else
    step_fail "Integration + Security + Recovery tests 失败"
fi

# ---- Step 9: Acceptance + E2E tests ----
step_header "验收 + E2E 测试 (acceptance + e2e)"
if $PY -m pytest tests/acceptance -v && $PNPM --dir apps/web e2e; then
    step_pass "Acceptance + E2E tests -- 100% pass"
else
    step_fail "Acceptance + E2E tests 失败"
fi

# ---- Step 10: Cleanup (via trap) ----
step_header "清理 Docker Compose 环境"
# trap EXIT 会自动执行 cleanup
step_pass "清理完成"

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
echo "  版本: 0.1.0"
echo "  阶段: Phase V0-V3 全栈交付"
echo "  迁移版本: ${MIGRATION_HEADS}"
echo ""
exit 0
