"""Code budget ratchet tests."""

from pathlib import Path

BASELINE_PATH = Path("quality/code-budget-baseline.json")


def test_baseline_file_exists():
    assert BASELINE_PATH.exists(), "code-budget-baseline.json missing"


def test_budget_checker_exists():
    assert Path("scripts/quality/check_code_budgets.py").exists()


def test_new_file_over_500_lines_fails(tmp_path):
    from scripts.quality.check_code_budgets import check_budgets

    path = tmp_path / "new_service.py"
    path.write_text("x = 1\n" * 501)
    baseline = {"files": {}}
    report = check_budgets([path], baseline)
    assert report.failed
    assert any(v.code == "new_file_too_large" for v in report.violations)


def test_existing_debt_may_not_increase(tmp_path):
    from scripts.quality.check_code_budgets import check_budgets

    path = tmp_path / "legacy_service.py"
    path.write_text("x = 1\n" * 601)
    baseline = {"files": {str(path): {"lines": 600, "max_complexity": 10}}}
    report = check_budgets([path], baseline)
    assert report.failed
    assert any(v.code == "legacy_file_grew" for v in report.violations)
