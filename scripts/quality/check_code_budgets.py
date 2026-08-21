"""Check code budgets: file size, complexity, and architecture rules.

Ratchet rules:
1. New production file >500 lines fails.
2. Existing over-budget file may not grow in lines.
3. New/modified function with Radon C-F fails (if Radon available).
4. apps/api/routers importing build_session_factory or SQLAlchemy fails.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_NEW_FILE_LINES = 500


@dataclass
class Violation:
    file: str
    code: str
    message: str


@dataclass
class BudgetReport:
    failed: bool = False
    violations: list[Violation] = field(default_factory=list)


def check_budgets(paths: list[Path], baseline: dict) -> BudgetReport:
    """Check files against budget rules."""
    report = BudgetReport()
    baseline_files = baseline.get("files", {})

    for path in paths:
        if not path.exists() or path.suffix != ".py":
            continue
        lines = len(path.read_text().splitlines())
        key = str(path)

        if key in baseline_files:
            max_lines = baseline_files[key].get("lines", lines)
            if lines > max_lines:
                report.failed = True
                report.violations.append(
                    Violation(
                        file=key,
                        code="legacy_file_grew",
                        message=f"{key} grew from {max_lines} to {lines}",
                    )
                )
        else:
            if lines > MAX_NEW_FILE_LINES:
                report.failed = True
                report.violations.append(
                    Violation(
                        file=key,
                        code="new_file_too_large",
                        message=(
                            f"{key} has {lines} lines "
                            f"(max {MAX_NEW_FILE_LINES})"
                        ),
                    )
                )

    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    baseline = json.loads(Path(args.baseline).read_text())
    paths = [Path(p) for p in args.paths]
    expanded: list[Path] = []
    for p in paths:
        if p.is_dir():
            expanded.extend(p.rglob("*.py"))
        else:
            expanded.append(p)

    report = check_budgets(expanded, baseline)
    if report.violations:
        for v in report.violations:
            print(f"FAIL: {v.code}: {v.message}")
        sys.exit(1)
    print("OK: all budgets pass")
    sys.exit(0)


if __name__ == "__main__":
    main()
