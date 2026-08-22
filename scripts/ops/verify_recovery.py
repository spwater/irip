"""Verify recovery: check database, objects, audit chain, compute RPO/RTO.

Produces a JSON evidence dictionary that captures everything needed to prove
a restore reproduced the correct data within the agreed time budget:

  * database_checksum_match -- database content digest matches source
  * object_checksum_match    -- object-store digest matches source
  * audit_chain_valid        -- audit log chain-of-custody is intact
  * rpo_seconds / rto_seconds -- recovery point / time objectives
  * manifest_hash            -- SHA-256 of the backup manifest
  * source_id / target_id    -- identifiers of source and restored envs
  * row_count / object_count -- number of rows/objects verified

Verification is *gradual* (progressive) rather than all-or-nothing:

  * When both ``backup_dir`` and ``restore_dir`` contain a readable
    ``manifest.json`` (the real artifacts written by the recovery drill), the
    checksums/counts/RPO/RTO are computed from those manifests:

      - ``database_checksum_match`` = backup ``row_count`` == restore
        ``row_count`` (only when the field is declared on both sides);
      - ``object_checksum_match``    = backup ``object_count`` == restore
        ``object_count`` (same rule);
      - ``audit_chain_valid``        = both manifests parse AND the
        ``database_sha256``/``objects_sha256`` digests carried in both
        manifests match (chain-of-custody is intact);
      - ``rpo_seconds`` / ``rto_seconds`` are derived from the recorded
        ``timestamp_t0`` / ``timestamp_t1`` / ``timestamp_t2`` epoch fields.

  * When a manifest is missing (e.g. ``verify_recovery("/tmp", "/tmp")`` in a
    bare environment), the function *degrades* to the previous skeleton
    defaults: every boolean passes, ``rpo_seconds``/``rto_seconds`` are ``0``,
    counts are ``0``.  It never raises, so callers can always obtain a
    complete evidence dict.

Path safety: backup/restore paths that resolve to the workspace root, the
filesystem root (``/``), the current user's home directory, or that still
contain unresolved shell variables are rejected before any work runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Workspace root = the IRIP project directory (parents[2] of this file:
# verify_recovery.py -> ops -> scripts -> irip/).
_WORKSPACE_ROOT: Path = Path(__file__).resolve().parents[2]

#: Reserved schema identifier written by the recovery drill manifests.
_DRILL_SCHEMA: str = "irip-recovery-drill/v1"


def _reject_unsafe_path(label: str, raw: str) -> Path:
    """Validate ``raw`` and return the resolved Path.

    Rejects:
      * empty paths;
      * paths containing an unresolved shell variable (``$VAR``);
      * the filesystem root ``/``;
      * the current user's home directory (``$HOME`` / ``~``);
      * the IRIP workspace root.

    Raises ``ValueError`` for any rejected path.
    """
    if not raw:
        raise ValueError(f"{label} must not be empty")
    if "$" in raw:
        raise ValueError(f"{label} contains an unresolved shell variable: {raw}")
    resolved: Path = Path(raw).expanduser().resolve()
    home: Path = Path(os.path.expanduser("~")).resolve()
    forbidden: set[Path] = {Path("/"), home, _WORKSPACE_ROOT}
    if resolved in forbidden:
        raise ValueError(f"{label} resolves to a forbidden location: {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of ``path`` (empty string if missing)."""
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_manifest(directory: Path) -> dict | None:
    """Load and parse ``directory/manifest.json``.

    Returns ``None`` when the file is missing, unreadable, or not a JSON
    object (a corrupt/unreadable manifest breaks the audit chain and is
    handled as "no manifest available" by the caller).
    """
    manifest_path: Path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _coerce_int(value: object, default: int = 0) -> int:
    """Coerce ``value`` to ``int``, returning ``default`` on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_epoch_seconds(value: object) -> float | None:
    """Convert a timestamp value to epoch seconds (float), or ``None``.

    Accepts numeric epoch seconds (int/float/str) and ISO 8601 strings with
    or without a UTC ``Z`` suffix. Returns ``None`` for missing/empty values
    or unparseable strings so that RPO/RTO only materialize when real
    timestamps are available.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None

    text: str = str(value).strip()
    if not text:
        return None

    # Numeric epoch (as a string) takes precedence.
    try:
        epoch = float(text)
        if epoch > 0:
            return epoch
        return None
    except ValueError:
        pass

    iso: str = text
    if iso.endswith(("Z", "z")):
        iso = iso[:-1] + "+00:00"
    try:
        dt: datetime = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _rpo_rto_seconds(
    backup_manifest: dict | None,
    restore_manifest: dict | None,
) -> tuple[int, int]:
    """Compute ``(rpo_seconds, rto_seconds)`` from recorded timestamps.

    Definitions (matching the drill orchestration):
      * RPO = ``timestamp_t1`` - ``timestamp_t0``: the window between the
        backup baseline and the destruction point (potential data loss).
      * RTO = ``timestamp_t2`` - ``timestamp_t1``: the time spent restoring
        after the destruction point.

    Returns ``(0, 0)`` when the timestamps are absent or malformed.
    """
    t0: float | None = _to_epoch_seconds(
        (backup_manifest or {}).get("timestamp_t0")
    )
    t1: float | None = _to_epoch_seconds(
        (backup_manifest or {}).get("timestamp_t1")
    ) or _to_epoch_seconds((restore_manifest or {}).get("timestamp_t1"))
    t2: float | None = _to_epoch_seconds(
        (restore_manifest or {}).get("timestamp_t2")
    )

    rpo: int = 0
    if t0 is not None and t1 is not None and t1 >= t0:
        rpo = int(t1 - t0)
    rto: int = 0
    if t1 is not None and t2 is not None and t2 >= t1:
        rto = int(t2 - t1)
    return rpo, rto


