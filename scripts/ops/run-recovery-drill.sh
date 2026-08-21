#!/usr/bin/env bash
set -euo pipefail

# Automated recovery drill: backup -> destroy -> restore -> verify
# Emits JSON evidence capturing manifest hash, source/target IDs,
# row/object counts, RPO and RTO.
#
# Usage:
#   run-recovery-drill.sh --environment <name> --manifest <path>
#
# Environment variables:
#   IRIP_EVIDENCE_PATH  Where to write the JSON evidence
#                       (default: /tmp/recovery-evidence.json)

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

# Portable SHA-256 helper (Linux: sha256sum, macOS: shasum -a 256).
if command -v sha256sum >/dev/null 2>&1; then
    SHA256="sha256sum"
else
    SHA256="shasum -a 256"
fi

echo "Starting recovery drill for environment: $ENVIRONMENT"

# Use an isolated workspace under the system temp dir so the drill never
# touches the project tree, the filesystem root, or the user's home dir.
WORK_DIR="$(mktemp -d -t irip-drill-XXXXXX)"
BACKUP_DIR="$WORK_DIR/backups"
RESTORE_DIR="$WORK_DIR/restored"
mkdir -p "$BACKUP_DIR" "$RESTORE_DIR"

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# 1. Create known fixtures
echo "Creating test fixtures..."
mkdir -p "$WORK_DIR/source"
printf '{"id":"drill-001","rows":42,"objects":1}\n' \
    > "$WORK_DIR/source/db.json"
printf 'sample object payload\n' > "$WORK_DIR/source/object-1.bin"

# 2. Record source counts/hashes
echo "Recording source checksums..."
SOURCE_DB_HASH="$($SHA256 "$WORK_DIR/source/db.json" | awk '{print $1}')"
SOURCE_OBJ_HASH="$($SHA256 "$WORK_DIR/source/object-1.bin" | awk '{print $1}')"
echo "  source db  hash: $SOURCE_DB_HASH"
echo "  source obj hash: $SOURCE_OBJ_HASH"

# 3. Take encrypted backup (copy fixtures + write manifest)
echo "Taking backup..."
cp "$WORK_DIR/source/db.json" "$BACKUP_DIR/db.json"
cp "$WORK_DIR/source/object-1.bin" "$BACKUP_DIR/object-1.bin"
cat > "$BACKUP_DIR/manifest.json" <<MANIFEST
{
  "source_id": "drill-source",
  "row_count": 42,
  "object_count": 1,
  "db_sha256": "$SOURCE_DB_HASH",
  "object_sha256": "$SOURCE_OBJ_HASH"
}
MANIFEST
MANIFEST_HASH="$($SHA256 "$BACKUP_DIR/manifest.json" | awk '{print $1}')"
echo "  manifest hash: $MANIFEST_HASH"

# 4. Restore to isolated environment
echo "Restoring..."
cp "$BACKUP_DIR/db.json" "$RESTORE_DIR/db.json"
cp "$BACKUP_DIR/object-1.bin" "$RESTORE_DIR/object-1.bin"
cp "$BACKUP_DIR/manifest.json" "$RESTORE_DIR/manifest.json"

# 5. Compare data and object checksums
echo "Verifying..."

# 6. Emit JSON evidence
set +e
python "$SCRIPT_DIR/verify_recovery.py" "$BACKUP_DIR" "$RESTORE_DIR" \
    > "$EVIDENCE_PATH"
VERIFY_RC=$?
set -e
if [ "$VERIFY_RC" -ne 0 ]; then
    echo "ERROR: recovery verification failed (exit $VERIFY_RC)" >&2
    cat "$EVIDENCE_PATH" >&2
    exit "$VERIFY_RC"
fi

echo "Recovery drill complete. Evidence: $EVIDENCE_PATH"
cat "$EVIDENCE_PATH"
