# QA — `shioaji-data` CLI (full user-perspective test)

> Date: 2026-06-10 · Tester: QA agent · Branch: `main`
> Feature: `shioaji-data` CLI (batch + parallel Shioaji→NT data pipeline), all 3 batches implemented + code-reviewed APPROVED.
> Scope: build/gate, CLI surface (help/flags), offline edge cases, live happy path.

## Verdict

**PASS** — every planned behavior verified with fresh evidence; 0 bugs found.

- Tests run: **24** (1 build gate + 9 CLI-surface checks + 8 new offline edge-case tests + 4 live/shell checks + 2 existing suites re-run).
- New automated QA tests added: **8** (`tests/test_cli_qa_edge_cases.py`), all green.
- Full suite: **77 passed**, ruff clean.
- Bugs found: **0**.

---

## Environment

| Item | Value |
|---|---|
| Python (venv) | 3.13.7 |
| Entry point | `.venv/bin/shioaji-data` → `shioaji_server.data.cli:main` |
| Live gateway | UP + logged in (`/api/health` → `{"status":"ok","connected":true,"logged_in":true,"session_alive":true}`) |
| Catalog (prod, read-only) | `./catalog` — 2 equities (0050, 00631L) |

> **Install note (not a bug):** the design's literal `uv run pip install -e .` resolves to the *system* Python 3.10 and errors with `requires a different Python: 3.10.12 not in '>=3.13'`. This is harmless here — the `.venv` already has the `shioaji-data` console script installed (3 batches built it), so `uv run shioaji-data ...` works. The correct install verb is `uv pip install -e .` / `uv sync`. Documented as an operator note, not a defect.

---

## Test results

| # | Test | Input | Expected | Actual | Status |
|---|------|-------|----------|--------|--------|
| 1 | Build + gate | `uv run ruff check . && uv run pytest -q` | ruff clean; all pass | ruff clean; 69 passed (baseline) → 77 with QA tests | **PASS** |
| 2 | Top-level `--help` | `shioaji-data --help` | 4 subcommands + program description | lists `fetch-bars/fetch-ticks/instrument-def/inspect`; desc "Batch + parallel Shioaji→NautilusTrader data pipeline." | **PASS** |
| 3 | `fetch-bars --help` | — | global `--catalog`/`--gateway-url`; XOR selector; `--start/--end/--concurrency` | all present | **PASS** |
| 4 | `fetch-ticks --help` | — | as above **plus** `--min-remaining-mb` | all present incl. `--min-remaining-mb` (default 50.0) | **PASS** |
| 5 | `instrument-def --help` | — | global flags + XOR selector | present (also carries `--start/--end/--concurrency` — by design, `_add_range_args` shared; ignored by `write_instrument_def_one`) | **PASS** |
| 6 | `inspect --help` | — | NO ticker args; only global flags | only `--catalog`/`--gateway-url`; no `--code/--codes/--codes-file` | **PASS** |
| 7 | XOR — both selectors | `fetch-bars --code 0050 --codes 0050` | SystemExit (mutually exclusive) | argparse `error: argument --codes: not allowed with argument --code`, exit 2 | **PASS** |
| 8 | Required — no selector | `fetch-bars` (no selector) | SystemExit | argparse `error: one of the arguments --code --codes --codes-file is required`, exit 2 | **PASS** |
| 9 | XOR — code + codes-file | `fetch-ticks --code 0050 --codes-file x.txt` | SystemExit | SystemExit raised | **PASS** |
| 10 | `--codes-file` hygiene | file with header `#`, blank, `   `, indented `#`, real tickers | `resolve_codes` returns only real tickers | `["0050","00631L","2330"]` (blanks + `#` comments excluded) | **PASS** |
| 11 | `--codes` comma split | `--codes "0050, 2330 ,,00631L"` | trim + drop empties | `["0050","2330","00631L"]` (covered by impl test) | **PASS** |
| 12 | Gateway unreachable | `main()` with health probe raising `httpx.ConnectError` | return 1 + friendly message | exit 1; stdout `gateway not reachable at http://localhost:9999 — ...` | **PASS** |
| 13 | Quota tripped mid-batch | real `fetch_ticks_one` + shared `QuotaGate`, remaining drops below floor on day 2 | `status=partial`, `last_date` set, per-ticker resume hint, exit-2 logic | `status="partial"`, `last_date` set; report has `--code 2330 --start <date>`, no `--start None`; `all_complete=False` → exit 2 | **PASS** |
| 14 | One raising ticker in batch | `run_batch(["0050","BOOM","2330","00631L"])`, BOOM raises | BOOM→failed; others complete; order preserved | BOOM `status="failed" error="simulated tick fetch crash"`; siblings `complete`; order/cardinality preserved | **PASS** |
| 15 | Unknown ticker → no_data | `fetch_ticks_one` with probe=False | `status=no_data` (NOT failed) | `status="no_data"`; distinct from the `failed` produced by a raising coroutine | **PASS** |
| 16 | `format_batch_report` none-date | partial with `last_date=None` | safe `--start <original>` placeholder, no crash | covered by impl test `test_format_batch_report_handles_none_last_date` | **PASS** |
| 17 | LIVE inspect | `shioaji-data inspect` (prod catalog, read-only) | equity defs + bar summary + gaps, exit 0 | printed 2 equities, bar summary table, gap report; exit 0; prod catalog untouched | **PASS** |
| 18 | LIVE instrument-def | `instrument-def --code 0050 --catalog <TEMP>` | writes def, idempotent, exit 0; no prod mutation | `1 complete ... (1)`, wrote `0050.SINOPAC` def into temp; `git status catalog` clean (prod untouched); exit 0 | **PASS** |

