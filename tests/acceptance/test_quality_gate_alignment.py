"""CI, local toolchain, and coverage gate alignment tests."""

import json
from pathlib import Path


def test_nvmrc_exists_and_is_22():
    nvmrc = Path(".nvmrc")
    assert nvmrc.exists(), ".nvmrc is missing"
    assert nvmrc.read_text().strip() == "22"


def test_package_manager_is_pnpm_11():
    pkg = json.loads(Path("apps/web/package.json").read_text())
    assert pkg["packageManager"] == "pnpm@11.15.1"


def test_pyproject_coverage_floor_is_50():
    pyproject = Path("pyproject.toml").read_text()
    assert "fail_under = 50" in pyproject


def test_ci_does_not_override_coverage_floor():
    workflow = Path(".github/workflows/ci.yml")
    if not workflow.exists():
        return
    text = workflow.read_text()
    assert "--cov-fail-under=30" not in text
    assert "--cov-fail-under=20" not in text
