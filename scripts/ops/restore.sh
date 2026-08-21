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

echo "Stopping API/Worker/Scheduler..."
docker compose $COMPOSE_FLAGS stop api worker scheduler

echo ""
echo "--- Phase: validate ---"
docker compose $COMPOSE_FLAGS run --rm restore \
    --phase validate --backup-dir "$BACKUP_DIR"

echo ""
echo "--- Phase: database ---"
docker compose $COMPOSE_FLAGS run --rm restore \
    --phase database --backup-dir "$BACKUP_DIR"

echo ""
echo "--- Phase: objects ---"
docker compose $COMPOSE_FLAGS run --rm restore \
    --phase objects --backup-dir "$BACKUP_DIR"

echo ""
echo "--- Phase: verify ---"
docker compose $COMPOSE_FLAGS run --rm restore \
    --phase verify --backup-dir "$BACKUP_DIR"

echo ""
echo "Restarting services..."
docker compose $COMPOSE_FLAGS up -d api worker scheduler

echo ""
echo "Restore complete."
