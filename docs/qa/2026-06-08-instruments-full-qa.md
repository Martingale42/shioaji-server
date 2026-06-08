# QA Report: Instrument Definitions (WS-A…D)

**Date**: 2026-06-08 (run executed 2026-06-09)
**Tester role**: `docs/sessions/instruments/03-qa-tester.md`
**Repos under test**:
- `/home/cy/Code/MT5/shioaji-server` @ `main` (WS-A gateway, WS-C/D scripts)
- `/home/cy/Code/MT5/nautilus_trader` @ `sinopac-adapter-clean` (WS-B Rust parse)

**Verdict**: **PASS WITH ISSUES**

> All behavior in the testable scope is correct and proven by real test output. No
> functional bug was found. The only issues are (1) two minor doc/cosmetic
> observations (severity LOW, no behavior impact) and (2) the plan-sanctioned
> live-integration deferrals (sinopac wheel + gateway creds unavailable in this
> env). FAIL was reserved for an actually-broken behavior; none was found.

---

## Suites Run (real counts)

| Suite | Command | Result |
|-------|---------|--------|
| shioaji-server pytest | `uv run pytest tests/ -v` | **55 passed, 0 failed** (1 pydantic-deprecation warning from the vendored `shioaji` lib, not our code) |
| shioaji-server ruff | `uv run ruff check src/ tests/ scripts/` | **All checks passed!** |
| nautilus-sinopac cargo (lib) | `cargo test -p nautilus-sinopac --features python` | **77 passed, 0 failed** |
| nautilus-sinopac cargo (http integ) | (same invocation) | **5 passed, 0 failed** |
| nautilus-sinopac cargo (ws integ) | (same invocation) | **3 passed, 0 failed** |
| nautilus-sinopac cargo (doctests) | (same invocation) | **0 tests** |
| sinopac Python integration | `uv run --active --no-sync pytest tests/integration_tests/adapters/sinopac/` | **DEFERRED** — `error: Required uv version ==0.11.6 does not match the running version 0.8.22` (version-gate, verified; tests are config/exec/factory, NOT instrument-parse) |

**Cargo grand total: 85 tests passed (77 + 5 + 3), 0 failed.**
**Python (shioaji-server): 55 tests passed, 0 failed.**

---

## Test Results

