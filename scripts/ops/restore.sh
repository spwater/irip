#!/usr/bin/env bash
set -euo pipefail

# Host-orchestrated restore: stop app, run phases, restart app.
# The restore container never touches Docker socket; all service
# stop/start is performed here on the host.
#
# Usage:
#   restore.sh --environment <name> --manifest <path> --confirm <token>
#
# Environment variables:
#   IRIP_RESTORE_APPROVED_BY  Required for production restores (approver identity).
#   IRIP_BACKUP_DIR           Backup directory on host (default: ./backups)
#   IRIP_COMPOSE_FILES        Compose file overrides (default: compose.base.yaml compose.production.yaml)

ENVIRONMENT=""
MANIFEST=""
CONFIRM=""
BACKUP_DIR="${IRIP_BACKUP_DIR:-./backups}"
COMPOSE_FILES="${IRIP_COMPOSE_FILES:-compose.base.yaml compose.production.yaml}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --environment) ENVIRONMENT="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --confirm) CONFIRM="$2"; shift 2 ;;
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

if [ -z "$CONFIRM" ]; then
    echo "ERROR: --confirm token required"
    exit 1
fi

if [ -z "$ENVIRONMENT" ]; then
    echo "ERROR: --environment required"
    exit 1
fi

if [ "$ENVIRONMENT" = "production" ] && [ -z "${IRIP_RESTORE_APPROVED_BY:-}" ]; then
    echo "ERROR: production restore requires IRIP_RESTORE_APPROVED_BY"
    exit 1
fi

# Build compose file flags
COMPOSE_FLAGS=""
for f in $COMPOSE_FILES; do
    COMPOSE_FLAGS="$COMPOSE_FLAGS -f $f"
done

# If a manifest path was given, resolve the backup directory from it.
if [ -n "$MANIFEST" ] && [ -f "$MANIFEST" ]; then
    BACKUP_DIR="$(dirname "$MANIFEST")"
fi

echo "=== IRIP Restore (host-orchestrated) ==="
echo "Environment : $ENVIRONMENT"
echo "Backup dir  : $BACKUP_DIR"
if [ -n "${IRIP_RESTORE_APPROVED_BY:-}" ]; then
    echo "Approved by : $IRIP_RESTORE_APPROVED_BY"
fi
echo ""

# 等待 postgres 接受连接。PITR 恢复后 postgres 以 recovery 模式启动
# （recovery.signal + recovery_target_action='promote'），pg_isready 仅在
# WAL 回放完成并 promote 后返回成功，因此此处轮询直到就绪（带超时）。
wait_for_postgres() {
    local attempts=0
    local max_attempts="${IRIP_PG_WAIT_ATTEMPTS:-24}"   # 24 * 5s = 最长约 120s
    local interval="${IRIP_PG_WAIT_INTERVAL:-5}"
    local pg_user="${IRIP_DATABASE_USER:-irip}"
    local pg_db="${IRIP_DATABASE_NAME:-irip}"
    echo "Waiting for postgres to accept connections (WAL replay + promote)..."
    until docker compose $COMPOSE_FLAGS exec -T postgres \
        pg_isready -U "$pg_user" -d "$pg_db" >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge "$max_attempts" ]; then
            echo "ERROR: postgres did not become ready within $((attempts * interval))s"
            docker compose $COMPOSE_FLAGS logs --tail=50 postgres || true
            return 1
        fi
        sleep "$interval"
    done
    echo "postgres is ready."
}

# 阶段顺序（关键安全约束）：
#   validate（PG 仍运行，仅做 manifest 校验，不碰 data）
#   → stop 应用 + postgres（PITR 清空 pgdata 前必须停 PG，否则损坏运行中库）
#   → database（PG 已停，清空 + 解压 base.tar.gz + recovery.signal）
#   → start postgres + wait（recovery 完成方可 verify）
#   → objects（MinIO 恢复，不依赖 PG）
#   → verify（PG 已启动，冒烟查询有效）
#   → start 应用
echo "--- Phase: validate (PG 仍运行，manifest 校验不碰 data) ---"
docker compose $COMPOSE_FLAGS run --rm --no-deps restore \
    --phase validate --backup-dir "$BACKUP_DIR"

echo ""
echo "Stopping API/Worker/Scheduler + PostgreSQL..."
docker compose $COMPOSE_FLAGS stop api worker scheduler postgres

echo ""
echo "--- Phase: database (PG 已停，PITR 清空/恢复 pgdata 安全) ---"
docker compose $COMPOSE_FLAGS run --rm --no-deps restore \
    --phase database --backup-dir "$BACKUP_DIR"

echo ""
echo "Starting PostgreSQL (recovery mode → WAL replay → promote)..."
docker compose $COMPOSE_FLAGS up -d postgres
wait_for_postgres

echo ""
echo "--- Phase: migrate (PG 已 promote，pg_restore / alembic forward migrations) ---"
docker compose $COMPOSE_FLAGS run --rm --no-deps restore \
    --phase migrate --backup-dir "$BACKUP_DIR"

echo ""
echo "--- Phase: objects (MinIO 恢复，无 PG 依赖) ---"
docker compose $COMPOSE_FLAGS run --rm --no-deps restore \
    --phase objects --backup-dir "$BACKUP_DIR"

echo ""
echo "--- Phase: verify (PG 已启动，冒烟查询 + 引用完整性) ---"
docker compose $COMPOSE_FLAGS run --rm --no-deps restore \
    --phase verify --backup-dir "$BACKUP_DIR"

echo ""
echo "Restarting application services..."
docker compose $COMPOSE_FLAGS up -d api worker scheduler

echo ""
echo "Restore complete."
