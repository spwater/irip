#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# IRIP 真实破坏→恢复演练（A5：隔离环境真实破坏恢复，产出 RPO/RTO 证据）
#
# 从「copy fixture」升级为「真实 PG/MinIO 破坏→恢复」编排：
#
#   阶段① 备份前基线  — 记录 DB 业务表行数 + MinIO 对象数，时间戳 T0
#   阶段② 真实备份    — docker compose run backup（pg_basebackup + mc mirror）
#                          产出含 SHA-256 校验和的 manifest.json
#   阶段③ 破坏        — truncate 业务表 + 删除 MinIO 对象，时间戳 T1
#   阶段④ 恢复        — restore 容器逐阶段恢复（validate/database/migrate/
#                          objects/verify，含 PG 停启 + PITR promote），时间戳 T2
#   阶段⑤ 校验+RPO/RTO — 重查行数/对象数与①对比；rpo=T1-T0，rto=T2-T1
#   阶段⑥ 输出证据    — 调用 verify_recovery.py 产出 evidence JSON
#
# 复用现有能力：
#   - deployments/compose/backup.py   （pg_basebackup + mc mirror + age 加密）
#   - deployments/compose/restore.py  （validate/database/migrate/objects/verify）
#   - 宿主编排模式参照 scripts/ops/restore.sh（PG 停启 + recovery promote）
#
# 安全护栏：
#   - 破坏仅在显式 --environment <drill|isolated|sandbox> 下执行；
#   - 环境名包含 production/dev（不区分大小写）直接拒绝；
#   - 顶部打印醒目「真实破坏」警告；
#   - 通过 docker compose -p <隔离项目名> 使用独立命名卷 + 独立备份目录，
#     绝不触碰 dev/prod 的 pgdata/miniodata/backups。
#
# 用法：
#   run-recovery-drill.sh --environment drill [--manifest <path>]
#
# 环境变量：
#   IRIP_EVIDENCE_PATH      证据 JSON 输出路径（默认 /tmp/recovery-evidence.json）
#   IRIP_DRILL_PROJECT      隔离的 compose project 名（默认 irip-drill）
#   IRIP_COMPOSE_FILES      compose 覆盖文件（默认 compose.base.yaml compose.development.yaml）
#   IRIP_DATABASE_PASSWORD  PG superuser 密码（未设置 IRIP_DRILL_DATABASE_ADMIN_URL 时必需）
#   IRIP_DRILL_DATABASE_ADMIN_URL  superuser 连接串（覆盖自动构造）
#   IRIP_DRILL_ROW_QUERY    基线行数聚合 SQL（默认核心业务表行数之和）
#   IRIP_DRILL_TRUNCATE_TABLES  破坏阶段 truncate 的表（默认 job,artifact_blob）
#   IRIP_DRILL_OBJECT_PREFIX    MinIO 删除前缀（默认删除整个 bucket）
#   其余 IRIP_MINIO_* / IRIP_DATABASE_* 沿用 backup.py / restore.py 约定
# =============================================================================

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
ENVIRONMENT=""
MANIFEST=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --environment) ENVIRONMENT="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

ENVIRONMENT="${ENVIRONMENT:-drill}"
EVIDENCE_PATH="${IRIP_EVIDENCE_PATH:-/tmp/recovery-evidence.json}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

log() {
    echo "[drill] $*"
}

now_epoch() {
    date +%s
}

# ---------------------------------------------------------------------------
# 安全护栏：环境名校验 + 醒目警告
# ---------------------------------------------------------------------------
cat >&2 <<'WARN'
================================================================
  WARNING: 此脚本会【真实破坏】目标环境的数据——
  截断/清空业务表数据、删除 MinIO 对象，再从备份真实恢复。
  仅限隔离 / 预生产环境执行，严禁在 dev / production 上运行。
================================================================
WARN

ENV_LOWER="$(printf '%s' "$ENVIRONMENT" | tr '[:upper:]' '[:lower:]')"

# 拒绝任何包含 production / dev 字样的环境名（不区分大小写）。
case "$ENV_LOWER" in
    *prod*|*dev*)
        die "refusing to run destructive drill in environment '$ENVIRONMENT' (name contains forbidden 'production'/'dev')"
        ;;
esac

# 破坏仅在显式隔离环境下执行（allowlist）。
DRILL_ALLOWED_ENVS="${IRIP_DRILL_ALLOWED_ENVS:-drill isolated sandbox}"
case " $DRILL_ALLOWED_ENVS " in
    *" $ENV_LOWER "*) : ;;
    *)
        die "refusing to run drill in '$ENVIRONMENT' (must be one of: $DRILL_ALLOWED_ENVS)"
        ;;
esac