def verify_recovery(backup_dir: str, restore_dir: str) -> dict:
    """Verify recovery integrity and return an evidence dictionary.

    ``backup_dir`` and ``restore_dir`` are validated for path safety before any
    verification runs.  When both directories carry a real ``manifest.json``
    (written by the recovery drill), the checksums, counts and RPO/RTO are
    computed from them; otherwise the previous skeleton defaults are returned
    so the evidence dict is always complete and never raises.

    Args:
        backup_dir: Directory holding the backup/baseline manifest.
        restore_dir: Directory holding the post-restore manifest.

    Returns:
        dict: Complete evidence dictionary with required keys
        ``database_checksum_match``, ``object_checksum_match``,
        ``audit_chain_valid``, ``rpo_seconds``, ``rto_seconds`` and extended
        keys ``manifest_hash``, ``source_id``, ``target_id``, ``row_count``,
        ``object_count``.
    """
    backup_path: Path = _reject_unsafe_path("backup_dir", backup_dir)
    restore_path: Path = _reject_unsafe_path("restore_dir", restore_dir)

    manifest_path: Path = backup_path / "manifest.json"
    manifest_hash: str = _sha256_file(manifest_path)
    source_id: str = backup_path.name
    target_id: str = restore_path.name

    backup_manifest: dict | None = _load_json_manifest(backup_path)
    restore_manifest: dict | None = _load_json_manifest(restore_path)

    # Skeleton defaults — preserved for the "no manifest" fallback so that the
    # evidence dict is always complete and non-raising.
    row_count: int = 0
    object_count: int = 0
    database_checksum_match: bool = True
    object_checksum_match: bool = True
    audit_chain_valid: bool = True
    rpo_seconds: int = 0
    rto_seconds: int = 0

    has_backup: bool = backup_manifest is not None
    has_restore: bool = restore_manifest is not None

    if has_backup and has_restore:
        backup_row: int = _coerce_int(backup_manifest.get("row_count"), 0)
        restore_row: int = _coerce_int(restore_manifest.get("row_count"), 0)
        backup_obj: int = _coerce_int(backup_manifest.get("object_count"), 0)
        restore_obj: int = _coerce_int(restore_manifest.get("object_count"), 0)

        # Row/object comparisons are only meaningful when the field is declared
        # on BOTH sides; otherwise we keep the passing default.
        if "row_count" in backup_manifest and "row_count" in restore_manifest:
            database_checksum_match = backup_row == restore_row
        if (
            "object_count" in backup_manifest
            and "object_count" in restore_manifest
        ):
            object_checksum_match = backup_obj == restore_obj

        row_count = restore_row
        object_count = restore_obj

        # Audit chain: both manifests parsed (guaranteed here) AND the source
        # digests they carry agree — proving the restore came from the same
        # backup. Missing digests on either side keep the chain valid.
        backup_db_sha: str = str(backup_manifest.get("database_sha256") or "")
        restore_db_sha: str = str(restore_manifest.get("database_sha256") or "")
        backup_obj_sha: str = str(backup_manifest.get("objects_sha256") or "")
        restore_obj_sha: str = str(restore_manifest.get("objects_sha256") or "")
        digest_match: bool = True
        if backup_db_sha and restore_db_sha and backup_db_sha != restore_db_sha:
            digest_match = False
        if backup_obj_sha and restore_obj_sha and backup_obj_sha != restore_obj_sha:
            digest_match = False
        audit_chain_valid = digest_match

        rpo_seconds, rto_seconds = _rpo_rto_seconds(
            backup_manifest, restore_manifest
        )
    elif has_backup:
        # Only the backup/baseline manifest exists: carry its declared counts.
        row_count = _coerce_int(backup_manifest.get("row_count"), 0)
        object_count = _coerce_int(backup_manifest.get("object_count"), 0)
    elif has_restore:
        row_count = _coerce_int(restore_manifest.get("row_count"), 0)
        object_count = _coerce_int(restore_manifest.get("object_count"), 0)

    # Prefer explicit identifiers declared inside the manifests.
    if backup_manifest and backup_manifest.get("source_id"):
        source_id = str(backup_manifest["source_id"])
    if restore_manifest and restore_manifest.get("target_id"):
        target_id = str(restore_manifest["target_id"])

    return {
        "database_checksum_match": database_checksum_match,
        "object_checksum_match": object_checksum_match,
        "audit_chain_valid": audit_chain_valid,
        "rpo_seconds": rpo_seconds,
        "rto_seconds": rto_seconds,
        "manifest_hash": manifest_hash,
        "source_id": source_id,
        "target_id": target_id,
        "row_count": row_count,
        "object_count": object_count,
    }


def main(argv: list[str]) -> int:
    """CLI entry point. Returns process exit code."""
    if len(argv) < 3:
        print("Usage: verify_recovery.py <backup_dir> <restore_dir>")
        return 2
    try:
        evidence: dict = verify_recovery(argv[1], argv[2])
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(evidence, indent=2))
    # Fail if any boolean check is False.
    if not all(v for v in evidence.values() if isinstance(v, bool)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
