# Production Deployment Runbook

> **Scope**: Standard production deployment for IRIP using Docker Compose
> (base + production overlay). Covers pre-deployment checks, deployment
> steps, recovery drill, and rollback procedures.
>
> **Prerequisites**: P0 and P1 readiness items are complete; secrets are
> provisioned in `./secrets/`; TLS certificates are obtained and deployed.
>
> **Execution role**: SRE / DevOps operator. Production restores require a
> named approver (`IRIP_RESTORE_APPROVED_BY`).

---

## 1. Pre-Deployment Checklist

Complete every item below before starting the deployment. Each item has a
verification command or acceptance criterion.

### 1.1 Compose Render Validation

Confirm the merged base + production compose configuration is syntactically
valid and resolves all required environment variables:

```bash
cd /path/to/irip
docker compose -f compose.base.yaml -f compose.production.yaml config >/dev/null 2>&1 \
  && echo "CONFIG OK" || echo "CONFIG FAIL"
```

- **Pass condition**: output is `CONFIG OK`.
- If `CONFIG FAIL`, inspect the full output without redirecting stderr to
  identify the missing variable or syntax error.

### 1.2 Container Image Scan

Scan all runtime images for known vulnerabilities before pushing to
production. The API and web images are built from
`deployments/compose/api.Dockerfile` and
`deployments/compose/web.Dockerfile` respectively.

```bash
# Build images (if not already built)
docker compose -f compose.base.yaml -f compose.production.yaml build api web worker scheduler bootstrap

# Scan with Trivy (or Grype)
trivy image irip-api:latest --severity HIGH,CRITICAL --exit-code 1
trivy image irip-web:latest --severity HIGH,CRITICAL --exit-code 1
```

- **Pass condition**: zero `CRITICAL` vulnerabilities; `HIGH` findings
  reviewed and either fixed or documented with a risk acceptance.
- The runtime stage must not contain ops tools (`postgresql-client`,
  `minio-client`, `docker-ce`). This is enforced by the acceptance test
  `test_api_dockerfile_does_not_install_pg_tools`.

### 1.3 TLS Certificate Validation

Production traffic is terminated at the `web` (nginx) container on port
443. Certificates are mounted via Docker secrets from `./secrets/`.

1. Confirm certificate files exist and are readable:

   ```bash
   ls -l ./secrets/tls_fullchain ./secrets/tls_private_key
   ```

   Both files must be present and owned by the deployment user.

2. Validate the certificate chain and expiry:

   ```bash
   openssl x509 -in ./secrets/tls_fullchain -noout -dates -subject -issuer
   ```

   - **Pass condition**: `notAfter` is at least 30 days in the future.
   - If the certificate expires within 30 days, renew before proceeding.

3. Verify the private key matches the certificate:

   ```bash
   openssl x509 -in ./secrets/tls_fullchain -noout -modulus | openssl md5
   openssl rsa -in ./secrets/tls_private_key -noout -modulus | openssl md5
   ```

   - **Pass condition**: both MD5 hashes are identical.

4. Confirm the production nginx config references the secret paths
   (`/run/secrets/tls_fullchain`, `/run/secrets/tls_private_key`) and
   listens on 443 with SSL:

   ```bash
   grep -E "ssl_certificate|listen 443" deployments/compose/nginx.conf
   ```

### 1.4 Secrets File Permissions

All secret files under `./secrets/` must have restrictive permissions
(`0400`) and must not be tracked in version control.

```bash
# Verify permissions (each file must show -r-------- or -r--r-----)
ls -l ./secrets/

# Verify no secrets are tracked in git
git ls-files ./secrets/
```

- **Pass condition**: all secret files are `0400` (or `0440` if a group
  is used); `git ls-files` returns empty (no secret content in the repo).

### 1.5 Required Secrets Inventory

The production compose overlay references the following Docker secrets.
Each must exist in `./secrets/` with a strong, non-default value:

| Secret file | Used by | Requirement |
|---|---|---|
| `jwt_secret` | api, worker, scheduler, bootstrap | >= 32 bytes, high entropy |
| `master_key` | api, worker, scheduler, bootstrap | >= 32 bytes, high entropy |
| `database_password` | postgres, worker, bootstrap, restore, backup | strong random password |
| `database_app_password` | api, worker, scheduler | strong random password (differs from `database_password`) |
| `redis_password` | redis, api, worker, scheduler | strong random password |
| `minio_secret_key` | minio, api, worker, scheduler, bootstrap | strong random secret |
| `bootstrap_admin_password` | api, bootstrap | strong random password |
| `tls_fullchain` | web | valid X.509 certificate chain |
| `tls_private_key` | web | matching RSA/ECDSA private key |

Verify each file is non-empty:

```bash
for f in ./secrets/*; do
  [ -s "$f" ] && echo "OK: $f" || echo "EMPTY: $f"
done
```

### 1.6 Security Acceptance Tests