# ---------------------------------------------------------------------------
# Compose 隔离编排：独立 project 名 → 独立命名卷；独立备份目录 → 不碰真实备份
# ---------------------------------------------------------------------------
export COMPOSE_PROJECT_NAME="${IRIP_DRILL_PROJECT:-irip-drill}"

COMPOSE_FILES="${IRIP_COMPOSE_FILES:-compose.base.yaml compose.development.yaml}"
COMPOSE_FLAGS=""
for f in $COMPOSE_FILES; do
    COMPOSE_FLAGS="$COMPOSE_FLAGS -f $f"
done

WORK_DIR="$(mktemp -d -t irip-drill-XXXXXX)"
# 独立的宿主机备份/WAL 目录（映射进 backup/restore 容器的 /backups）
DRILL_BACKUP_HOST="$WORK_DIR/backups"
DRILL_WAL_ARCHIVE_HOST="$WORK_DIR/wal_archive"
mkdir -p "$DRILL_BACKUP_HOST" "$DRILL_WAL_ARCHIVE_HOST"

export IRIP_BACKUP_HOST_DIR="$DRILL_BACKUP_HOST"
export IRIP_WAL_ARCHIVE_HOST_DIR="$DRILL_WAL_ARCHIVE_HOST"

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 连接 / 工具参数
# ---------------------------------------------------------------------------
PG_USER="${IRIP_DATABASE_USER:-irip}"
PG_DB="${IRIP_DATABASE_NAME:-irip}"
PG_PASSWORD="${IRIP_DATABASE_PASSWORD:-}"

MINIO_ENDPOINT="${IRIP_MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${IRIP_MINIO_ACCESS_KEY:-irip}"
MINIO_SECRET_KEY="${IRIP_MINIO_SECRET_KEY:-irip_dev_password}"
MINIO_BUCKET="${IRIP_MINIO_BUCKET:-irip-artifacts}"
MC_ALIAS="${IRIP_MINIO_MC_ALIAS:-irip}"
MC_CONFIG_DIR="/tmp/mc-drill"

# 基线行数聚合 SQL：核心业务表行数之和（与 restore.py SMOKE_QUERIES 对齐）
ROW_QUERY="${IRIP_DRILL_ROW_QUERY:-SELECT COALESCE((SELECT count(*) FROM app_user),0) + COALESCE((SELECT count(*) FROM department),0) + COALESCE((SELECT count(*) FROM role),0) + COALESCE((SELECT count(*) FROM artifact_blob),0) + COALESCE((SELECT count(*) FROM job),0);}"

# 破坏阶段 truncate 的目录表（逗号分隔）
TRUNCATE_TABLES="${IRIP_DRILL_TRUNCATE_TABLES:-job,artifact_blob}"

# ---------------------------------------------------------------------------
# 运维容器执行（backup / restore）：覆盖 secret 文件为显式 superuser 连接串
# ---------------------------------------------------------------------------
build_ops_admin_url() {
    local url="${IRIP_DRILL_DATABASE_ADMIN_URL:-}"
    if [ -n "$url" ]; then
        printf '%s' "$url"
        return
    fi
    if [ -z "$PG_PASSWORD" ]; then
        die "IRIP_DATABASE_PASSWORD not set and IRIP_DRILL_DATABASE_ADMIN_URL not provided"
    fi
    printf 'postgresql+psycopg://%s:%s@postgres:5432/%s' "$PG_USER" "$PG_PASSWORD" "$PG_DB"
}

OPS_ADMIN_URL="$(build_ops_admin_url)"

run_ops() {
    # $@ = service + args（backup / restore --phase ...）
    docker compose $COMPOSE_FLAGS run --rm --no-deps \
        -e IRIP_DATABASE_ADMIN_URL_FILE= \
        -e "IRIP_DATABASE_ADMIN_URL=$OPS_ADMIN_URL" \
        -e "IRIP_ENV=drill" \
        "$@"
}

# 在 postgres 容器内执行 psql 查询（-tAc：仅数值 / 单值输出）
pg_exec() {
    docker compose $COMPOSE_FLAGS exec -T postgres \
        psql -U "$PG_USER" -d "$PG_DB" -tAc "$1"
}

# ---------------------------------------------------------------------------
# MinIO 辅助（mc CLI，与 backup.py/restore.py 使用同一工具）
# ---------------------------------------------------------------------------
mc_alias() {
    # 宿主机需安装 mc CLI（dev/pre-prod compose 已暴露 minio 9000 端口）。
    # 与 backup.py/restore.py 使用同一 MinIO 凭据约定。
    command -v mc >/dev/null 2>&1 || die "mc CLI not found on host (required for MinIO baseline/destroy)"
    mc --config-dir "$MC_CONFIG_DIR" alias set \
        "$MC_ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
}