| # | Category | Test / Probe | Input | Expected | Actual | Status |
|---|----------|--------------|-------|----------|--------|--------|
| 1 | WS-A | `test_options_right_call_is_C` / `_put_is_P` | real `shioaji.Option` w/ `OptionRight.Call`/`.Put` | `option_right` == "C" / "P", NOT "OptionRight.Call" | "C" / "P" | PASS |
| 2 | WS-A | `test_stock_currency_is_value_not_repr` | real `Stock` w/ `Currency.TWD` | "TWD", NOT "Currency.TWD" | "TWD" | PASS |
| 3 | WS-A | `test_stock_exchange_is_usable_code` | real `Stock` w/ `Exchange.TSE` | "TSE", NOT "Exchange.TSE" | "TSE" | PASS |
| 4 | WS-A | `test_stock_day_trade_is_value` | real `Stock` w/ `DayTrade.Yes` | "Yes", NOT "DayTrade.Yes" | "Yes" | PASS |
| 5 | WS-A | `test_{stock,futures,options}_has_authoritative_fields` | real contracts | carry `unit`/`multiplier`/`currency`(/`underlying_code`) | all present, correct values | PASS |
| 6 | WS-A | `test_serializers_tolerate_missing_optional_fields` | duck-typed bare contract | defaults, no raise | `currency=""`, `unit=0`, `multiplier=0`, `day_trade=""` | PASS |
| 7 | WS-A | **QA probe**: serializer dict keys ↔ Pydantic `response_model` fields | 3 real contracts | exact key parity (0 missing / 0 extra) + round-trip validates | Stock 13/13, Futures 15/15, Options 17/17, all round-trip OK | PASS |
| 8 | WS-A | **QA probe**: `_enum_value` edge cases | plain `"C"`, `None`, `None+default`, `int 50` | passthrough / "" / default / "50" | `'C'` / `''` / `'TWD'` / `'50'` | PASS |
| 9 | WS-B | `test_option_right_c_parses_call` / `_p_parses_put` | `option_right="C"`/"P" | `OptionKind::Call` / `Put`, no `bail!` | Call / Put | PASS |
| 10 | WS-B | `test_option_right_unknown_bails` | `option_right="Call"` (pre-WS-A spelling) | `Err` (bails) | `is_err()` true | PASS |
| 11 | WS-B | `test_futures_uses_authoritative_multiplier` | `multiplier=777` (not in table) | `multiplier=777` (not 200) | 777.0 | PASS |
| 12 | WS-B | `test_futures_multiplier_zero_falls_back_to_table` | `multiplier=0`, cat=TXF | fallback `futures_multiplier("TXF")` = 200 | 200.0 | PASS |
| 13 | WS-B | `test_options_uses_authoritative_multiplier` | `multiplier=99` (not table 50) | 99 | 99.0 | PASS |
| 14 | WS-B | `test_options_multiplier_zero_falls_back_to_table` | `multiplier=0`, cat=TXO | fallback `options_multiplier("TXO")` = 50 | 50.0 | PASS |
| 15 | WS-B | `test_futures_unit_sets_lot_size` / `test_stock_unit_sets_lot_size` | `unit=5` / `unit=100` | `lot_size=5` / `100` | 5.0 / 100.0 | PASS |
| 16 | WS-B | `test_stock_missing_unit_falls_back_to_default_lot` | `unit=0`, `currency=""` | `lot_size=1000`, `currency=TWD` | 1000.0 / TWD | PASS |
| 17 | WS-B | `test_twse_tick_size_tsmc_reference` | `reference=580.0` (>100 TWD) | tick `(1.0, 1)`, NOT 0.01 | (1.0, 1) | PASS |
| 18 | WS-B | TWSE tier ladder (`under_10`…`above_1000` + `boundary_at_10`) | 5/10/25/75/300/750/1500 | 0.01/0.05/0.05/0.10/0.50/1.0/5.0 | all match schedule | PASS |
| 19 | WS-B | `test_parse_options_to_contract_call` (fixture JSON) | `contracts_options.json[0]` TXO20000C6 | OptionContract, kind=Call, mult=50, ccy=TWD, strike=20000 | all assert | PASS |
| 20 | WS-C | `test_load_instrument_returns_provider_instrument` | provider `find()`→Equity(1.0/2000) | returns provider inst verbatim, NOT legacy 0.01/1000 | 1.00 / 2000, `load_async`+`find` called w/ id | PASS |
| 21 | WS-C | `test_make_provider_wires_client_into_provider` | gateway url | `SinopacHttpClient(base_url=…)` wrapped in `SinopacInstrumentProvider(client=…)` | both asserted | PASS |
| 22 | WS-C | `test_script_path_writes_via_write_data` | provider inst | `catalog.write_data([inst])` unmodified | called once w/ `[equity]` | PASS |
| 23 | WS-C | `test_legacy_hardcoded_builders_are_gone` | scripts source | `make_equity`/`contract_to_equity` gone; all 3 fetch scripts import shared helper | grep asserts pass | PASS |
| 24 | WS-C | same-instrument equality (live node vs backtest script) | — | identical id/tick/lot/mult | **DEFERRED** (no sinopac wheel + no gateway) | DEFERRED |
| 25 | WS-D | `test_dry_run_writes_nothing` | temp catalog: legacy Equity + 5 ticks + 3 bars | `instruments=1`, `regenerated=0`, no marker, no backup, def still 0.01/1000, counts unchanged | all assert | PASS |
| 26 | WS-D | `test_real_regen_fixes_def_and_preserves_data` | mocked provider → 0.05/2000 | def→0.05/2000; tick/bar count + first ts_event identical; fp_before==fp_after | all assert (data red line held) | PASS |
| 27 | WS-D | `test_idempotent_marker_aborts_second_run` | 2× real regen | 2nd run `regenerated=0` | asserts | PASS |
| 28 | WS-D | `test_aborts_if_backup_dir_already_exists` | pre-existing backup dir | `regenerated=0`, error, no marker | asserts | PASS |
| 29 | WS-D | **read-only dry-run on REAL catalog** | `--catalog-path ./catalog --dry-run` | reports N instruments, mutates nothing | 2 instruments (`0050`, `00631L`), both `tick=0.01 lot=1000`, `regenerated=0 errors=0`; tree content-hash identical before/after; no marker; no `catalog_pre_instrument_regen_backup/`; `catalog_pre_restamp_backup/` preserved | PASS |
| 30 | WS-D | real regen on production catalog | — | corrected tick/lot in real catalog | **DEFERRED** (no sinopac wheel + no gateway creds) | DEFERRED |
| 31 | Regress | shioaji-server full suite | `uv run pytest tests/` | green | 55/55 | PASS |
| 32 | Regress | shioaji-server lint | `uv run ruff check …` | green | clean | PASS |
| 33 | Regress | nautilus-sinopac cargo | `cargo test -p nautilus-sinopac --features python` | green | 85/85 | PASS |

