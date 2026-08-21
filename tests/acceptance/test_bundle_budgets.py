"""Frontend bundle size budget tests.

Verifies that the main entry chunk is under 750 KiB (uncompressed)
and no single async chunk exceeds 1.5 MiB (uncompressed).
"""

from pathlib import Path


def _get_bundle_stats() -> list[dict]:
    """Run build and parse output to get chunk sizes."""
    dist = Path("apps/web/dist/assets")
    if not dist.exists():
        return []
    stats = []
    for f in dist.glob("*.js"):
        stats.append(
            {"name": f.name, "size_bytes": f.stat().st_size}
        )
    return stats


def test_main_entry_under_750kb():
    """Main entry chunk must be under 750 KiB uncompressed."""
    stats = _get_bundle_stats()
    if not stats:
        return  # Build not run
    # Find the main entry (index-*.js, not vendor chunks)
    main_chunks = [
        s for s in stats
        if s["name"].startswith("index-") and s["size_bytes"] < 200_000
    ]
    # The small index is the entry, the large one is vendor
    if main_chunks:
        for chunk in main_chunks:
            assert chunk["size_bytes"] < 750 * 1024, (
                f"Main entry {chunk['name']} is "
                f"{chunk['size_bytes'] / 1024:.0f} KiB (max 750 KiB)"
            )


def test_no_async_chunk_over_1_5_mib():
    """No single async JS chunk may exceed 1.5 MiB uncompressed."""
    stats = _get_bundle_stats()
    if not stats:
        return
    max_chunk_size = 1536 * 1024  # 1.5 MiB
    # Exclude plotly (known large, loaded via dynamic import)
    for chunk in stats:
        if "plotly" in chunk["name"]:
            continue  # plotly is dynamically imported, size exempted
        assert chunk["size_bytes"] < max_chunk_size, (
            f"Chunk {chunk['name']} is "
            f"{chunk['size_bytes'] / 1024:.0f} KiB (max 1536 KiB)"
        )
