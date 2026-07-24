#!/bin/bash
# IRIP 一键启动所有服务
# 用法:
#   bash start_services.sh          — 本地开发模式（venv 直接启动 API + Worker）
#   bash start_services.sh docker   — Docker Compose 全容器化模式
#
# 开发时用默认模式，改代码不需要重建镜像。
# 需要部署或测试 Docker 时用 docker 模式。

set -e
PROJECT_DIR="/Users/shuipei/Desktop/snowSP/irip"
cd "$PROJECT_DIR"

MODE="${1:-local}"

# 加载 .env
load_env() {
  while IFS='=' read -r key value; do
    [ -z "$key" ] && continue
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    key=$(echo "$key" | xargs)
    [ -z "$key" ] && continue
    export "$key=$value" 2>/dev/null || true
  done < .env
}

echo "=== IRIP 服务启动 ($MODE 模式) ==="

# 1. Docker 基础容器（两种模式都需要）
echo "[1] 检查 Docker 基础容器..."
COMPOSE="docker compose -f $PROJECT_DIR/compose.yaml -f $PROJECT_DIR/compose.override.local.yaml --project-directory $PROJECT_DIR"
$COMPOSE up -d --no-build postgres redis minio 2>/dev/null
sleep 3
echo "  PostgreSQL:5432 / Redis:6379 / MinIO:9000 就绪"

if [ "$MODE" = "docker" ]; then
  # ---- Docker 模式：全部容器化 ----
  echo "[2] 启动 API + Worker 容器..."
  $COMPOSE up -d --no-build api worker 2>&1 | tail -10
  echo "  API + Worker 容器已启动"

  echo "[3] 检查前端..."
  if lsof -i :5173 -t >/dev/null 2>&1; then
    echo "  Vite dev server 在运行 :5173"
  else
    echo "  Vite 未运行，启动: cd apps/web && pnpm dev"
  fi
else
  # ---- 本地开发模式：venv 直接启动 ----
  echo "[2] 启动 API (venv)..."
  if lsof -i :8000 -t >/dev/null 2>&1; then
    echo "  API 已在运行 :8000"
  else
    nohup .venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --env-file .env > /tmp/irip-api.log 2>&1 &
    disown $! 2>/dev/null
    sleep 5
    if lsof -i :8000 -t >/dev/null 2>&1; then
      echo "  API 启动成功 :8000"
    else
      echo "  API 启动失败，查看 /tmp/irip-api.log"
      tail -20 /tmp/irip-api.log
      exit 1
    fi
  fi

  echo "[3] 启动 Worker (venv)..."
  if pgrep -f "celery.*apps.worker" >/dev/null 2>&1; then
    echo "  Worker 已在运行"
  else
    load_env
    nohup .venv/bin/celery -A apps.worker.celery_app worker --loglevel=info --concurrency=2 > /tmp/irip-worker.log 2>&1 &
    disown $! 2>/dev/null
    sleep 6
    if pgrep -f "celery.*apps.worker" >/dev/null 2>&1; then
      echo "  Worker 启动成功"
    else
      echo "  Worker 启动失败，查看 /tmp/irip-worker.log"
      tail -20 /tmp/irip-worker.log
      exit 1
    fi
  fi

  echo "[4] 检查前端..."
  if lsof -i :5173 -t >/dev/null 2>&1; then
    echo "  Vite dev server 在运行 :5173"
  else
    echo "  Vite 未运行，请手动启动: cd apps/web && pnpm dev"
  fi
fi

# 验证登录
echo ""
echo "=== 验证登录 ==="
token=$(curl -s http://127.0.0.1:8000/api/v1/auth/login -X POST -H "Content-Type: application/json" -d '{"email":"admin@irip.local","password":"Admin-IRIP-2026"}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token','FAILED')[:20])" 2>/dev/null)
if [ "$token" != "FAILED" ] && [ -n "$token" ]; then
  echo "登录验证通过 ✅"
  echo ""
  echo "服务就绪："
  echo "  API:     http://127.0.0.1:8000"
  echo "  前端:    http://localhost:5173"
  echo "  账号:    admin@irip.local / Admin-IRIP-2026"
else
  echo "登录验证失败 ❌"
fi
