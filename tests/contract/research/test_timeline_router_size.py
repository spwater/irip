"""Router file size budget: research_timeline.py must be under 500 lines."""

from pathlib import Path


def test_research_timeline_router_under_500_lines():
    """research_timeline.py must be under 500 lines or in baseline."""
    import json

    path = Path("apps/api/routers/research_timeline.py")
    lines = len(path.read_text().splitlines())
    baseline_path = Path("quality/code-budget-baseline.json")
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        key = str(path)
        if key in baseline.get("files", {}):
            max_lines = baseline["files"][key].get("lines", 500)
            assert lines <= max_lines, f"research_timeline.py grew from {max_lines} to {lines}"
            return
    assert lines <= 500, f"research_timeline.py is {lines} lines (max 500)"


def test_router_files_under_500_or_in_baseline():
    """All router files must be under 500 lines or listed in baseline."""
    baseline_path = Path("quality/code-budget-baseline.json")
    import json

    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    baseline_files = baseline.get("files", {})

    routers_dir = Path("apps/api/routers")
    for f in routers_dir.glob("*.py"):
        if f.name == "__init__.py":
            continue
        lines = len(f.read_text().splitlines())
        key = str(f)
        if key in baseline_files:
            max_lines = baseline_files[key].get("lines", lines)
            assert lines <= max_lines, f"{f.name} grew from {max_lines} to {lines}"
        else:
            assert lines <= 500, f"{f.name} is {lines} lines (max 500 for new files)"
