# Code Review — Batch 3 (Tasks 5 & 6): `shioaji-data` CLI + entry + docs

**Reviewer:** Code Reviewer
**Date:** 2026-06-09
**Scope:** `git diff 55dfbef..HEAD` — two commits:

- `0040482` feat: shioaji-data CLI (argparse, 4 subcommands, console_scripts entry) — Task 5
- `2696aed` docs: README usage for shioaji-data CLI — Task 6

**Files reviewed:** `src/shioaji_server/data/cli.py`, `src/shioaji_server/data/inspect.py`,
`pyproject.toml`, `tests/test_data_cli.py`, `README.md`. Cross-checked against the
authoritative `src/shioaji_server/data/fetch.py` (Task 3/4) signatures.

---

## Verdict: APPROVED

The batch implements Task 5 & 6 to spec. All required gates are green, every
Batch-3-specific check passes, and the one intentional design choice that
deviates from a stale doc paragraph (signature order) is the *correct* one. No
Critical or Important findings. Two Minor notes are documentation/semantic
nits, not blockers.

---

## Batch 3 specific checks — results

| Check | Result | Evidence |
|-------|--------|----------|
| MUTUAL EXCLUSION: `--code` XOR `--codes` XOR `--codes-file`, required group | PASS | `cli.py:58` `add_mutually_exclusive_group(required=True)`; `inspect` has no ticker args (`cli.py:132-136`). "both" → `test_code_and_codes_mutually_exclusive`; "neither" → `test_ticker_selector_is_required`. Both SystemExit. |
| EXIT CODES: 0 / 2 / 1 mapping | PASS | `main()` `cli.py:256-261` → 1 on `httpx.ConnectError`; `cli.py:272-273` → `0 if all complete else 2`. Locked by `test_main_returns_1_when_gateway_down` (1) and `test_dispatch_partial_returns_exit_2` (2). |
| CLIENT LIFECYCLE: closed in `finally`, no leak | PASS | `cli.py:266-275` builds `ShioajiClient` inside `_run()` with `try/finally: await client.close()`. `inspect` + gateway-down paths return *before* any client is constructed, so there is nothing to leak. |
| SHARED QUOTAGATE: one gate per batch, bound into closure | PASS | `cli.py:224` constructs exactly one `QuotaGate(client, args.min_remaining_mb)` in the `fetch-ticks` branch of `_build_per_ticker`, bound once and shared by every `per_ticker(code)` call (`cli.py:227`). Not per-ticker. |
| SIGNATURE WIRING: Task 3 authoritative order, not stale design §4.4 | PASS | See "Signature wiring" below. CLI matches `fetch.py`, deliberately NOT design §4.4. |
| CODES-FILE SAFETY: read as plain text, no eval/shell/glob | PASS | `_read_codes_file` (`cli.py:141-156`) does `Path(path).read_text(...).splitlines()`, strips blanks + `#` lines. No `eval`, no `subprocess`, no `glob`. Operator-supplied local path read as text only. |
| inspect.py: leftover `main()`/argparse removed, stale docstring fixed | PASS | `git show 0040482` removes the `main()` + `argparse` import and rewrites the `python -m scripts.inspect_catalog` usage line to `uv run shioaji-data inspect`. Repo-wide grep for `scripts.inspect_catalog` / `inspect.main` is clean. |
| README: shioaji-data section (install, 4 commands, batch example, maintenance note) | PASS | `README.md` adds `## 資料下載 — shioaji-data CLI` with install (`uv pip install -e .`), all 4 commands, a batch example (`fetch-ticks --codes 0050,00631L --concurrency 4`), exit-code table, and the maintenance-chain note (`scripts.maintenance.<x>` stays separate — verified the dir exists). |

### Signature wiring (the load-bearing check)

Design doc §4.4 (`docs/plans/2026-06-09-scripts-cli-design.md:105-107`) is **stale**:

```
fetch_bars_one(client, code, start, end, catalog, gateway_url)
write_instrument_def_one(code, catalog, gateway_url)
```

Authoritative Task 3 signatures in `fetch.py`:

```
fetch_bars_one(client, gateway_url, code, start, end, catalog)          # fetch.py:235
fetch_ticks_one(client, gateway_url, code, start, end, catalog, gate)   # fetch.py:262
write_instrument_def_one(client_url, code, catalog)                     # fetch.py:225
```

The CLI calls (`cli.py:214`, `:227`, `:233`) follow the **authoritative** order
positionally — correct. `write_instrument_def_one(gateway_url, code, catalog)`
maps `gateway_url → client_url`, which is right: that helper opens its own
provider via `load_instrument(gateway_url, ...)` and does not need the shared
`ShioajiClient` at all. Verified `load_instrument`'s first positional arg is a
gateway URL (`instruments.py:56`).