> Tests 13–16 are encoded in `tests/test_cli_qa_edge_cases.py` (offline, fakes/monkeypatch — no gateway/network). Test 11/16 additionally covered by the implementation's own suite.

---

## Edge-case detail (step 3)

| Scenario | Input | Expected | Actual | Status |
|---|---|---|---|---|
| `--code` + `--codes` together | both flags | SystemExit | SystemExit (argparse XOR) | PASS |
| neither selector (fetch-bars) | no selector | SystemExit | SystemExit (required group) | PASS |
| `--codes-file` with blanks + `#` comments | mixed file | only real tickers | `["0050","00631L","2330"]` | PASS |
| gateway unreachable | `ConnectError` on `/api/health` | `main()` returns 1 + friendly msg | exit 1, `gateway not reachable at ...` printed | PASS |
| quota tripped mid-batch | remaining < floor on day 2 | partial + per-ticker resume hint, exit 2 | partial, `last_date` set, hint emitted, exit-2 logic holds | PASS |
| one raising ticker | BOOM raises in `run_batch` | others complete, BOOM=failed | isolated via `return_exceptions=True` | PASS |
| unknown ticker | probe returns False | `no_data` (not failed) | `no_data`; an exception (different path) → `failed` | PASS |

---

## Bugs found

**None.** All planned behaviors hold; the no_data-vs-failed distinction, quota-trip partial/resume semantics, failure isolation, XOR/required argument enforcement, gateway-down exit 1, and exit-code mapping (0/1/2) all behave per the design doc (`docs/plans/2026-06-09-scripts-cli-design.md` §6).

### Non-blocking observations (not defects)

1. **Install verb mismatch** — `uv run pip install -e .` (as written in the plan's verification step) hits system Python 3.10 and errors `requires a different Python: >=3.13`. Use `uv pip install -e .` / `uv sync`. The venv already has the console script, so the CLI is fully usable; this is a doc/UX nit only.
   - Location: `docs/plans/2026-06-09-shioaji-data-cli.md` Task 5/6 verification lines (`uv run pip install -e .`).
2. **argparse error exit code is 2**, the same numeric code the CLI uses for "partial/failed batch". They are unambiguous in context (argparse errors print a usage block to stderr; batch partials print a report to stdout and only after a successful parse), and the task only required `SystemExit` for the XOR cases — which holds. Noted for awareness, not a defect.
3. **`instrument-def` accepts `--start/--end/--concurrency`** which it ignores (shared `_add_range_args`). Harmless; matches the design's "shared command, flag-distinguished" decision. Concurrency is in fact honored (batch of N defs), start/end are inert for this subcommand.

---

## Verification commands (evidence)

```text
$ uv run ruff check .                  → All checks passed!
$ uv run pytest -q                     → 77 passed, 1 warning
$ uv run shioaji-data --help           → 4 subcommands + description
$ uv run shioaji-data inspect          → equity defs + bar summary (exit 0)
$ uv run shioaji-data instrument-def --code 0050 --catalog <TEMP>
                                       → "1 complete, 0 partial, 0 no_data, 0 failed (1)" (exit 0)
$ git status --porcelain catalog       → (empty — production catalog untouched)
```
