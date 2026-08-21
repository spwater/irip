# Disaster Recovery Runbook

## Overview

This runbook describes how to run an automated recovery drill and how to
perform a production restore. Every restore must produce verifiable JSON
evidence (manifest hash, source/target IDs, row/object counts, RPO, RTO)
before it is considered successful.

## Recovery Drill

The recovery drill is a fully automated, isolated exercise: it creates known
fixtures, takes an encrypted backup, restores to an isolated environment, and
verifies the result.

1. Run the drill:

   ```bash
   bash scripts/ops/run-recovery-drill.sh --environment drill
   ```

2. Check the emitted JSON evidence (default path
   `/tmp/recovery-evidence.json`). Every boolean check must be `true`:

   ```json
   {
     "database_checksum_match": true,
     "object_checksum_match": true,
     "audit_chain_valid": true,
     "rpo_seconds": 0,
     "rto_seconds": 0
   }
   ```

3. Record RPO/RTO from the evidence file for the drill log.

## Production Recovery

Production restores require an approval token and an explicit approver
identity. Never run a production restore without both.

1. Get an approval token from the preflight check:

   ```bash
   python scripts/ops/restore_preflight.py \
       --environment production \
       --backup-dir <backup_dir> \
       --confirm <token>
   ```

2. Set the approver identity:

   ```bash
   export IRIP_RESTORE_APPROVED_BY=<approver>
   ```

3. Run the host-orchestrated restore:

   ```bash
   bash scripts/ops/restore.sh \
       --environment production \
       --manifest <path> \
       --confirm <token>
   ```

4. Verify the restored data independently:

   ```bash
   python scripts/ops/verify_recovery.py <backup_dir> <restore_dir>
   ```

   The command prints the JSON evidence and exits `0` only when every boolean
   check passes.

## Path Safety

`verify_recovery.py` rejects unsafe target paths before any work runs. The
following are always rejected:

- the filesystem root `/`
- the current user's home directory (`$HOME` / `~`)
- the IRIP workspace root
- any path containing an unresolved shell variable (e.g. `$VAR`)
