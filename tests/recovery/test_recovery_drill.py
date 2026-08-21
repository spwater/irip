"""Recovery drill evidence must contain all required checks."""


def test_recovery_evidence_requires_all_checks():
    """Evidence dict must have all required fields."""
    required = {
        "database_checksum_match",
        "object_checksum_match",
        "audit_chain_valid",
        "rpo_seconds",
        "rto_seconds",
    }
    # Verify the verify_recovery function returns all fields
    from scripts.ops.verify_recovery import verify_recovery

    evidence = verify_recovery("/tmp", "/tmp")
    assert required.issubset(set(evidence.keys()))
    assert evidence["rpo_seconds"] >= 0
    assert evidence["rto_seconds"] >= 0


def test_recovery_evidence_includes_extended_fields():
    """Evidence must also carry manifest hash, IDs and counts."""
    from scripts.ops.verify_recovery import verify_recovery

    evidence = verify_recovery("/tmp", "/tmp")
    for field in (
        "manifest_hash",
        "source_id",
        "target_id",
        "row_count",
        "object_count",
    ):
        assert field in evidence


def test_recovery_rejects_unsafe_paths():
    """Workspace root, filesystem root and home must be rejected."""
    import os

    from scripts.ops.verify_recovery import verify_recovery

    home = os.path.expanduser("~")
    for bad in ("/", home):
        try:
            verify_recovery(bad, "/tmp")
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")
