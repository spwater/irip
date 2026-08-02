"""allow controlled DELETE on immutable tables via GUC

Revision ID: ec4b55a466ce
Revises: 780b980397b7
Create Date: 2026-08-02 18:17:29.125607
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = 'ec4b55a466ce'
down_revision: str | None = '780b980397b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION raise_immutable_violation()
        RETURNS trigger AS $$
        BEGIN
            -- Allow controlled DELETE via GUC (e.g., component deletion cleanup)
            IF TG_OP = 'DELETE' AND COALESCE(current_setting('app.allow_immutable_delete', true), 'off') = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'Table % is immutable: UPDATE/DELETE not allowed (F-03)',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION raise_immutable_violation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Table % is immutable: UPDATE/DELETE not allowed (F-03)',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """)
