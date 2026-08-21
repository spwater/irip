"""Plotly on-demand loading verification and compilation gate tests."""

from pathlib import Path


def test_plotly_is_dynamic_import():
    """Plotly must be loaded via dynamic import, not static import."""
    src = Path("apps/web/src")
    if not src.exists():
        return
    # Check no static import of plotly in source files
    for py_file in src.rglob("*.tsx"):
        content = py_file.read_text()
        # Dynamic import is OK: import("plotly...") or lazy(() => import(...))
        # Static import is NOT OK: import ... from "plotly..."
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("import ") and "plotly" in stripped.lower():
                # Must be a dynamic import (import())
                assert "import(" in stripped, (
                    f"Static plotly import in {py_file}: {stripped}"
                )


def test_tsc_config_exists():
    """TypeScript config must exist for type checking."""
    assert Path("apps/web/tsconfig.json").exists()


def test_eslint_config_exists():
    """ESLint config must exist."""
    assert Path("apps/web/eslint.config.js").exists() or Path(
        "apps/web/.eslintrc.js"
    ).exists()
