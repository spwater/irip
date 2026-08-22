"""Real-manifest verification: progressive vs full comparison of drill manifests.

These tests cover the *upgraded* ``verify_recovery`` behaviour where both the
backup and restore directories carry a real ``manifest.json`` (written by the
recovery drill).  They complement ``test_recovery_drill.py`` without changing
its existing assertions.
"""

import json


def _write_manifest(directory, **fields):
    """Write a manifest.json into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def test_real_manifests_match_yields_all_checks_pass(tmp_path):
    """Agreeing row/object counts + digests => all booleans True, counts carried."""
    from scripts.ops.verify_recovery import verify_recovery

    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    _write_manifest(
        backup_dir,
        row_count=10,
        object_count=3,
        database_sha256="a" * 64,
        objects_sha256="b" * 64,
        timestamp_t0=1000,
        timestamp_t1=1010,
    )
    _write_manifest(
        restore_dir,
        row_count=10,
        object_count=3,
        database_sha256="a" * 64,
        objects_sha256="b" * 64,
        timestamp_t0=1000,
        timestamp_t1=1010,
        timestamp_t2=1030,
    )

    evidence = verify_recovery(str(backup_dir), str(restore_dir))

    assert evidence["database_checksum_match"] is True
    assert evidence["object_checksum_match"] is True
    assert evidence["audit_chain_valid"] is True
    assert evidence["row_count"] == 10
    assert evidence["object_count"] == 3
    assert evidence["rpo_seconds"] == 10  # 1010 - 1000
    assert evidence["rto_seconds"] == 20  # 1030 - 1010
    assert len(evidence["manifest_hash"]) == 64  # sha256 of backup manifest


def test_row_mismatch_sets_database_checksum_match_false(tmp_path):
    """Different row counts => database_checksum_match False."""
    from scripts.ops.verify_recovery import verify_recovery

    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    _write_manifest(backup_dir, row_count=10, object_count=3)
    _write_manifest(restore_dir, row_count=7, object_count=3)

    evidence = verify_recovery(str(backup_dir), str(restore_dir))

    assert evidence["database_checksum_match"] is False
    assert evidence["object_checksum_match"] is True


def test_object_mismatch_sets_object_checksum_match_false(tmp_path):
    """Different object counts => object_checksum_match False."""
    from scripts.ops.verify_recovery import verify_recovery

    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    _write_manifest(backup_dir, row_count=10, object_count=3)
    _write_manifest(restore_dir, row_count=10, object_count=5)

    evidence = verify_recovery(str(backup_dir), str(restore_dir))

    assert evidence["object_checksum_match"] is False
    assert evidence["database_checksum_match"] is True


def test_digest_mismatch_breaks_audit_chain(tmp_path):
    """Diverging database_sha256 => audit_chain_valid False."""
    from scripts.ops.verify_recovery import verify_recovery

    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    _write_manifest(
        backup_dir, row_count=10, object_count=3, database_sha256="a" * 64
    )
    _write_manifest(
        restore_dir, row_count=10, object_count=3, database_sha256="c" * 64
    )

    evidence = verify_recovery(str(backup_dir), str(restore_dir))

    assert evidence["audit_chain_valid"] is False


def test_missing_manifests_degrades_to_defaults(tmp_path):
    """No manifest => skeleton defaults (all True, rpo/rto = 0, counts = 0)."""
    from scripts.ops.verify_recovery import verify_recovery

    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    backup_dir.mkdir(parents=True, exist_ok=True)
    restore_dir.mkdir(parents=True, exist_ok=True)

    evidence = verify_recovery(str(backup_dir), str(restore_dir))

    assert evidence["database_checksum_match"] is True
    assert evidence["object_checksum_match"] is True
    assert evidence["audit_chain_valid"] is True
    assert evidence["rpo_seconds"] == 0
    assert evidence["rto_seconds"] == 0
    assert evidence["row_count"] == 0
    assert evidence["object_count"] == 0


def test_rpo_rto_from_iso_timestamps(tmp_path):
    """ISO 8601 timestamps (with Z) are parsed for RPO/RTO."""
    from scripts.ops.verify_recovery import verify_recovery

    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    _write_manifest(
        backup_dir,
        row_count=1,
        object_count=1,
        timestamp_t0="2024-01-01T00:00:00Z",
        timestamp_t1="2024-01-01T00:01:00Z",
    )
    _write_manifest(
        restore_dir,
        row_count=1,
        object_count=1,
        timestamp_t2="2024-01-01T00:03:30Z",
    )

    evidence = verify_recovery(str(backup_dir), str(restore_dir))

    assert evidence["rpo_seconds"] == 60
    assert evidence["rto_seconds"] == 150