---

## Evidence (key raw output excerpts)

**WS-A serializer↔model parity probe** (`/tmp/qa_probe.py`, run via `uv run python`):
```
=== Stock ===   MISSING in dict: []  EXTRA in dict: []  ROUND-TRIP: OK
=== Futures === MISSING in dict: []  EXTRA in dict: []  ROUND-TRIP: OK
=== Options === MISSING in dict: []  EXTRA in dict: []  ROUND-TRIP: OK
=== _enum_value edge cases ===
  plain str 'C' -> 'C'   None -> ''   None w/ default 'TWD' -> 'TWD'   int 50 -> '50'
```

**WS-D real-catalog dry-run** (read-only):
```
Found 2 instruments: ['0050.SINOPAC', '00631L.SINOPAC']
  0050.SINOPAC   BEFORE: ticks=8068863 (first_ts=1583110801187000000) bars=398643 ... | def tick=0.01 lot=1000
  00631L.SINOPAC BEFORE: ticks=2540646 (first_ts=1583110800586000000) bars=330672 ... | def tick=0.01 lot=1000
DRY-RUN: would regenerate 2 instrument definitions (no backup, no write)
DRY-RUN complete: instruments=2 regenerated=0 errors=0
```
Catalog content-hash (size+path over 3196 files) identical before vs after the dry-run:
`4a49816629fbd757722ff184e110824623c40e2dc92c960df6f22564b35663bf` (both). No marker
created, no `catalog_pre_instrument_regen_backup/`, `catalog_pre_restamp_backup/` (213M) preserved.

**Real catalog still legacy** (post-dry-run inspect):
```
0050.SINOPAC: tick=0.01 lot=1000 type=Equity
00631L.SINOPAC: tick=0.01 lot=1000 type=Equity
```

**Environment-constraint verification** (NOT assumed):
```
nautilus_trader 1.224.0
adapters: [... 'shioaji', ...]            # OLD shioaji-named adapter shipped
import nautilus_trader.adapters.sinopac → ModuleNotFoundError
nautilus_pyo3.sinopac                  → AttributeError
uv (global) 0.8.22  vs  pyproject required-version ==0.11.6   # version-gate real
```

---

## Bugs Found

**None of functional severity.** Two LOW observations (cosmetic / documentation only):

### Obs 1 (LOW, doc): role file's headline test count is stale
- **Where**: orchestrator/role context says "141 tests 全過"; the instrument-relevant
  shioaji-server suite is **55 tests** (the 141 was a prior whole-project count across
  all backlog components, not the instrument slice).
- **Impact**: none on behavior — just a number to keep accurate in the next report.
- **Reproduce**: `uv run pytest tests/ -v` → `55 passed`.
- **Expected/Actual**: expected the report's count to match the suite; actual differs.
  Filed as a note, not a defect.

### Obs 2 (LOW, defensive): `parse_currency_or_twd` silently downgrades a typo to TWD
- **Where**: `nautilus_trader/crates/adapters/sinopac/src/http/parse.rs:350-355`.
- **Behavior**: any unrecognized currency code (e.g. a future SDK typo like `"USDD"`)
  falls back to TWD without logging. For Taiwan venues this is the documented, correct
  default (all instruments are TWD-quoted), so it is intentional, not a bug. Noted only
  as a future hardening opportunity (a `tracing::warn!` on the unrecognized-non-empty
  branch would aid diagnosis if a non-TWD venue is ever added).