---

## Findings

### Critical
None.

### Important
None.

### Minor

1. **[design doc §4.4 — `docs/plans/2026-06-09-scripts-cli-design.md:105-107`] Stale signatures not corrected.**
   The CLI correctly ignores them (per the task brief, §4.4 is known-stale and
   `fetch.py` is authoritative), and `cli.py:204-206` even documents the
   divergence in a docstring. Non-blocking, but a one-line fix to the design doc
   would prevent a future reader from "fixing" the CLI to match the wrong order.

2. **[`cli.py:272` / design doc §intent] `no_data` maps to exit 2.**
   `all(r.status == "complete")` means a batch containing a `no_data` ticker
   (probe returned false — explicitly "非失敗 / not a failure" per design doc
   line 140) still exits `2`. This matches the **Task 5 spec verbatim**
   ("`return 0` if all complete else `2`", plan line 231) and is the
   conservative/operator-friendly reading (a non-`complete` ticker surfaces a
   non-zero), so no code change is requested. Flagging only because design doc
   line 140 ("no_data is not a failure") vs line 141 ("exit 2 on
   failed/incomplete") are mildly in tension; whichever is desired, the code's
   behaviour is defensible and `format_batch_report` tallies `no_data`
   separately so the operator sees exactly why. If "no_data should be exit 0"
   is ever wanted, change the predicate to
   `r.status in ("complete", "no_data")`.

### Nits (no action)

- `instrument-def` inherits `--start`/`--end`/`--concurrency` via `_add_range_args`
  even though `write_instrument_def_one` ignores start/end. This is **per the
  plan** (Task 5: the three fetch subcommands get the range args) and keeps the
  subparser shapes uniform; `--concurrency` is genuinely used for batch
  instrument-def. No change.

---

## Verification results

### `uv run ruff check .`
```
All checks passed!
```

### `uv run pytest -q` (tail)
```
.....................................................................    [100%]
69 passed, 1 warning in 1.39s
```
(The 1 warning is an upstream Pydantic-v2 deprecation inside the `shioaji`
package, unrelated to this batch.)

### `uv run pytest tests/test_data_cli.py -v`
```
tests/test_data_cli.py::test_parser_has_four_subcommands PASSED          [ 12%]
tests/test_data_cli.py::test_code_and_codes_mutually_exclusive PASSED    [ 25%]
tests/test_data_cli.py::test_ticker_selector_is_required PASSED          [ 37%]
tests/test_data_cli.py::test_resolve_codes_from_file PASSED              [ 50%]
tests/test_data_cli.py::test_resolve_codes_from_codes_flag PASSED        [ 62%]
tests/test_data_cli.py::test_dispatch_routes_fetch_ticks PASSED          [ 75%]
tests/test_data_cli.py::test_dispatch_partial_returns_exit_2 PASSED      [ 87%]
tests/test_data_cli.py::test_main_returns_1_when_gateway_down PASSED     [100%]
8 passed in 0.61s
```

### `uv run shioaji-data --help` (parser surface)
```
usage: shioaji-data [-h] {fetch-bars,fetch-ticks,instrument-def,inspect} ...

positional arguments:
  {fetch-bars,fetch-ticks,instrument-def,inspect}
    fetch-bars          download 1-min bars for one or many tickers
    fetch-ticks         download trade ticks day-by-day, quota-aware
    instrument-def      write only the NT instrument definition(s)
    inspect             equity-definition + bar-quality report
```

Subcommand `--help` confirmed for all four:
- `fetch-bars` / `instrument-def`: `(--code | --codes | --codes-file)` required group
  + `--catalog --gateway-url --start --end --concurrency`.
- `fetch-ticks`: same, plus `--min-remaining-mb` (default `50.0`).
- `inspect`: only `--catalog --gateway-url` (no ticker args). Matches spec.

---

## Test quality assessment

Tests are meaningful, not box-ticking:

- Parser-shape and mutual-exclusion tests assert behaviour through the real
  `build_parser()` (no internals reimplemented except a benign `_actions`
  scan for choices).
- `resolve_codes` is tested for both `--codes-file` (blank + `#` comment +
  whitespace skipping) and `--codes` (comma-split, empties dropped).
- Dispatch tests monkeypatch only the boundary (`_check_gateway`, `run_batch`,
  `ShioajiClient`, `ParquetDataCatalog`) — fully offline, no gateway — and
  lock the parsed `codes`/`concurrency` that flow into `run_batch`, plus the
  0/2/1 exit-code mapping. This is the right seam per the plan's testing
  philosophy (public CLI surface + dispatch; per-ticker drivers exercised
  indirectly).

All four Batch-3-required paths (mut-ex "both" + "neither", gateway-down exit 1,
partial exit 2) are covered.