mc_object_count() {
    mc_alias
    mc --config-dir "$MC_CONFIG_DIR" ls --recursive \
        "$MC_ALIAS/$MINIO_BUCKET" 2>/dev/null | wc -l | tr -d ' '
}

mc_destroy_objects() {
    mc_alias
    local target="$MC_ALIAS/$MINIO_BUCKET"
    if [ -n "${IRIP_DRILL_OBJECT_PREFIX:-}" ]; then
        target="$target/$IRIP_DRILL_OBJECT_PREFIX"
    fi
    mc --config-dir "$MC_CONFIG_DIR" rm --recursive --force "$target" >/dev/null
}

# ---------------------------------------------------------------------------
# wait_for_postgres：PITR recovery → WAL 回放 → promote 完成前轮询
# ---------------------------------------------------------------------------
wait_for_postgres() {
    local attempts=0
    local max_attempts="${IRIP_PG_WAIT_ATTEMPTS:-24}"
    local interval="${IRIP_PG_WAIT_INTERVAL:-5}"
    log "waiting for postgres (WAL replay + promote) ..."
    until docker compose $COMPOSE_FLAGS exec -T postgres \
        pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge "$max_attempts" ]; then
            docker compose $COMPOSE_FLAGS logs --tail=50 postgres || true
            die "postgres did not become ready within $((attempts * interval))s"
        fi
        sleep "$interval"
    done
    log "postgres is ready."
}

# 查找最新备份 manifest（<id>/manifest.json）
find_latest_backup_manifest() {
    find "$DRILL_BACKUP_HOST" -mindepth 2 -maxdepth 2 -name manifest.json -print \
        2>/dev/null | sort | tail -n 1
}

# 从真实备份 manifest 读取摘要字段
manifest_field() {
    local file="$1"
    local key="$2"
    python - "$file" "$key" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
print(data.get(sys.argv[2], ""))
PY
}

# =============================================================================
# 阶段① 备份前基线
# =============================================================================
log "=== phase 1/6: baseline (T0) ==="
log "ensuring postgres + minio are up for the drill project ..."
docker compose $COMPOSE_FLAGS up -d postgres minio >/dev/null
wait_for_postgres

T0="$(now_epoch)"
BASELINE_ROWS="$(pg_exec "$ROW_QUERY" | tr -d '[:space:]')"
BASELINE_ROWS="${BASELINE_ROWS:-0}"
BASELINE_OBJECTS="$(mc_object_count)"
BASELINE_OBJECTS="${BASELINE_OBJECTS:-0}"
log "baseline rows=$BASELINE_ROWS objects=$BASELINE_OBJECTS t0=$T0"

# =============================================================================
# 阶段② 真实备份
# =============================================================================
log "=== phase 2/6: real backup (pg_basebackup + mc mirror) ==="
if [ -n "$MANIFEST" ] && [ -f "$MANIFEST" ]; then
    BACKUP_MANIFEST_HOST="$MANIFEST"
    BACKUP_ID_DIR="$(dirname "$MANIFEST")"
    log "using pre-existing backup manifest: $BACKUP_MANIFEST_HOST"
else
    run_ops backup
    BACKUP_MANIFEST_HOST="$(find_latest_backup_manifest)"
    [ -n "$BACKUP_MANIFEST_HOST" ] || die "backup produced no manifest.json under $DRILL_BACKUP_HOST"
    BACKUP_ID_DIR="$(dirname "$BACKUP_MANIFEST_HOST")"
fi
CONTAINER_BACKUP_DIR="/backups/$(basename "$BACKUP_ID_DIR")"
log "backup manifest host : $BACKUP_MANIFEST_HOST"
log "backup dir (container): $CONTAINER_BACKUP_DIR"

REAL_DB_SHA="$(manifest_field "$BACKUP_MANIFEST_HOST" "database_sha256")"
REAL_OBJ_SHA="$(manifest_field "$BACKUP_MANIFEST_HOST" "objects_sha256")"
REAL_BACKUP_ID="$(manifest_field "$BACKUP_MANIFEST_HOST" "backup_id")"
log "real backup: id=${REAL_BACKUP_ID} db_sha256=${REAL_DB_SHA:0:12}... objects_sha256=${REAL_OBJ_SHA:0:12}..."

# =============================================================================
# 阶段③ 破坏
# =============================================================================
log "=== phase 3/6: destroy (T1) ==="
T1="$(now_epoch)"

log "truncating business tables: $TRUNCATE_TABLES"
pg_exec "TRUNCATE TABLE $TRUNCATE_TABLES CASCADE;"

