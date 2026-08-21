"""Router purity: no direct database construction or SQL execution.

The research timeline router must not contain build_session_factory,
IRIP_DATABASE_URL, session.execute, sa.text, or sa.select calls.
All database operations must go through Service methods.
"""

from pathlib import Path


def test_timeline_router_contains_no_database_construction():
    source = Path("apps/api/routers/research_timeline.py").read_text()
    assert "build_session_factory" not in source
    assert "IRIP_DATABASE_URL" not in source
    assert "session.execute" not in source
    assert "sa.text(" not in source
    assert "sa.select(" not in source


def test_timeline_router_has_no_import_os_for_db():
    source = Path("apps/api/routers/research_timeline.py").read_text()
    # Router should not import os for database URL reads
    lines = source.split("\n")
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith("import os")
            and "IRIP_DATABASE" in lines[min(lines.index(line) + 1, len(lines) - 1)]
        ):
            raise AssertionError("Router imports os for database URL reads")
