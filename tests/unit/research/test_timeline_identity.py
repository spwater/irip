"""Timeline service identity-aware constructor tests.

Verifies that TimelineQueryService, RecommendationService, and AnalysisService
require department_id and actor_id in their constructors.
"""

from uuid import uuid4

import pytest

from packages.research.timeline.analysis_service import AnalysisService
from packages.research.timeline.recommendation_service import RecommendationService
from packages.research.timeline.timeline_query_service import TimelineQueryService


def test_timeline_query_service_requires_identity():
    """TimelineQueryService without department_id raises TypeError."""
    with pytest.raises(TypeError):
        TimelineQueryService(session_factory=None)  # type: ignore[arg-type]


def test_timeline_query_service_accepts_identity():
    """TimelineQueryService with department_id and actor_id constructs."""
    dept_id = uuid4()
    actor_id = uuid4()
    svc = TimelineQueryService(
        session_factory=None,  # type: ignore[arg-type]
        department_id=dept_id,
        actor_id=actor_id,
    )
    assert svc._dept_id == dept_id
    assert svc._actor_id == actor_id


def test_recommendation_service_requires_identity():
    """RecommendationService without department_id raises TypeError."""
    with pytest.raises(TypeError):
        RecommendationService(session_factory=None)  # type: ignore[arg-type]


def test_recommendation_service_accepts_identity():
    """RecommendationService with department_id and actor_id constructs."""
    dept_id = uuid4()
    actor_id = uuid4()
    svc = RecommendationService(
        session_factory=None,  # type: ignore[arg-type]
        department_id=dept_id,
        actor_id=actor_id,
    )
    assert svc._dept_id == dept_id
    assert svc._actor_id == actor_id


def test_analysis_service_requires_identity():
    """AnalysisService without department_id raises TypeError."""
    with pytest.raises(TypeError):
        AnalysisService(session_factory=None)  # type: ignore[arg-type]


def test_analysis_service_accepts_identity():
    """AnalysisService with department_id and actor_id constructs."""
    dept_id = uuid4()
    actor_id = uuid4()
    svc = AnalysisService(
        session_factory=None,  # type: ignore[arg-type]
        department_id=dept_id,
        actor_id=actor_id,
    )
    assert svc._dept_id == dept_id
    assert svc._actor_id == actor_id


def test_analysis_service_has_no_database_url_reads():
    """AnalysisService source must not read IRIP_DATABASE_URL or admin@irip.local."""
    import ast
    from pathlib import Path

    source = Path("packages/research/timeline/analysis_service.py").read_text()
    tree = ast.parse(source)
    # Walk all string constants and check none contain the forbidden patterns
    forbidden = ["IRIP_DATABASE_URL", "IRIP_ALEMBIC_DATABASE_URL", "admin@irip.local"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Skip docstrings (first statement in module/class/function)
            for pattern in forbidden:
                if pattern in node.value:
                    # Check if it's a docstring (lineno matches a def/class)
                    is_docstring = False
                    for parent in ast.walk(tree):
                        if isinstance(parent, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                            if (
                                parent.body
                                and isinstance(parent.body[0], ast.Expr)
                                and isinstance(parent.body[0].value, ast.Constant)
                                and parent.body[0].value.lineno == node.lineno
                            ):
                                is_docstring = True
                                break
                    if not is_docstring:
                        pytest.fail(f"Forbidden pattern '{pattern}' found in code")
    # Also check no build_session_factory import
    assert "build_session_factory" not in source.replace('"""Analysis service', "", 1)
