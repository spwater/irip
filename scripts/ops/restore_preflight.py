#!/usr/bin/env python3
"""Restore preflight checks (simplified).

Runs lightweight validation before the host orchestrator kicks off the
actual restore phases.  This script does NOT perform any restore work;
it only verifies that the prerequisites are in place.

Checks:
  1. Backup directory exists and contains manifest.json.
  2. --confirm token is non-empty.
  3. For production: IRIP_RESTORE_APPROVED_BY is set.
  4. Docker daemon is reachable (host-side).

Usage:
  python restore_preflight.py --environment production \
      --backup-dir /backups/2026-01-01 --confirm <token>
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _check(label: str, ok: bool, detail: str = "") -> bool:
    """Print a pass/fail line and return the boolean result."""
    status: str = "PASS" if ok else "FAIL"
    suffix: str = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="IRIP restore preflight checks"
    )
    parser.add_argument(
        "--environment",
        type=str,
        required=True,
        help="Target environment name (e.g. production, staging)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Backup directory path (must contain manifest.json)",
    )
    parser.add_argument(
        "--confirm",
        type=str,
        default="",
        help="Confirmation token (must be non-empty)",
    )
    args: argparse.Namespace = parser.parse_args()

    print("=== IRIP Restore Preflight ===")
    print(f"  Environment: {args.environment}")

    all_ok: bool = True

    # 1. Confirm token
    all_ok &= _check(
        "Confirm token provided",
        bool(args.confirm),
    )

    # 2. Production approval
    if args.environment == "production":
        approved_by: str = os.getenv("IRIP_RESTORE_APPROVED_BY", "")
        all_ok &= _check(
            "IRIP_RESTORE_APPROVED_BY set (production)",
            bool(approved_by),
        )
    else:
        _check("IRIP_RESTORE_APPROVED_BY (non-production, skipped)", True)

    # 3. Backup directory + manifest
    if args.backup_dir is not None:
        all_ok &= _check(
            "Backup directory exists",
            args.backup_dir.exists(),
            str(args.backup_dir),
        )
        manifest: Path = args.backup_dir / "manifest.json"
        all_ok &= _check(
            "manifest.json present",
            manifest.exists(),
            str(manifest),
        )
    else:
        all_ok &= _check("Backup directory specified", False, "missing --backup-dir")

    # 4. Docker daemon reachable (host-side)
    docker_bin: str | None = shutil.which("docker")
    if docker_bin is None:
        all_ok &= _check("docker binary on PATH", False)
    else:
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=False,
        )
        all_ok &= _check(
            "Docker daemon reachable",
            result.returncode == 0,
        )

    print()
    if all_ok:
        print("Preflight: all checks passed.")
        sys.exit(0)
    else:
        print("Preflight: one or more checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
