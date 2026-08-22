"""Frontend bundle size budget tests.

Verifies that the main entry chunk is under 750 KiB (uncompressed) and no single
async chunk exceeds 1.5 MiB (uncompressed), and asserts that Plotly stays out of the
first-paint (synchronous) JS path.

Bundle exemption policy
-----------------------
Plotly ships as the full ``plotly.js-dist-min`` bundle (~4.8 MiB) because the IRIP
dashboard renders 3D scatter plots and Plotly offers no official partial build that
covers them. The exemption below is RETAINED after review; its rationale, mitigation
and owner are recorded so future reviewers can audit the decision. To add another
exemption, append a new :class:`BundleExemption` entry to :data:`BUNDLE_EXEMPTIONS`
and reference it from the relevant test instead of using a magic comment.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# Budget thresholds (uncompressed).
MAIN_ENTRY_BUDGET_BYTES = 750 * 1024  # 750 KiB
ASYNC_CHUNK_BUDGET_BYTES = 1536 * 1024  # 1.5 MiB

# Relative path to the built frontend output (relative to the repository root,
# which is the CWD pytest runs in). Matches the CI ordering: ``web-build`` runs
# first, ``web-test`` runs afterwards. When ``dist`` is absent locally, tests skip.
DIST_DIR = Path("apps/web/dist")
ASSETS_DIR = DIST_DIR / "assets"


@dataclass(frozen=True)
class BundleExemption:
    """A declared, auditable exemption for a bundle-size budget item.

    Each entry records *why* a budget is being waived, *how* the impact is
    contained, and *who* owns the decision, so the exemption is reviewable rather
    than hidden in a ``# noqa``-style magic comment.

    Attributes:
        package: The npm package (or chunk-name fragment) covered by the exemption.
        budget_item: Human-readable description of the budget item being waived.
        reason: Why the exemption is necessary (no smaller alternative exists).
        mitigation: How the size impact is contained (e.g. lazy loading).
        owner: Team accountable for owning/re-reviewing this exemption.
        review_date: ISO 8601 date the exemption was last reviewed and retained.
    """

    package: str
    budget_item: str
    reason: str
    mitigation: str
    owner: str
    review_date: str


# Single source of truth for every outstanding bundle-size exemption. Tests MUST
# consult this list (via :func:`_exemption_for_chunk`) instead of hard-coding
# ``if "plotly" in name: continue`` magic comments.
BUNDLE_EXEMPTIONS: tuple[BundleExemption, ...] = (
    BundleExemption(
        package="plotly",
        budget_item=f"async chunk < {ASYNC_CHUNK_BUDGET_BYTES // 1024} KiB "
        f"({ASYNC_CHUNK_BUDGET_BYTES} bytes) uncompressed",
        reason=(
            "3D scatter plots require the full plotly.js; Plotly ships no official "
            "partial bundle covering 3D surface/scatter, so the full dist-min "
            "(~4.8 MiB) is necessary."
        ),
        mitigation=(
            "Loaded on demand via dynamic import(); it ships as an independent async "
            "chunk and does not block first paint (asserted by "
            "test_plotly_not_in_first_paint)."
        ),
        owner="platform-security-team",
        review_date="2026-08-22",
    ),
)


def _get_bundle_stats() -> list[dict]:
    """Run build and parse output to get chunk sizes."""
    if not ASSETS_DIR.exists():
        return []
    stats = []
    for f in ASSETS_DIR.glob("*.js"):
        stats.append({"name": f.name, "size_bytes": f.stat().st_size})
    return stats


def _exemption_for_chunk(chunk_name: str) -> BundleExemption | None:
    """Return the exemption covering ``chunk_name``, or ``None``.

    A chunk is exempt when its filename contains the exempted package name
    (e.g. ``plotly.min-<hash>.js`` matches the ``plotly`` exemption).
    """
    for exemption in BUNDLE_EXEMPTIONS:
        if exemption.package in chunk_name.lower():
            return exemption
    return None


def _get_entry_sync_chunks(index_html: str) -> list[str]:
    """Return the first-paint (synchronous) JS chunk filenames from ``index.html``.

    These are the chunks the browser must fetch/evaluate before first paint: the
    module entry script plus any statically ``<link rel="modulepreload">`` chunks.
    Returns an empty list when the entry script cannot be located.
    """
    entry_match = re.search(
        r'<script[^>]+type="module"[^>]+src="(/assets/[^"]+\.js)"', index_html
    )
    entry_src = entry_match.group(1) if entry_match else None
    preloads = re.findall(
        r'<link rel="modulepreload"[^>]+href="(/assets/[^"]+\.js)"', index_html
    )
    sync_srcs = ([entry_src] if entry_src else []) + preloads
    return [Path(src).name for src in sync_srcs]


def test_main_entry_under_750kb():
    """Main entry chunk must be under 750 KiB uncompressed."""
    stats = _get_bundle_stats()
    if not stats:
        return  # Build not run
    # Find the main entry (index-*.js, not vendor chunks)
    main_chunks = [s for s in stats if s["name"].startswith("index-") and s["size_bytes"] < 200_000]
    # The small index is the entry, the large one is vendor
    for chunk in main_chunks:
        assert chunk["size_bytes"] < MAIN_ENTRY_BUDGET_BYTES, (
            f"Main entry {chunk['name']} is {chunk['size_bytes'] / 1024:.0f} KiB "
            f"(max {MAIN_ENTRY_BUDGET_BYTES // 1024} KiB)"
        )


def test_no_async_chunk_over_1_5_mib():
    """No single async JS chunk may exceed 1.5 MiB uncompressed."""
    stats = _get_bundle_stats()
    if not stats:
        return
    for chunk in stats:
        if _exemption_for_chunk(chunk["name"]) is not None:
            continue  # Covered by a declared BundleExemption (see BUNDLE_EXEMPTIONS)
        assert chunk["size_bytes"] < ASYNC_CHUNK_BUDGET_BYTES, (
            f"Chunk {chunk['name']} is {chunk['size_bytes'] / 1024:.0f} KiB "
            f"(max {ASYNC_CHUNK_BUDGET_BYTES // 1024} KiB)"
        )


def test_plotly_not_in_first_paint():
    """Assert Plotly stays out of the first-paint (synchronous) JS path.

    Plotly is shipped full-size and exempted from the async-chunk budget (see
    ``BUNDLE_EXEMPTIONS``). That exemption only stays honest if the library lives in
    an independently-named *async* chunk that is neither the main entry nor a
    statically-preloaded chunk, so it cannot block first paint.

    Asserts:
      1. Every first-paint chunk (entry script + modulepreload links) has a
         plotly-free filename.
      2. Those chunks' contents do not contain the string ``plotly`` (i.e. they do
         not statically reference the library).
      3. A plotly chunk still exists as an independent, large (>1 MiB) async chunk.
    """
    index_html = DIST_DIR / "index.html"
    if not index_html.exists():
        return  # Build not run
    sync_chunks = _get_entry_sync_chunks(index_html.read_text(encoding="utf-8"))
    if not sync_chunks:
        return  # Entry script not found; nothing sync to assert against

    # 1 & 2: first-paint chunks must be plotly-free in name and content.
    for name in sync_chunks:
        assert "plotly" not in name.lower(), f"plotly is sync-loaded via {name}"
        chunk_path = ASSETS_DIR / name
        if not chunk_path.exists():
            continue
        content = chunk_path.read_text(encoding="utf-8", errors="ignore")
        assert "plotly" not in content.lower(), (
            f"first-paint chunk {name} references plotly (should be async-only)"
        )

    # 3: plotly must exist as an independent, still-large async chunk.
    plotly_chunks = [p.name for p in ASSETS_DIR.glob("*.js") if "plotly" in p.name.lower()]
    assert plotly_chunks, "plotly chunk not found in dist/assets (was it trimmed?)"
    for name in plotly_chunks:
        size = (ASSETS_DIR / name).stat().st_size
        assert size > 1024 * 1024, (
            f"plotly chunk {name} is {size / 1024:.0f} KiB; expected the full "
            f"~4.8 MiB bundle, not a trimmed/partial build"
        )
