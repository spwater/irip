#!/usr/bin/env bash
set -euo pipefail

# Bootstrap irip_app role with NOSUPERUSER NOBYPASSRLS for CI testing
# Usage: bootstrap_limited_database_role.sh <alembic_url> <app_password>

ALEMBIC_URL="${1:?Usage: bootstrap_limited_database_role.sh <alembic_url> <app_password>}"
APP_PASSWORD="${2:?Missing app password}"

# Run migrations as migration user
# Convert plain postgresql:// to postgresql+psycopg:// for async alembic driver
# (env.py expects postgresql+psycopg:// which it converts to postgresql+psycopg_async://)
if [[ "$ALEMBIC_URL" == postgresql+psycopg://* ]]; then
  ALEMBIC_PY_URL="$ALEMBIC_URL"
else
  ALEMBIC_PY_URL="postgresql+psycopg://${ALEMBIC_URL#postgresql://}"
fi
IRIP_ALEMBIC_DATABASE_URL="$ALEMBIC_PY_URL" python -m alembic upgrade head

# Set irip_app password for this test run
psql "$ALEMBIC_URL" -c "ALTER ROLE irip_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '$APP_PASSWORD'"

# Verify role attributes
psql "$ALEMBIC_URL" -t -c "SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'irip_app'"
