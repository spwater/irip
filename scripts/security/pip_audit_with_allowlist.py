"""Run pip-audit with vulnerability exemptions driven by the allowlist.

Reads the vulnerability allowlist YAML (``<allowlist>``) and extracts each
exception's ``id`` field, then invokes ``pip-audit`` passing ``--ignore-vuln
<id>`` for every entry.  Any extra CLI arguments are forwarded to pip-audit
verbatim (typically ``--skip-editable``).

This removes the hardcoded ``--ignore-vuln`` from CI so that every exemption
is owner+expiry audited by ``check_vulnerability_allowlist.py`` before this
script runs.  Exit codes are inherited from pip-audit (0 = clean, non-zero =
vulnerabilities found or tool failure).
"""

import subprocess
import sys
from pathlib import Path

import yaml


def extract_vuln_ids(path: Path) -> list[str]:
    """Extract the vuln ``id`` of every exception entry from the allowlist.

    Args:
        path: Path to the allowlist YAML file.

    Returns:
        list[str]: Vulnerability IDs in declaration order (deduplicated).
    """
    data: dict = yaml.safe_load(path.read_text()) or {}
    exceptions = data.get("exceptions", [])
    ids: list[str] = []
    seen: set[str] = set()
    for entry in exceptions:
        if not isinstance(entry, dict):
            continue
        vuln_id = str(entry.get("id", "")).strip()
        if vuln_id and vuln_id not in seen:
            ids.append(vuln_id)
            seen.add(vuln_id)
    return ids


def build_ignore_args(ids: list[str]) -> list[str]:
    """Build ``--ignore-vuln <id>`` argument pairs for pip-audit.

    Args:
        ids: Vulnerability IDs.

    Returns:
        list[str]: Flattened flag/value pairs.
    """
    args: list[str] = []
    for vuln_id in ids:
        args.extend(["--ignore-vuln", vuln_id])
    return args


def _main(argv: list[str]) -> int:
    """CLI entry point. Returns the pip-audit exit code (or usage error)."""
    if len(argv) < 2:
        print("Usage: pip_audit_with_allowlist.py <allowlist.yaml> [pip-audit args...]")
        return 2

    allowlist_path = Path(argv[1])
    if not allowlist_path.exists():
        print(f"Error: allowlist not found: {allowlist_path}")
        return 2

    ids = extract_vuln_ids(allowlist_path)
    command = ["pip-audit", *build_ignore_args(ids), *argv[2:]]
    print("Running:", " ".join(command))
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
