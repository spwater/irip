#!/usr/bin/env bash
# IRIP 本地开发启动脚本
# 用法：./dev.sh [up|restart|down|status]
#   up      - 启动全部服务（Docker 基础设施 + API + Worker + Beat + 前端）
#   restart - 重启应用层服务（API + Worker + Beat + 前端），不动 Docker 容器
#   down    - 停止全部服务（含 Docker 容器）
#   status  - 查看服务状态

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 加载 .env ──────────────────────────────────────────────
# 关键：Celery 用 os.getenv() 读环境变量，不像 uvicorn 有 --env-file 参数，
# 所以必须在 shell 层面 export 环境变量，否则 Worker/Beat 连不上带密码的 Redis。
if [ ! -f .env ]; then
    echo "❌ .env 文件不存在，请先创建"
    exit 1
fi
load_env() {
    while IFS= read -r line || [ -n "$line" ]; do
        # 跳过空行和注释行
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        # 提取 key=value
        key="${line%%=*}"
        value="${line#*=}"
        # 跳过无效 key（必须以字母/下划线开头）
        [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && continue
        # 去掉值两端的引号
        value="${value#\"}" ; value="${value%\"}"
        value="${value#\'}" ; value="${value%\'}"
        export "$key=$value"
    done < .env
}
load_env

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[dev.sh]${NC} $1"; }
warn() { echo -e "${YELLOW}[dev.sh]${NC} $1"; }
err() { echo -e "${RED}[dev.sh]${NC} $1"; }

# PID 文件
PID_DIR="/tmp/irip-dev-pids"
mkdir -p "$PID_DIR"

start_infra() {
    log "启动 Docker 基础设施（PG/Redis/MinIO）..."
    docker compose -f compose.base.yaml -f compose.development.yaml up -d postgres redis minio
    
    # 等待 PG healthy
    log "等待 PostgreSQL 就绪..."
    for i in $(seq 1 15); do
        if [ "$(docker inspect irip-postgres-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ]; then
            log "PostgreSQL 就绪"
            break
        fi
        sleep 2
        if [ $i -eq 15 ]; then
            err "PostgreSQL 启动超时"
            exit 1
        fi
    done
    
    # 等待 MinIO healthy
    log "等待 MinIO 就绪..."
    for i in $(seq 1 10); do
        if [ "$(docker inspect irip-minio-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ]; then
            log "MinIO 就绪"
            break
        fi
        sleep 2
    done
    
    log "基础设施全部就绪 ✅"
}

run_migrations() {
    log "检查迁移版本..."
    CURRENT=$(.venv/bin/alembic current 2>/dev/null | grep -oE '^[0-9]+' | tail -1 || true)
    if [ -z "${CURRENT}" ]; then
        log "全新数据库，执行迁移..."
        .venv/bin/alembic upgrade head 2>&1 | tail -5
    else
        log "当前迁移版本: ${CURRENT}（跳过迁移）"
    fi
}

run_bootstrap() {
    # 检查是否已有 admin 用户
    HAS_ADMIN=$(docker exec irip-postgres-1 psql -U irip -d irip -tAc "SELECT count(*) FROM app_user WHERE email='admin@irip.local'" 2>/dev/null || echo "0")
    if [ "$HAS_ADMIN" -gt 0 ] 2>/dev/null; then
        log "Bootstrap 已完成（admin 用户存在），跳过"
        return
    fi
    
    log "首次启动，执行 Bootstrap..."
    .venv/bin/python -m deployments.compose.bootstrap 2>&1 | grep -E "Bootstrap:|WARNING|ERROR" || true
    log "Bootstrap 完成 ✅"
}

start_api() {
    if [ -f "$PID_DIR/api.pid" ] && kill -0 "$(cat $PID_DIR/api.pid)" 2>/dev/null; then
        warn "API 已在运行（PID: $(cat $PID_DIR/api.pid)）"
        return
    fi
    
    log "启动 API（uvicorn :8000）..."
    .venv/bin/uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000 --env-file .env > /tmp/irip-api.log 2>&1 &
    echo $! > "$PID_DIR/api.pid"
    sleep 3
    
    if curl -s http://localhost:8000/api/v1/health/live | grep -q "ok"; then
        log "API 就绪 ✅ (http://localhost:8000)"
    else
        err "API 启动失败，查看 /tmp/irip-api.log"
        tail -10 /tmp/irip-api.log
    fi
}

start_worker() {
    if [ -f "$PID_DIR/worker.pid" ] && kill -0 "$(cat $PID_DIR/worker.pid)" 2>/dev/null; then
        warn "Worker 已在运行（PID: $(cat $PID_DIR/worker.pid)）"
        return
    fi
    
    log "启动 Celery Worker..."
    .venv/bin/celery -A apps.worker.celery_app worker --loglevel=info --queues=irip-normal,irip-ops,irip-research > /tmp/irip-worker.log 2>&1 &
    echo $! > "$PID_DIR/worker.pid"
    sleep 3
    log "Worker 就绪 ✅"
}

start_beat() {
    if [ -f "$PID_DIR/beat.pid" ] && kill -0 "$(cat $PID_DIR/beat.pid)" 2>/dev/null; then
        warn "Beat 已在运行（PID: $(cat $PID_DIR/beat.pid)）"
        return
    fi
    
    log "启动 Celery Beat..."
    .venv/bin/celery -A apps.worker.celery_app beat --loglevel=info > /tmp/irip-beat.log 2>&1 &
    echo $! > "$PID_DIR/beat.pid"
    sleep 2
    log "Beat 就绪 ✅"
}

start_web() {
    if [ -f "$PID_DIR/web.pid" ] && kill -0 "$(cat $PID_DIR/web.pid)" 2>/dev/null; then
        warn "前端已在运行（PID: $(cat $PID_DIR/web.pid)）"
        return
    fi
    
    log "启动前端（Vite :5173）..."
    cd apps/web
    npx vite --port 5173 > /tmp/irip-web.log 2>&1 &
    echo $! > "$PID_DIR/web.pid"
    cd "$SCRIPT_DIR"
    sleep 3
    
    if curl -s http://localhost:5173 | grep -q "html"; then
        log "前端就绪 ✅ (http://localhost:5173)"
    else
        warn "前端可能还在编译中，稍等片刻访问 http://localhost:5173"
    fi
}

stop_all() {
    log "停止服务..."
    for name in api worker beat web; do
        if [ -f "$PID_DIR/$name.pid" ]; then
            PID=$(cat "$PID_DIR/$name.pid")
            if kill -0 "$PID" 2>/dev/null; then
                kill "$PID" 2>/dev/null || true
                log "停止 $name (PID: $PID)"
            fi
            rm -f "$PID_DIR/$name.pid"
        fi
    done
    
    # 杀残留的 uvicorn / celery / vite 进程
    pkill -f "uvicorn apps.api.main:app" 2>/dev/null || true
    pkill -f "celery.*apps.worker.celery_app" 2>/dev/null || true
    pkill -f "vite --port 5173" 2>/dev/null || true
    
    docker compose -f compose.base.yaml -f compose.development.yaml stop 2>/dev/null || true
    log "全部停止 ✅"
}

# 只停应用层进程（不动 Docker 容器），用于 restart 子命令
stop_apps() {
    log "停止应用层进程（保留 Docker 容器）..."
    for name in api worker beat web; do
        if [ -f "$PID_DIR/$name.pid" ]; then
            PID=$(cat "$PID_DIR/$name.pid")
            if kill -0 "$PID" 2>/dev/null; then
                kill "$PID" 2>/dev/null || true
                log "停止 $name (PID: $PID)"
            fi
            rm -f "$PID_DIR/$name.pid"
        fi
    done
    pkill -f "uvicorn apps.api.main:app" 2>/dev/null || true
    pkill -f "celery.*apps.worker.celery_app" 2>/dev/null || true
    pkill -f "vite --port 5173" 2>/dev/null || true
    sleep 2
    log "应用层进程已停止 ✅"
}

show_status() {
    echo -e "\n${CYAN}═══ IRIP 服务状态 ═══${NC}\n"
    
    # Docker 基础设施
    for svc in postgres redis minio; do
        STATUS=$(docker inspect irip-${svc}-1 --format '{{.State.Health.Status}}' 2>/dev/null || echo "未启动")
        if [ "$STATUS" = "healthy" ]; then
            echo -e "  $svc: ${GREEN}✅ $STATUS${NC}"
        else
            echo -e "  $svc: ${RED}❌ $STATUS${NC}"
        fi
    done
    
    # 本地进程
    for name in api worker beat web; do
        if [ -f "$PID_DIR/$name.pid" ] && kill -0 "$(cat $PID_DIR/$name.pid)" 2>/dev/null; then
            echo -e "  $name: ${GREEN}✅ running (PID: $(cat $PID_DIR/$name.pid))${NC}"
        else
            echo -e "  $name: ${RED}❌ stopped${NC}"
        fi
    done
    
    # 迁移版本
    CURRENT=$(.venv/bin/alembic current 2>/dev/null | grep -oE '^[0-9]+' | tail -1 || true)
    CURRENT=${CURRENT:-未知}
    echo -e "\n  迁移版本: ${CYAN}${CURRENT}${NC}"
    
    # 心跳
    HB=$(docker exec irip-redis-1 redis-cli -a "$IRIP_REDIS_PASSWORD" GET irip:worker:heartbeat 2>/dev/null | tail -1 || echo "")
    if [ -n "$HB" ]; then
        echo -e "  Worker 心跳: ${GREEN}✅ $HB${NC}"
    else
        echo -e "  Worker 心跳: ${RED}❌ 无心跳${NC}"
    fi
    
    echo -e "\n${CYAN}═════════════════════${NC}\n"
}

# ---- 主逻辑 ----

case "${1:-up}" in
    up)
        log "IRIP 本地开发环境启动中..."
        start_infra
        run_migrations
        run_bootstrap
        start_api
        start_worker
        start_beat
        start_web
        echo ""
        log "全部就绪！"
        echo -e "  API:    ${CYAN}http://localhost:8000${NC}"
        echo -e "  前端:   ${CYAN}http://localhost:5173${NC}"
        echo -e "  MinIO:  ${CYAN}http://localhost:9001${NC} (irip / $IRIP_MINIO_SECRET_KEY)"
        echo -e "  登录:   ${CYAN}admin@irip.local / ${IRIP_BOOTSTRAP_ADMIN_PASSWORD:-agsdgfsdg21r34sf}${NC}"
        echo ""
        ;;
    restart)
        log "重启应用层服务（Docker 容器保持运行）..."
        stop_apps
        start_api
        start_worker
        start_beat
        start_web
        echo ""
        log "重启完成 ✅"
        echo -e "  API:    ${CYAN}http://localhost:8000${NC}"
        echo -e "  前端:   ${CYAN}http://localhost:5173${NC}"
        echo ""
        ;;
    down)
        stop_all
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: ./dev.sh [up|restart|down|status]"
        echo "  up      - 启动全部服务"
        echo "  restart - 重启应用层（保留 Docker 容器）"
        echo "  down    - 停止全部服务"
        echo "  status  - 查看服务状态"
        exit 1
        ;;
esac