log "deleting MinIO objects: $MC_ALIAS/$MINIO_BUCKET"
mc_destroy_objects
log "destroyed at t1=$T1"

# =============================================================================
# 阶段④ 恢复（restore 容器逐阶段：validate/database/migrate/objects/verify）
# =============================================================================
log "=== phase 4/6: restore (T2 measured after) ==="

log "--- restore phase: validate ---"
run_ops restore --phase validate --backup-dir "$CONTAINER_BACKUP_DIR"

log "stopping postgres (PITR clear/pgdata requires stopped PG) ..."
docker compose $COMPOSE_FLAGS stop postgres >/dev/null

log "--- restore phase: database (PITR) ---"
run_ops restore --phase database --backup-dir "$CONTAINER_BACKUP_DIR"

log "starting postgres (recovery mode → WAL replay → promote) ..."
docker compose $COMPOSE_FLAGS up -d postgres >/dev/null
wait_for_postgres

log "--- restore phase: migrate ---"
run_ops restore --phase migrate --backup-dir "$CONTAINER_BACKUP_DIR"

log "--- restore phase: objects ---"
run_ops restore --phase objects --backup-dir "$CONTAINER_BACKUP_DIR"

log "--- restore phase: verify ---"
run_ops restore --phase verify --backup-dir "$CONTAINER_BACKUP_DIR"

T2="$(now_epoch)"
log "restore complete at t2=$T2"

# =============================================================================
# 阶段⑤ 校验 + RPO/RTO
# =============================================================================
log "=== phase 5/6: verify + RPO/RTO ==="
RESTORED_ROWS="$(pg_exec "$ROW_QUERY" | tr -d '[:space:]')"
RESTORED_ROWS="${RESTORED_ROWS:-0}"
RESTORED_OBJECTS="$(mc_object_count)"
RESTORED_OBJECTS="${RESTORED_OBJECTS:-0}"

RPO_SECONDS=$((T1 - T0))
RTO_SECONDS=$((T2 - T1))
log "restored rows=$RESTORED_ROWS (baseline=$BASELINE_ROWS) objects=$RESTORED_OBJECTS (baseline=$BASELINE_OBJECTS)"
log "RPO=${RPO_SECONDS}s (T1-T0)  RTO=${RTO_SECONDS}s (T2-T1)"

# 写入 drill 证据清单：backup_dir（基线）+ restore_dir（恢复后）
VERIFY_BACKUP_DIR="$WORK_DIR/evidence/backup"
VERIFY_RESTORE_DIR="$WORK_DIR/evidence/restore"
mkdir -p "$VERIFY_BACKUP_DIR" "$VERIFY_RESTORE_DIR"

cat > "$VERIFY_BACKUP_DIR/manifest.json" <<MANIFEST
{
  "schema": "irip-recovery-drill/v1",
  "phase": "baseline",
  "environment": "$ENVIRONMENT",
  "backup_id": "$REAL_BACKUP_ID",
  "row_count": $BASELINE_ROWS,
  "object_count": $BASELINE_OBJECTS,
  "database_sha256": "$REAL_DB_SHA",
  "objects_sha256": "$REAL_OBJ_SHA",
  "timestamp_t0": $T0,
  "timestamp_t1": $T1,
  "source_id": "$ENVIRONMENT"
}
MANIFEST

cat > "$VERIFY_RESTORE_DIR/manifest.json" <<MANIFEST
{
  "schema": "irip-recovery-drill/v1",
  "phase": "post_restore",
  "environment": "$ENVIRONMENT",
  "backup_id": "$REAL_BACKUP_ID",
  "row_count": $RESTORED_ROWS,
  "object_count": $RESTORED_OBJECTS,
  "database_sha256": "$REAL_DB_SHA",
  "objects_sha256": "$REAL_OBJ_SHA",
  "timestamp_t0": $T0,
  "timestamp_t1": $T1,
  "timestamp_t2": $T2,
  "target_id": "$ENVIRONMENT"
}
MANIFEST

# =============================================================================
# 阶段⑥ 输出证据 JSON
# =============================================================================
log "=== phase 6/6: emit evidence ==="
set +e
python "$SCRIPT_DIR/verify_recovery.py" "$VERIFY_BACKUP_DIR" "$VERIFY_RESTORE_DIR" \
    > "$EVIDENCE_PATH"
VERIFY_RC=$?
set -e
if [ "$VERIFY_RC" -ne 0 ]; then
    echo "ERROR: recovery verification failed (exit $VERIFY_RC)" >&2
    cat "$EVIDENCE_PATH" >&2
    exit "$VERIFY_RC"
fi

log "recovery drill complete. Evidence: $EVIDENCE_PATH"
cat "$EVIDENCE_PATH"