Run the security acceptance test suite to verify the compose
configuration meets all hardening requirements:

```bash
cd /path/to/irip
IRIP_ENV=test uv run pytest \
  tests/acceptance/test_compose_security.py \
  tests/acceptance/test_container_images.py \
  tests/acceptance/test_nginx_tls_config.py \
  tests/acceptance/test_restore_has_no_docker_socket.py \
  -q
```

- **Pass condition**: all tests pass (exit code 0).
- These tests verify: only `web` exposes ports, runtime images are free
  of ops tools, TLS is configured, and the restore service does not
  mount the Docker socket.

---

## 2. Deployment Steps

### 2.1 Start Infrastructure and Application Services

```bash
cd /path/to/irip

# Render and validate the merged configuration (final check)
docker compose -f compose.base.yaml -f compose.production.yaml config >/dev/null

# Start all services (infra first via depends_on health checks, then app)
docker compose -f compose.base.yaml -f compose.production.yaml up -d
```

This starts the following services in dependency order:

1. **Infrastructure**: `postgres`, `redis`, `minio` (health-checked)
2. **Application**: `api`, `worker`, `scheduler` (wait for infra healthy)
3. **Edge**: `web` (nginx with TLS, waits for `api` healthy)

The `backup` and `restore` services are gated behind the
`dangerous-ops` profile and do not start automatically.

### 2.2 Run Bootstrap (First Deployment Only)

On a fresh database, run the bootstrap one-shot container to apply
database migrations and create the initial organization, roles, admin
user, and MinIO bucket:

```bash
docker compose -f compose.base.yaml -f compose.production.yaml run --rm bootstrap
```

- The bootstrap container runs `alembic upgrade head` followed by
  `python -m deployments.compose.bootstrap`.
- On subsequent deployments, migrations are typically run as a separate
  step (see below); the bootstrap container can be skipped if the
  schema and admin user already exist.

**Run migrations only (subsequent deployments)**:

```bash
docker compose -f compose.base.yaml -f compose.production.yaml run --rm \
  api alembic upgrade head
```

### 2.3 Verify Health Checks

After all services are up, verify each health check endpoint:

1. **PostgreSQL** (infra):

   ```bash
   docker compose -f compose.base.yaml -f compose.production.yaml ps postgres
   # Status should show "healthy"
   ```

2. **Redis** (infra):

   ```bash
   docker compose -f compose.base.yaml -f compose.production.yaml ps redis
   # Status should show "healthy"
   ```

3. **MinIO** (infra):

   ```bash
   docker compose -f compose.base.yaml -f compose.production.yaml ps minio
   # Status should show "healthy"
   ```

4. **API** (liveness):

   ```bash
   curl -f http://localhost/api/v1/health/live
   # Expected: 200 OK
   ```

5. **API** (readiness):

   ```bash
   curl -f http://localhost/api/v1/health/ready
   # Expected: 200 OK with all subsystem checks passing
   ```

6. **Worker**:

   ```bash
   docker compose -f compose.base.yaml -f compose.production.yaml ps worker
   # Status should show "healthy"
   ```

7. **Web** (TLS):

   ```bash
   curl -fk https://localhost/
   # Expected: 200 OK (SPA index.html)
   ```

8. **HTTP to HTTPS redirect**:

   ```bash
   curl -sI http://localhost/ | head -1
   # Expected: HTTP/1.1 301 Moved Permanently
   ```

### 2.4 Post-Deployment Smoke Test

Run a quick functional smoke test to confirm the platform is usable:

```bash
# Login with bootstrap admin (password from secrets/bootstrap_admin_password)
# Verify token issuance and refresh work
# Verify file upload/download via MinIO
# Verify a simple AI assistant conversation
```

Record the deployment timestamp, image digests, and smoke test results
in the deployment log.

---

## 3. Recovery Drill

The recovery drill is a fully automated, isolated exercise that
validates the backup and restore pipeline end-to-end. It does **not**
touch the production data or the project tree.

### 3.1 Run the Drill

```bash
cd /path/to/irip
bash scripts/ops/run-recovery-drill.sh --environment drill
```

The script creates known fixtures in an isolated temp directory, takes
an encrypted backup, restores to a separate directory, and verifies the
result.

### 3.2 Verify Evidence

The drill emits a JSON evidence file (default path:
`/tmp/recovery-evidence.json`). Every boolean check must be `true`:

```bash
cat /tmp/recovery-evidence.json
```

Expected output:

```json
{
  "database_checksum_match": true,
  "object_checksum_match": true,
  "audit_chain_valid": true,
  "rpo_seconds": 0,
  "rto_seconds": 0
}
```

- **Pass condition**: all booleans are `true` and the script exits 0.
- Record RPO/RTO values from the evidence file in the drill log.
- If any check fails, do not proceed to production deployment until the
  backup/restore pipeline is fixed and the drill passes.

### 3.3 Production Recovery (if needed)

