"""0082: Encrypt existing plaintext secret values with AES-256-GCM

Revision ID: 0082
Revises: 0081
Create Date: 2026-08-07

Encrypts all existing plaintext values in the `secret` table using
EnvelopeCrypto (AES-256-GCM). Values already encrypted (starting with
'v0:' prefix) are skipped.

Prerequisite: IRIP_MASTER_KEY environment variable must be set.
"""

import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Encrypt existing plaintext secret values."""
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw_key = os.getenv("IRIP_MASTER_KEY", "")
    is_test_env = os.getenv("IRIP_ENV") == "test"

    if not raw_key and not is_test_env:
        raise RuntimeError(
            "IRIP_MASTER_KEY is required to encrypt existing secrets. "
            "Set IRIP_ENV=test for test environments or provide IRIP_MASTER_KEY."
        )

    if not raw_key:
        raw_key = base64.b64encode(b"0" * 32).decode("ascii")

    key = base64.b64decode(raw_key)
    if len(key) != 32:
        raise ValueError(f"Master key must be 32 bytes, got {len(key)} bytes")

    conn = op.get_bind()

    # Select all secrets
    rows = conn.execute(
        sa.text("SELECT id, value FROM secret"),
    ).fetchall()

    aesgcm = AESGCM(key)
    updated = 0
    for row in rows:
        secret_id = row[0]
        value = row[1]

        # Skip already-encrypted values (format: v{version}:{nonce}:{ciphertext})
        if value.startswith("v0:"):
            continue

        # Encrypt plaintext value
        nonce = os.urandom(12)
        ct_with_tag = aesgcm.encrypt(nonce, value.encode("utf-8"), None)

        nonce_b64 = base64.b64encode(nonce).decode("ascii")
        ct_b64 = base64.b64encode(ct_with_tag).decode("ascii")
        encrypted = f"v0:{nonce_b64}:{ct_b64}"

        conn.execute(
            sa.text("UPDATE secret SET value = :enc WHERE id = :sid"),
            {"enc": encrypted, "sid": str(secret_id)},
        )
        updated += 1

    print(
        f"0082: Encrypted {updated} secret values (skipped {len(rows) - updated} already encrypted)"
    )


def downgrade() -> None:
    """Cannot reverse encryption safely (would require re-encrypting to plaintext)."""
    print("0082: downgrade skipped - cannot reverse encryption")
    pass
