"""0081: REVOKE UPDATE/DELETE on audit_event from irip_app

Revision ID: 0081
Revises: 0080
Create Date: 2026-08-07
"""

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Revoke UPDATE and DELETE on audit_event from irip_app.

    audit_event is an immutable table (has trigger preventing UPDATE/DELETE).
    irip_app should only have SELECT and INSERT, not UPDATE/DELETE.
    """
    op.execute("REVOKE UPDATE, DELETE ON TABLE audit_event FROM irip_app")


def downgrade() -> None:
    """Restore UPDATE, DELETE on audit_event to irip_app."""
    op.execute("GRANT UPDATE, DELETE ON TABLE audit_event TO irip_app")