Production restores require an approval token and an explicit approver
identity. Never run a production restore without both. See the
[Disaster Recovery Runbook](disaster-recovery.md) for the full
procedure.

```bash
# 1. Get an approval token from the preflight check
python scripts/ops/restore_preflight.py \
    --environment production \
    --backup-dir <backup_dir> \
    --confirm <token>

# 2. Set the approver identity
export IRIP_RESTORE_APPROVED_BY=<approver>

# 3. Run the host-orchestrated restore
bash scripts/ops/restore.sh \
    --environment production \
    --manifest <path> \
    --confirm <token>

# 4. Verify the restored data independently
python scripts/ops/verify_recovery.py <backup_dir> <restore_dir>
```

The restore container never mounts the Docker socket. All service
stop/start is performed by the host script (`scripts/ops/restore.sh`).

---

## 4. Rollback Steps

### 4.1 Application Rollback (Image-Level)

If the new deployment is unstable, roll back to the previous image
version:

```bash
cd /path/to/irip

# 1. Stop the application services (keep infra running)
docker compose -f compose.base.yaml -f compose.production.yaml stop api worker scheduler web

# 2. Rebuild or pull the previous image version
#    (replace <previous_tag> with the known-good image tag)
docker compose -f compose.base.yaml -f compose.production.yaml build api web worker scheduler

# 3. Restart with the previous images
docker compose -f compose.base.yaml -f compose.production.yaml up -d api worker scheduler web
```

### 4.2 Database Migration Rollback

If a migration introduced a breaking schema change, roll back the
Alembic migration:

```bash
# Check current migration head
docker compose -f compose.base.yaml -f compose.production.yaml run --rm api alembic current

# Roll back one revision
docker compose -f compose.base.yaml -f compose.production.yaml run --rm api alembic downgrade -1

# Roll back to a specific revision
docker compose -f compose.base.yaml -f compose.production.yaml run --rm api alembic downgrade <revision>
```

> **Warning**: Rolling back migrations may cause data loss if the
> migration added columns or tables with data. Always take a database
> backup before rolling back. If RLS policies were added, rolling back
> the migration will disable RLS protection; assess the security impact
> before proceeding.

### 4.3 Database Restore from Backup

If the database is corrupted or data was lost, restore from the most
recent backup using the production recovery procedure (see Section 3.3
and the [Disaster Recovery Runbook](disaster-recovery.md)):

```bash
export IRIP_RESTORE_APPROVED_BY=<approver>
bash scripts/ops/restore.sh \
    --environment production \
    --manifest <path_to_backup_manifest> \
    --confirm <token>
```

### 4.4 Full Rollback (Code + Database)

For a complete rollback to a known-good state:

1. Stop all application services (keep infra running).
2. Restore the database from the pre-deployment backup (Section 4.3).
3. Rebuild and start the previous application image version
   (Section 4.1).
4. Run health checks (Section 2.3) to confirm the platform is
   operational.
5. Record the rollback in the incident log with root cause and
   timeline.

---

## Appendix A: Service Overview

| Service | Purpose | Network | Host Ports |
|---|---|---|---|
| `postgres` | Primary database (pgvector/pg16) with PITR | backend (internal) | none |
| `redis` | Celery broker + result backend | backend (internal) | none |
| `minio` | Object storage for artifacts | backend (internal) | none |
| `api` | FastAPI application server | frontend + backend | none |
| `worker` | Celery worker (async tasks) | backend (internal) | none |
| `scheduler` | Celery beat (scheduled tasks) | backend (internal) | none |
| `web` | Nginx (TLS termination + SPA + reverse proxy) | frontend | 80, 443 |
| `bootstrap` | One-shot: migrations + initial data | backend (internal) | none |
| `backup` | One-shot: encrypted backup (profile: dangerous-ops) | backend (internal) | none |
| `restore` | One-shot: restore from backup (profile: dangerous-ops) | backend (internal) | none |

## Appendix B: Quick Reference Commands

```bash
# Validate compose config
docker compose -f compose.base.yaml -f compose.production.yaml config >/dev/null

# Start all services
docker compose -f compose.base.yaml -f compose.production.yaml up -d

# Check service status
docker compose -f compose.base.yaml -f compose.production.yaml ps

# View logs (follow)
docker compose -f compose.base.yaml -f compose.production.yaml logs -f api

# Stop all services (keep volumes)
docker compose -f compose.base.yaml -f compose.production.yaml down

# Stop and remove volumes (DESTRUCTIVE)
docker compose -f compose.base.yaml -f compose.production.yaml down -v

# Run security acceptance tests
IRIP_ENV=test uv run pytest \
  tests/acceptance/test_compose_security.py \
  tests/acceptance/test_container_images.py \
  tests/acceptance/test_nginx_tls_config.py \
  tests/acceptance/test_restore_has_no_docker_socket.py \
  -q

# Run recovery drill
bash scripts/ops/run-recovery-drill.sh --environment drill
```
