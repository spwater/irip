# Deterministic Particle-Size Fixture Generator

## Overview

This generator produces **deterministic, reproducible** particle-size
experiment data for the IRIP platform.  All randomness is driven by a
single fixed seed, so every run with the same seed produces byte-for-byte
identical output — including Excel files, PDF reports, and SHA-256 digests.

## Quick Start

```bash
# Generate fixtures to a directory
python examples/particle-size/generate.py --output .artifacts/particle-size --seed 20260715
```

Output summary:
```
Generated 60 experiments, 62 source files, 2 duplicate copies, 3 blocked, 2 warnings.
Manifest digest: sha256:d0f998bc2...
```

## What It Produces

| Item | Count |
|------|-------|
| Unique experiments | 60 |
| Excel files (.xlsx) | 62 (60 originals + 2 duplicates) |
| PDF reports (.pdf) | 60 |
| Self-check failures | 3 |
| Moisture warnings | 2 |
| Total files in manifest | 122 |

### Experiment Dimensions

- **4 batches**: B01, B02, B03, B04
- **2 instruments**: LaserSizer-3000 (laser diffraction), SieveSet-A (sieve analysis)
- **2 method versions**: ISO-13320-1:2009 (laser), ISO-2591-1:1988 (sieve)
- **3 source naming styles**: D50_um, MedianDiameter_mm, 中位径
- **Source units**: um and mm (alternating)
- **32-point distribution curves**: cumulative % passing at logarithmically spaced size bins
- **Percentiles**: D10 < D50 < D90 for normal experiments

### Anomalies

- **3 self-check failures** (EXP-011, EXP-026, EXP-041): D10 >= D50 or D50 >= D90
- **2 moisture warnings** (EXP-006, EXP-036): moisture content > 3.0%
- **2 duplicate files** (EXP-001-DUP1, EXP-031-DUP1): byte-for-byte copies of EXP-001 and EXP-031

## File Format

### Excel Files (.xlsx)

Each experiment has a two-sheet workbook:

- **Summary** sheet: experiment ID, batch, instrument, method, operator,
  sample ID, timestamp, D10, D50, D90, specific surface, moisture,
  self-check flag, moisture-warning flag.
- **Distribution** sheet: 32 rows of (size bin, cumulative % passing).

### PDF Reports (.pdf)

Minimal valid PDF 1.4 documents with a single page containing the
experiment ID, key metrics (D10/D50/D90, specific surface, moisture),
and self-check status.

### ground_truth.json

Source-of-truth JSON with:

- Top-level metadata: seed, experiment_count, duplicate_count,
  self_check_failures, moisture_warnings.
- Per-experiment entries (62 total = 60 originals + 2 duplicates) with
  all data fields, anomaly flags, and the SHA-256 digest of the .xlsx file.

### expected_manifest.json

File manifest with:

- `total_files`: total number of source files (122).
- `files`: list of `{path, sha256, size, kind}` for every file.
- `manifest_digest`: SHA-256 of the manifest JSON (excluding the digest
  field itself), computed with `sort_keys=True` and `separators=(",", ":")`.

## Determinism Guarantee

Running the generator twice with the same seed **always** produces
identical manifest digests:

```python
first  = generate_particle_fixture(out1, seed=20260715)
second = generate_particle_fixture(out2, seed=20260715)
assert first.manifest_digest == second.manifest_digest  # always True
```

This is achieved by:

1. Using `random.Random(seed)` — never touching global random state.
2. Calling the RNG in a fixed order for every experiment (anomaly flags
   modify values *after* all RNG calls, without consuming additional
   random state).
3. Re-zipping Excel files with fixed ZIP entry timestamps.
4. Patching the `docProps/core.xml` modified timestamp to a fixed value
   (openpyxl overrides it with `datetime.now()` during save).

## API

```python
from pathlib import Path
from examples.particle_size.generate import (
    generate_particle_fixture,
    ParticleFixtureSummary,
)

summary: ParticleFixtureSummary = generate_particle_fixture(
    output=Path(".artifacts/particle-size"),
    seed=20260715,
)
# summary.manifest_digest   -> "sha256:..."
# summary.experiment_count   -> 60
# summary.duplicate_count    -> 2
# summary.self_check_failures -> 3
# summary.moisture_warnings  -> 2
```

## Testing

```bash
# Run fixture-specific tests
python -m pytest tests/unit/examples/test_particle_fixture.py -v

# Run full unit + contract suite
python -m pytest tests/unit/ tests/contract/ -q
```
