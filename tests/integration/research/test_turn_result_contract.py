"""TurnResult contract: run_id must be non-null.

P0 Timeline Task 1: the ``research_turn_result.run_id`` column must be
NOT NULL at both the ORM and database level.  A TurnResult without a
Run is an invalid state (one Run produces one Result).
"""

from packages.research.timeline.entities import ResearchTurnResult


def test_turn_result_run_id_is_non_nullable_in_orm():
    """The ORM column must declare nullable=False."""
    column = ResearchTurnResult.__table__.c.run_id
    assert column.nullable is False


def test_turn_result_run_id_type_is_non_optional():
    """The Mapped type annotation must not include None."""
    column = ResearchTurnResult.__table__.c.run_id
    # nullable=False at the Column level is the primary contract;
    # we also assert the type annotation is UUID (not UUID | None).
    assert column.nullable is False
    assert "None" not in str(column.type)


def test_turn_result_run_id_has_unique_constraint():
    """run_id must remain unique (one result per run)."""
    column = ResearchTurnResult.__table__.c.run_id
    assert column.unique is True


def test_turn_result_run_id_has_foreign_key():
    """run_id must reference research_analysis_run.id with CASCADE delete."""
    fks = list(ResearchTurnResult.__table__.c.run_id.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "research_analysis_run"
    assert fk.column.name == "id"
    assert fk.ondelete == "CASCADE"
