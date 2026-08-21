"""Compilation gates: verify Plotly lazy loading, TS config validity, and lint rules."""

import json
from pathlib import Path


def test_plotly_is_dynamic_import():
    """Plotly library must be loaded via dynamic import, not static import."""
    src = Path("apps/web/src")
    if not src.exists():
        return
    for py_file in src.rglob("*.tsx"):
        content = py_file.read_text()
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("import ") and "plotly.js" in stripped.lower():
                assert "import(" in stripped, f"Static plotly.js import in {py_file}: {stripped}"


def test_tsconfig_has_strict_mode():
    """TypeScript config must enable strict mode."""
    tsconfig_path = Path("apps/web/tsconfig.json")
    assert tsconfig_path.exists(), "tsconfig.json missing"
    config = json.loads(tsconfig_path.read_text())
    compiler_opts = config.get("compilerOptions", {})
    assert compiler_opts.get("strict") is True, (
        "tsconfig.json must have compilerOptions.strict = true"
    )
    assert compiler_opts.get("noEmit") is True, (
        "tsconfig.json must have compilerOptions.noEmit = true"
    )


def test_eslint_config_has_typescript_rules():
    """ESLint config must include TypeScript and accessibility rules."""
    eslint_path = Path("apps/web/eslint.config.js")
    assert eslint_path.exists(), "eslint.config.js missing"
    content = eslint_path.read_text()
    # Must reference TypeScript ESLint
    assert "typescript-eslint" in content or "@typescript-eslint" in content, (
        "ESLint config must include TypeScript rules"
    )
    # Must reference accessibility rules
    assert "jsx-a11y" in content or "a11y" in content, (
        "ESLint config must include accessibility (jsx-a11y) rules"
    )
    # Must restrict deprecated APIs
    assert "destroyOnClose" in content or "no-restricted-syntax" in content, (
        "ESLint config must restrict deprecated destroyOnClose"
    )


def test_vite_config_has_manual_chunks():
    """Vite config must define manualChunks for bundle splitting."""
    vite_path = Path("apps/web/vite.config.ts")
    assert vite_path.exists(), "vite.config.ts missing"
    content = vite_path.read_text()
    assert "manualChunks" in content, "vite.config.ts must define manualChunks for vendor splitting"
    assert "react-vendor" in content, "manualChunks must split react-vendor"
    assert "chunkSizeWarningLimit" in content, "vite.config.ts must set chunkSizeWarningLimit"
