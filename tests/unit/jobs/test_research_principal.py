"""Research task principal allowlist and validation tests."""

from uuid import uuid4

import pytest

from packages.jobs.research_principal import ResearchTaskPrincipal


def test_from_payload_valid():
    actor, dept, ws = uuid4(), uuid4(), uuid4()
    p = ResearchTaskPrincipal.from_payload(
        {"actor_id": str(actor), "department_id": str(dept), "workspace_id": str(ws)}
    )
    assert p.actor_id == actor
    assert p.department_id == dept
    assert p.workspace_id == ws


def test_from_payload_rejects_missing_field():
    with pytest.raises(ValueError):
        ResearchTaskPrincipal.from_payload(
            {"department_id": str(uuid4()), "workspace_id": str(uuid4())}
        )


def test_from_payload_rejects_invalid_uuid():
    with pytest.raises(ValueError):
        ResearchTaskPrincipal.from_payload(
            {
                "actor_id": "not-a-uuid",
                "department_id": str(uuid4()),
                "workspace_id": str(uuid4()),
            }
        )


def test_as_kwargs_returns_strings():
    actor, dept, ws = uuid4(), uuid4(), uuid4()
    p = ResearchTaskPrincipal(actor_id=actor, department_id=dept, workspace_id=ws)
    kwargs = p.as_kwargs()
    assert set(kwargs) == {"actor_id", "department_id", "workspace_id"}
    assert kwargs["actor_id"] == str(actor)
    assert kwargs["department_id"] == str(dept)
    assert kwargs["workspace_id"] == str(ws)


def test_payload_with_extra_fields_still_parses():
    """Extra fields in payload are ignored — only allowlisted IDs extracted."""
    actor, dept, ws = uuid4(), uuid4(), uuid4()
    p = ResearchTaskPrincipal.from_payload(
        {
            "actor_id": str(actor),
            "department_id": str(dept),
            "workspace_id": str(ws),
            "prompt": "must-not-enter-celery",
            "analysis_text": "secret",
        }
    )
    assert p.actor_id == actor
    # Principal does not carry any content
    assert not hasattr(p, "prompt")
    assert not hasattr(p, "analysis_text")
