"""Tests for the deterministic particle-size fixture generator.

Validates that ``examples/particle-size/generate.py`` produces a
reproducible set of 60 experiments (plus 2 duplicates, 3 self-check
failures, and 2 moisture warnings) with a stable manifest digest.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

# ── Module loading ──────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GENERATE_PATH = _PROJECT_ROOT / "examples" / "particle-size" / "generate.py"
_EXAMPLES_DIR = _PROJECT_ROOT / "examples" / "particle-size"

# Default seed used by the generator and committed reference files.
_SEED = 20260715


def _load_generate_module():
    """Dynamically load the generate.py module from the examples directory."""
    spec = importlib.util.spec_from_file_location("particle_generate", _GENERATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["particle_generate"] = module
    spec.loader.exec_module(module)
    return module


_gen = _load_generate_module()
generate_particle_fixture = _gen.generate_particle_fixture
ParticleFixtureSummary = _gen.ParticleFixtureSummary


# ── Helper ─────────────────────────────────────────────────────────────


def _generate(tmp_path: Path, seed: int = _SEED) -> tuple[ParticleFixtureSummary, Path]:
    """Generate a fixture into *tmp_path*/*out* and return the summary + dir."""
    out_dir = tmp_path / "out"
    summary = generate_particle_fixture(out_dir, seed=seed)
    return summary, out_dir


def _read_ground_truth(out_dir: Path) -> dict:
    """Load ground_truth.json from the output directory."""
    return json.loads((out_dir / "ground_truth.json").read_text(encoding="utf-8"))


def _read_manifest(out_dir: Path) -> dict:
    """Load manifest.json from the output directory."""
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


# ── Tests ──────────────────────────────────────────────────────────────


def test_particle_fixture_is_deterministic(tmp_path: Path) -> None:
    """Generating twice with the same seed produces the same manifest digest."""
    first = generate_particle_fixture(tmp_path / "first", seed=_SEED)
    second = generate_particle_fixture(tmp_path / "second", seed=_SEED)

    assert first.manifest_digest == second.manifest_digest
    assert first.experiment_count == 60
    assert first.duplicate_count == 2
    assert first.self_check_failures == 3
    assert first.moisture_warnings == 2


def test_manifest_digest_matches_committed(tmp_path: Path) -> None:
    """The generated manifest digest matches the committed expected_manifest.json."""
    summary, _ = _generate(tmp_path)
    committed_path = _EXAMPLES_DIR / "expected_manifest.json"
    committed = json.loads(committed_path.read_text(encoding="utf-8"))

    assert summary.manifest_digest == committed["manifest_digest"]


def test_d10_lt_d50_lt_d90(tmp_path: Path) -> None:
    """Non-failure, non-duplicate experiments satisfy D10 < D50 < D90."""
    _, out_dir = _generate(tmp_path)
    truth = _read_ground_truth(out_dir)

    for exp in truth["experiments"]:
        if not exp["is_duplicate_of"] and not exp["self_check_failure"]:
            assert exp["d10"] < exp["d50"], f"D10 >= D50 for {exp['id']}"
            assert exp["d50"] < exp["d90"], f"D50 >= D90 for {exp['id']}"


def test_duplicate_files_byte_identical(tmp_path: Path) -> None:
    """Duplicate files are byte-for-byte identical to their originals."""
    _, out_dir = _generate(tmp_path)
    truth = _read_ground_truth(out_dir)

    duplicates = [e for e in truth["experiments"] if e["is_duplicate_of"]]
    assert len(duplicates) == 2

    for dup in duplicates:
        original_id = dup["is_duplicate_of"]
        assert original_id is not None
        original_path = out_dir / f"{original_id}.xlsx"
        dup_path = out_dir / f"{dup['id']}.xlsx"
        assert original_path.read_bytes() == dup_path.read_bytes(), (
            f"Duplicate {dup['id']} is not byte-identical to {original_id}"
        )


def test_self_check_failures_labeled(tmp_path: Path) -> None:
    """Exactly 3 experiments are labeled as self-check failures."""
    _, out_dir = _generate(tmp_path)
    truth = _read_ground_truth(out_dir)

    failures = [e for e in truth["experiments"] if e["self_check_failure"]]
    assert len(failures) == 3

    # Verify each failure actually violates D10 < D50 < D90.
    for f in failures:
        assert f["d10"] >= f["d50"] or f["d50"] >= f["d90"], (
            f"Failure {f['id']} does not violate D10 < D50 < D90"
        )


def test_moisture_warnings_labeled(tmp_path: Path) -> None:
    """Exactly 2 experiments are labeled as moisture warnings (> 3.0%)."""
    _, out_dir = _generate(tmp_path)
    truth = _read_ground_truth(out_dir)

    warnings = [e for e in truth["experiments"] if e["moisture_warning"]]
    assert len(warnings) == 2

    for w in warnings:
        assert w["moisture"] > 3.0, f"Warning {w['id']} has moisture {w['moisture']} <= 3.0"


def test_total_file_count(tmp_path: Path) -> None:
    """Total file count: 62 Excel (60 originals + 2 duplicates) + 60 PDF = 122."""
    _, out_dir = _generate(tmp_path)
    manifest = _read_manifest(out_dir)

    excel_files = [f for f in manifest["files"] if f["kind"] == "excel"]
    pdf_files = [f for f in manifest["files"] if f["kind"] == "pdf"]

    assert len(excel_files) == 62  # 60 originals + 2 duplicates
    assert len(pdf_files) == 60  # one per experiment
    assert manifest["total_files"] == 122


def test_all_files_have_sha256(tmp_path: Path) -> None:
    """Every file in the manifest has a valid 64-char hex SHA-256 digest."""
    _, out_dir = _generate(tmp_path)
    manifest = _read_manifest(out_dir)

    for f in manifest["files"]:
        sha = f["sha256"]
        assert len(sha) == 64, f"Invalid SHA-256 length for {f['path']}: {len(sha)}"
        int(sha, 16)  # raises ValueError if not valid hex

    # Cross-check: recompute digest from the actual file bytes.
    for f in manifest["files"]:
        filepath = out_dir / f["path"]
        actual = hashlib.sha256(filepath.read_bytes()).hexdigest()
        assert actual == f["sha256"], f"SHA-256 mismatch for {f['path']}"


def test_different_seed_different_output(tmp_path: Path) -> None:
    """A different seed produces a different manifest digest."""
    default = generate_particle_fixture(tmp_path / "default", seed=_SEED)
    different = generate_particle_fixture(tmp_path / "different", seed=99999)

    assert default.manifest_digest != different.manifest_digest