- **Impact**: none today — every WS-B/WS-A path is TWD.

---

## Deferred (environment) — plan-sanctioned, with hand-off preconditions

These were deferred because the live integration substrate is unavailable in this env.
They are NOT failures; they are gated on preconditions a future executor must satisfy.

| Item | Why deferred (verified) | Hand-off precondition to run |
|------|-------------------------|------------------------------|
| **WS-C real same-source equality** (live node instrument == backtest-script instrument: id/tick/lot/multiplier) | `nautilus_trader.adapters.sinopac` → `ModuleNotFoundError`; `nautilus_pyo3.sinopac` → `AttributeError` in shioaji-server venv (ships OLD `shioaji` adapter from wheel `v1.224.0`). The WS-B `.so` exists only at `nautilus_trader/target/debug/libnautilus_sinopac.so`, not packaged into either venv. | Build + install the rebuilt fork wheel (sinopac adapter) into shioaji-server's venv; start the gateway on `:8000` with valid Sinopac creds; then load the same `InstrumentId` via both `SinopacInstrumentProvider` (script) and a booted live node and assert field equality. |
| **WS-D real regen on production catalog** | Same missing sinopac wheel + no gateway/creds. Real catalog deliberately untouched: no `catalog/.instruments_regenerated`, no `catalog_pre_instrument_regen_backup/`. | Same wheel + running logged-in gateway, then `uv run python -m scripts.regen_catalog_instruments --catalog-path ./catalog --gateway-url http://localhost:8000` (drop `--dry-run`). Expect a `catalog_pre_instrument_regen_backup/` snapshot, the marker written, `0050`/`00631L` definitions corrected to their reference-tiered tick/lot, and the data red line (tick/bar counts + first ts_event) verified identical. |
| **sinopac Python integration tests** (`tests/integration_tests/adapters/sinopac/`) | `uv run` refuses to launch: `Required uv version ==0.11.6 does not match running 0.8.22` (`pyproject.toml` `required-version = "==0.11.6"`). These tests are config/execution/factory wiring — they do NOT touch instrument-parse fields (grep for `Equity/OptionContract/multiplier/lot_size/option_right` returns nothing), so WS-B instrument correctness is already fully covered by the 85 passing Rust tests. | Install uv `0.11.6` (`uv self update 0.11.6`) in the nautilus_trader repo, then `uv run --active --no-sync pytest tests/integration_tests/adapters/sinopac/ -v`. |

---

## Reviewer Notes (test-rigor audit, not just green ticks)

I read each batch's tests to confirm they assert **real distinguishing behavior**, not
vacuous truths:

- **WS-A**: tests build real `shioaji` `Stock`/`Future`/`Option` types (so enum classes
  and field names match production), and assert both the positive ("C"/"P"/"TWD"/"TSE")
  and the negative ("not OptionRight.Call"/"not Currency.TWD"). My extra probe proved
  serializer dict keys are an exact set-match to each Pydantic `response_model` (0 missing,
  0 extra) and that the dict round-trips through the model — i.e. the gateway will not 502
  on a `response_model` mismatch.
- **WS-B**: multiplier tests use values **deliberately outside** the hardcoded table
  (777 futures, 99 options) to prove the authoritative path, and `0` to prove the table
  fallback — so a regression to "always use the table" would fail. The `bail!` test uses
  the exact pre-WS-A spelling `"Call"` to guard the bug that broke options wholesale.
- **WS-D**: the data red line is asserted three ways — row counts, first `ts_event`, and a
  before/after `DataFingerprint` equality — on a real temp `ParquetDataCatalog` seeded with
  actual `TradeTick`/`Bar` rows, with the provider mocked to return a *different* tick/lot
  (0.05/2000) so a no-op regen would fail the definition assertion. Idempotency-marker and
  backup-clobber aborts are both covered.
- **WS-C**: the mock returns a non-legacy 1.0/2000 instrument so "provider value flows
  through unmodified" is unambiguous; `_make_provider` wiring and the `write_data` call site
  are both asserted; and the retired builders are grep-asserted gone with a positive check
  that all three fetch scripts now import the shared helper.

The deferred live checks are the genuinely un-runnable slice; everything testable is green.
