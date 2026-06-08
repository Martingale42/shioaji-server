# Code Review: Backlog Batch 2 (BL-3 Catalog Metadata Restamp)

**Date:** 2026-06-08
**Reviewer:** Code Reviewer (Batch 2)
**Commits:** `e370720`, `8c84c18`
**Repo:** `shioaji-server` @ `main`

## Verdict: APPROVED WITH NOTES

Timestamps intact (true UTC). NT-readable. Tests pass. Linter clean. One minor observation, zero blockers.

---

## Critical Data Verification (RED LINE)

### Timestamps NOT re-shifted

| Check | Result |
|---|---|
| First tick `ts_event` UTC | `2020-03-02 01:00:00.586000+00:00` (hour 01, true UTC) |
| First bar `ts_event` UTC | `2020-03-02 01:01:00+00:00` (hour 01, true UTC) |
| Last tick `ts_event` UTC | `2026-06-05 06:30:00+00:00` (hour 06, true UTC) |
| Last bar `ts_event` UTC | `2026-06-05 05:30:00+00:00` (hour 05, true UTC) |
| All within TW market hours under true UTC (~01:00-06:30)? | YES |
| Any ~09:00 UTC (pre-shift / double-shift)? | NO |

**Conclusion:** Timestamps are true UTC. No re-shift occurred.

### NT-readability restored

| Check | Result |
|---|---|
| `ParquetDataCatalog('catalog').query(TradeTick)` | 10,609,509 ticks |
| `ParquetDataCatalog('catalog').query(Bar)` | 729,315 bars |
| `MissingMetadata` error? | NO -- queries succeed |

### Schema repair confirmed

**Live catalog (post-restamp):**
- Tick columns: `price`/`size` = `fixed_size_binary[16]`, `trade_id` = `string`
- Tick kv: `instrument_id`, `price_precision`, `size_precision` all present
- Bar columns: `open`/`high`/`low`/`close`/`volume` = `fixed_size_binary[16]`
- Bar kv: `bar_type`, `instrument_id`, `price_precision`, `size_precision` all present

**Backup (pre-restamp, damaged):**
- Tick columns: `price`/`size` = `large_binary`, `trade_id` = `large_string`
- Tick kv: NONE (all stripped)
- Bar columns: all `large_binary`
- Bar kv: NONE (all stripped)

Cross-reference confirms the repair is genuine and the backup preserves the damaged state.

### Idempotency

| Check | Result |
|---|---|
| `.metadata_restamped` marker exists | YES |
| Marker content matches counts | `instruments=2, tick_files=3041, tick_rows=10609509, bar_files=152, bar_rows=729315` |
| Re-running would abort | YES (marker check at line 199-205) |

### Backup

| Check | Result |
|---|---|
| `catalog_pre_restamp_backup/` exists | YES |
| Contains 3041 tick + 152 bar files | YES |
| Has `.ts_utc_migrated` marker (Batch 2) | YES |

---

## Test & Lint Results

| Suite | Result |
|---|---|
| `uv run pytest tests/ -v` | 28 passed (including 4 restamp-specific tests) |
| `uv run ruff check src/ tests/ scripts/` | All checks passed |

---

## File-by-File Review

### `scripts/restamp_catalog_metadata.py` (commit `e370720`)

**Correctness vs plan:** Faithful implementation. Canonical schemas match the plan's specification. `_repair_table` does `cast()` + `replace_schema_metadata()` only -- no arithmetic on timestamp columns. The `ArrowSerializer.deserialize()` step acts as a runtime proof that each file is NT-valid before writing to the output catalog.

**Error handling:** Per-instrument `try/except` with `log.exception()` (line 244-247) -- failures are collected, not silent. Non-zero exit on any error (line 309-312). Good.

**Docstrings:** Mathematical docstring convention followed on `_repair_table` and `restamp`. `RestampStats` is a plain dataclass, appropriate.

**Security:** No user-facing input beyond CLI `--catalog-path` / `--out-path`. No network I/O. No eval/exec. Clean.

**YAGNI:** No over-engineering. Script does one thing well.

### `tests/test_restamp_metadata.py` (commit `e370720`)

**Correctness:** Tests simulate the actual damage mechanism (polars round-trip) rather than hand-crafting bad data -- this is excellent because it validates the real failure mode.

Four tests cover:
1. TradeTick repair + value identity
2. TradeTick timestamp preservation (critical)
3. Bar repair + value identity
4. Bar timestamp preservation (critical)

**Observation:** `_write_nt_and_damage` creates temp dirs via `tempfile.mkdtemp()` (line 96) without cleanup. These are small and pytest cleans up the process, but `tmp_path` fixture would be more idiomatic. Not a blocker -- see Minor-1.

### `scripts/migrate_ts_to_utc.py` (commit `8c84c18`)

**Correctness:** Warning docstring added at module level, clearly states the polars round-trip damage and points to the repair script. Appropriate.

### `docs/BACKLOG.md` (commit `8c84c18`)

**Correctness:** BL-3 reclassified from "tech-debt / pre-existing" to "Batch 2 regression" with the checkbox marked done. Background corrected with strikethrough of the old claim. Verification numbers match (`10,609,509 ticks + 729,315 bars`).

### `docs/AUDIT.md` (commit `8c84c18`)

**Correctness:** Correction note appended after the existing table, clearly labelled with date and BL-3 reference. Does not alter the original audit findings, only adds the correction.

### `docs/sessions/backlog/progress.json` + `docs/sessions/progress.json` (commit `8c84c18`)

**Correctness:** Batch progression updated, `open_followups` pruned, `resolved_followups` added with accurate narrative.

---

## Findings

### Minor-1: Temp dir cleanup in tests [tests/test_restamp_metadata.py:96]

`_write_nt_and_damage` uses `tempfile.mkdtemp()` without cleanup. Prefer pytest `tmp_path` fixture or `tempfile.TemporaryDirectory()` context manager for automatic cleanup. Not a data correctness issue.

### Minor-2: `startswith` matching for bar dirs [scripts/restamp_catalog_metadata.py:159]

`bar_dir.name.startswith(iid)` could false-match if an instrument ID is a prefix of another (e.g. `0050.SIN` matching `0050.SINOPAC-...`). Current catalog has only `0050.SINOPAC` and `00631L.SINOPAC` -- no collision. But if a new instrument like `0050.SINOPAC_SPECIAL` were ever added, it would match `0050.SINOPAC` entries too. A safer check would be `bar_dir.name.startswith(iid + "-")`. Extremely low risk given NT naming conventions, but noted for future-proofing.

---

## Summary

| Category | Count |
|---|---|
| Critical | 0 |
| Important | 0 |
| Minor | 2 |

The DATA RED LINE is satisfied: timestamps are true UTC (~01:00 range), not double-shifted (~09:00). The schema repair is verified at both the pyarrow level (column types + kv metadata) and the NT level (`ParquetDataCatalog.query()` succeeds). The backup preserves the damaged state for rollback. Idempotency marker prevents accidental re-runs. Tests accurately simulate the damage mechanism. Docs corrections are accurate and well-sourced.
