#!/bin/bash
# IRIP 一键启动所有服务
# 用法:
#   bash start_services.sh          — 本地开发模式（venv 直接启动 API + Worker）
#   bash start_services.sh docker   — Docker Compose 全容器化交付模式
#
# 开发时用默认模式，改代码不需要重建镜像。
# 需要部署或测试 Docker 时用 docker 模式（全量服务 + --build + bootstrap）。

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

COMPOSE="docker compose -f $PROJECT_DIR/compose.yaml -f $PROJECT_DIR/compose.override.local.yaml --project-directory $PROJECT_DIR"

if [ "$MODE" = "docker" ]; then
  # ============================================================
  # Docker 全量容器化交付模式
  # 一次性启动全部常驻服务 + bootstrap（迁移+建管理员），用 --build
  # ============================================================
  echo "[1] 构建并启动全部服务（postgres/redis/minio/api/worker/scheduler/web/bootstrap）..."
  echo "    首次构建较慢（pip 安装 + npm install + vite build），请耐心等待..."
  $COMPOSE --profile ops up --build -d 2>&1 | tail -30
  echo "  全部容器已提交启动"

  echo ""
  echo "[2] 等待基础服务健康..."
  sleep 10
  $COMPOSE ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || $COMPOSE ps

  echo ""
  echo "[3] 检查 bootstrap（数据库迁移 + 管理员创建）..."
  # bootstrap 是一次性容器，restart: "no"，up 后会自动跑完退出
  BOOTSTRAP_EXIT=$($COMPOSE ps bootstrap --format json 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('State', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
  if [ "$BOOTSTRAP_EXIT" = "exited" ] || [ "$BOOTSTRAP_EXIT" = "unknown" ]; then
    echo "  bootstrap 已执行，查看日志确认："
    $COMPOSE logs bootstrap --tail 20 2>/dev/null || true
  fi

  echo ""
  echo "[4] 等待 API 健康就绪..."
  for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/api/v1/health/live >/dev/null 2>&1; then
      echo "  API 健康检查通过 ✅"
      break
    fi
    echo "  等待 API 就绪... ($i/30)"
    sleep 5
  done

else
  # ============================================================
  # 本地开发模式：venv 直接启动 API + Worker
  # ============================================================
  echo "[1] 检查 Docker 基础容器..."
  $COMPOSE up -d --no-build postgres redis minio 2>/dev/null
  sleep 3
  echo "  PostgreSQL:5432 / Redis:6379 / MinIO:9000 就绪"

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

# ============================================================
# 验证登录
# ============================================================
echo ""
echo "=== 验证登录 ==="
sleep 3
token=$(curl -s http://127.0.0.1:8000/api/v1/auth/login -X POST -H "Content-Type: application/json" -d '{"email":"admin@irip.local","password":"Admin-IRIP-2026"}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token','FAILED')[:20])" 2>/dev/null)
if [ "$token" != "FAILED" ] && [ -n "$token" ]; then
  echo "登录验证通过 ✅"
  echo ""
  echo "服务就绪："
  echo "  API:     http://127.0.0.1:8000"
  echo "  前端:    http://localhost:8080"
  echo "  MinIO:   http://localhost:9001 (irip / irip_dev_password)"
  echo "  账号:    admin@irip.local / Admin-IRIP-2026"
else
  echo "登录验证失败 ❌（如首次启动，请等 bootstrap 跑完后再试）"
  echo "  查看状态: $COMPOSE ps"
  echo "  查看 bootstrap 日志: $COMPOSE logs bootstrap"
  echo "  手动重跑 bootstrap: $COMPOSE --profile ops run --rm bootstrap"
fi
