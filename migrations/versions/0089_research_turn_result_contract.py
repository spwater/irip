"""0089: Enforce non-null run_id on research_turn_result.

P0 Timeline Task 1: the ``research_turn_result.run_id`` column must be
NOT NULL.  A TurnResult without a Run is an invalid state -- one Run
produces exactly one Result.  The column already carries a UNIQUE
constraint and a CASCADE foreign key to ``research_analysis_run.id``;
this migration only tightens nullability.

Upgrade safety: before issuing ALTER COLUMN ... NOT NULL, the migration
checks for existing NULL rows and aborts if any are found.  This
prevents silently corrupting data.

Downgrade: intentionally a no-op.  Re-introducing nullable=True would
allow the invalid state this migration was created to prevent.

Revision ID: 0089
Revises: 0088
Create Date: 2026-08-21
"""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Set research_turn_result.run_id to NOT NULL after a safety check."""

    # Safety check: abort if any row has a NULL run_id.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM research_turn_result WHERE run_id IS NULL) THEN
            RAISE EXCEPTION 'research_turn_result contains rows with NULL run_id; '
                'clean up before running this migration';
          END IF;
        END
        $$
        """
    )

    op.alter_column("research_turn_result", "run_id", nullable=False)


def downgrade() -> None:
    """Do NOT restore nullable=True -- that would reintroduce invalid state."""
    pass
