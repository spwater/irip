#!/bin/bash
# IRIP 一键启动所有服务
# 用法:
#   bash start_services.sh          — 本地开发模式（venv 直接启动 API + Worker）
#   bash start_services.sh docker   — Docker Compose 全容器化交付模式
#
# 开发时用默认模式，改代码不需要重建镜像。
# 需要部署或测试 Docker 时用 docker 模式（全量服务 + --build + bootstrap）。

set -e
# M-12: 基于脚本自身目录定位项目根目录，不硬编码个人路径
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

MODE="${1:-local}"

# 加载 .env
load_env() {
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
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
  if pgrep -f "celery.*apps.worker.*worker" >/dev/null 2>&1; then
    echo "  Worker 已在运行"
  else
    load_env
    nohup .venv/bin/celery -A apps.worker.celery_app worker --loglevel=info --concurrency=2 --queues=irip-normal,irip-ops,irip-research > /tmp/irip-worker.log 2>&1 &
    WORKER_PID=$!
    disown $WORKER_PID 2>/dev/null
    sleep 6
    if pgrep -f "celery.*apps.worker.*worker" >/dev/null 2>&1; then
      echo "  Worker 进程启动成功 (PID: $WORKER_PID)"
    else
      echo "  ❌ Worker 启动失败，查看 /tmp/irip-worker.log"
      echo "  --- 最后 30 行日志 ---"
      tail -30 /tmp/irip-worker.log
      echo "  --- 常见原因 ---"
      echo "  1. Redis 未启动或连接失败"
      echo "  2. 数据库连接失败"
      echo "  3. Python 依赖缺失（运行: uv sync）"
      echo "  4. .env 配置错误"
      exit 1
    fi
  fi

  # 检查 Worker 健康检查端点 (9100)
  echo "  检查 Worker 健康检查端点 :9100 ..."
  WORKER_HEALTHY=false
  for i in $(seq 1 10); do
    if curl -sf http://127.0.0.1:9100/health >/dev/null 2>&1; then
      echo "  Worker 健康检查通过 ✅ (:9100/health)"
      WORKER_HEALTHY=true
      break
    fi
    echo "  等待 Worker 健康检查就绪... ($i/10)"
    sleep 2
  done
  if [ "$WORKER_HEALTHY" = "false" ]; then
    echo "  ⚠️  Worker 健康检查端点 :9100 未就绪（Worker 可能仍在初始化）"
    echo "  这不阻塞启动，但 Docker healthcheck 会标记 worker 为 unhealthy"
    echo "  查看 Worker 日志: tail -f /tmp/irip-worker.log"
  fi

  echo "[4] 启动 Beat 调度器 (venv)..."
  if pgrep -f "celery.*apps.worker.*beat" >/dev/null 2>&1; then
    echo "  Beat 调度器已在运行"
  else
    nohup .venv/bin/celery -A apps.worker.celery_app beat --loglevel=info > /tmp/irip-beat.log 2>&1 &
    BEAT_PID=$!
    disown $BEAT_PID 2>/dev/null
    sleep 3
    if pgrep -f "celery.*apps.worker.*beat" >/dev/null 2>&1; then
      echo "  Beat 调度器启动成功 (PID: $BEAT_PID)"
    else
      echo "  ⚠️  Beat 调度器启动失败，查看 /tmp/irip-beat.log"
      echo "  --- 最后 20 行日志 ---"
      tail -20 /tmp/irip-beat.log
      echo "  Beat 不可用将导致定时任务（Outbox 投递/心跳/备份）无法执行"
    fi
  fi

  echo "[5] 检查前端..."
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
