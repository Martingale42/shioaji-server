# shioaji-data CLI Implementation Plan

> **For Claude:** Use superpowers:executing-plans, superpowers:subagent-driven-development, or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Wrap the Paradigm B download/inspect scripts into a single `shioaji-data` console command (promoted into `src/shioaji_server/data/`), with single-or-batch ticker fetching that runs in parallel via asyncio.

**Architecture:** Promote `scripts/{bars,client,instruments,inspect_catalog,fetch_single,fetch_single_ticks}.py` into an installed subpackage `shioaji_server.data`. A thin `cli.py` (argparse) parses 4 subcommands and dispatches to pure async drivers in `fetch.py`/`inspect.py`. Batch (`--codes`) runs tickers concurrently with `asyncio.gather` bounded by a semaphore, coordinated by a shared throttled `QuotaGate` that stops launching work when the daily 500 MB quota runs low. The one-shot maintenance chain stays in `scripts/maintenance/`.

**Tech Stack:** Python 3.13, argparse (stdlib, zero new deps), asyncio + httpx.AsyncClient, NautilusTrader ParquetDataCatalog, hatchling/uv, pytest.

**Design doc:** `docs/plans/2026-06-09-scripts-cli-design.md`

**Conventions:** All Python via `uv run`. English commit messages, no AI-attribution footer. Each task leaves the tree green (`uv run ruff check` + `uv run pytest` pass) and is committed.

---

### Task 1: Scaffold `data/` package + move `inspect_catalog.py`

**Files:**
- Create: `src/shioaji_server/data/__init__.py`
- Move: `scripts/inspect_catalog.py` → `src/shioaji_server/data/inspect.py`

**Implementation:**

`inspect_catalog.py` has no internal/test consumers, so it moves cleanly first and establishes the new package.

1. `git mv scripts/inspect_catalog.py src/shioaji_server/data/inspect.py`
2. Add `src/shioaji_server/data/__init__.py` — short docstring: "Same-source Shioaji→NT data pipeline (download + inspect). Exposed via the `shioaji-data` CLI."
3. In `inspect.py`, keep `inspect_equities()` / `inspect_bars()` / `ns_to_dt()` and a thin `inspect_catalog(catalog_dir: Path) -> None` that calls both. Keep the `main()`/argparse for now (removed in Task 5 once the CLI calls `inspect_catalog`), OR drop `main()` and keep only the callable — either is fine since nothing imports it.

**Verification:**

Run: `uv run python -c "from shioaji_server.data.inspect import inspect_catalog; print('ok')"`
Expected: `ok`
Run: `uv run ruff check src/shioaji_server/data/ && uv run pytest -q`
Expected: ruff clean, all tests pass (nothing referenced `scripts.inspect_catalog`).

**Commit:**
```bash
git add src/shioaji_server/data/ scripts/inspect_catalog.py
git commit -m "refactor: scaffold shioaji_server.data, move catalog inspect into it"
```

---

### Task 2: Relocate shared modules (client, bars, instruments) + repoint all importers

**Files:**
- Move: `scripts/client.py` → `src/shioaji_server/data/client.py`
- Move: `scripts/bars.py` → `src/shioaji_server/data/bars.py`
- Move: `scripts/instruments.py` → `src/shioaji_server/data/instruments.py`
- Modify: `scripts/fetch_single.py`, `scripts/fetch_single_ticks.py` (repoint imports — files stay put this task)
- Modify: `scripts/maintenance/regen_catalog_instruments.py` (import + docstring)
- Modify: `tests/test_instrument_provider_path.py` (import path + grep-guard expected string)

**Implementation:**

This is the coupled relocation — everything that imports these three modules must repoint in the same commit to stay green.

1. `git mv` the three files into `src/shioaji_server/data/`.
2. Inside the moved files, convert internal imports to **relative**:
   - `bars.py`: `from scripts.client import ShioajiClient` → `from .client import ShioajiClient`
   - `client.py`, `instruments.py`: no real `scripts.*` imports (the `scripts.instruments` line in `instruments.py` is a docstring example — update it to `shioaji_server.data.instruments` for accuracy).
3. Repoint the two fetchers (still in `scripts/` this task) to the package:
   - `from scripts.bars import ...` → `from shioaji_server.data.bars import ...`
   - `from scripts.client import ShioajiClient` → `from shioaji_server.data.client import ShioajiClient`
   - `from scripts.instruments import load_instrument` → `from shioaji_server.data.instruments import load_instrument`
4. `scripts/maintenance/regen_catalog_instruments.py:39` → `from shioaji_server.data.instruments import load_instrument`; update the docstring mention (L7) `scripts.instruments.load_instrument` → `shioaji_server.data.instruments.load_instrument`.
5. `tests/test_instrument_provider_path.py`: change `scripts.instruments` import/usages to `shioaji_server.data.instruments`; in `test_legacy_hardcoded_builders_are_gone`, update the expected import string assertions to `"from shioaji_server.data.instruments import load_instrument"` (the files read are still `scripts/fetch_single*.py` this task).

> Transitional state (resolved in Task 3): `scripts/fetch_single*.py` live in `scripts/` but import from the package. This is intentional and green; Task 3 folds them into `data/fetch.py`.

**Verification:**

Run: `uv run ruff check . && uv run pytest -q`
Expected: ruff clean; all tests pass (`test_trade_id`, `test_instrument_provider_path`, `test_regen_catalog_instruments`, `test_restamp_metadata` all green).

**Commit:**
```bash
git add -A
git commit -m "refactor: relocate client/bars/instruments into shioaji_server.data, repoint importers"
```

---

### Task 3: Consolidate fetchers into `data/fetch.py` (single-ticker callables)

**Files:**
- Create: `src/shioaji_server/data/fetch.py`
- Delete: `scripts/fetch_single.py`, `scripts/fetch_single_ticks.py`
- Modify: `tests/test_trade_id.py` (import path)
- Modify: `tests/test_instrument_provider_path.py` (grep guard reads `data/fetch.py`)

**Implementation:**

Merge the two fetchers into one module, separating **logic** (callables returning a result) from the old `main()/parse_args()` (which are dropped — the CLI in Task 5 replaces them).

1. Move verbatim the pure helpers from `fetch_single_ticks.py`: `_tick_type_to_aggressor`, `_ns_to_trade_id`, `ticks_to_trade_ticks`, `trading_days`. Imports use relative form (`from .bars import VENUE, probe_kbar_availability, fetch_stock_bars`, `from .client import ShioajiClient`, `from .instruments import load_instrument`).
2. Add a `TickerResult` dataclass:

```python
@dataclass
class TickerResult:
    code: str
    status: str          # "complete" | "partial" | "no_data" | "failed"
    n_written: int = 0
    last_date: date | None = None
    error: str | None = None
```

3. Extract single-ticker drivers from the old `main()` bodies (behaviour unchanged):

```python
async def write_instrument_def_one(client_url, code, catalog) -> TickerResult: ...
    # load_instrument(client_url, InstrumentId(Symbol(code), VENUE)) -> catalog.write_data([inst])

async def fetch_bars_one(client, gateway_url, code, start, end, catalog) -> TickerResult: ...
    # probe; write instrument def; fetch_stock_bars(); return complete/no_data

async def fetch_ticks_one(client, gateway_url, code, start, end, catalog, min_remaining_mb) -> TickerResult: ...
    # probe; write instrument def; day-by-day loop (move fetch_stock_ticks body here);
    # keep the existing per-ticker check_quota for now (shared QuotaGate arrives in Task 4);
    # return complete (reached end) | partial (quota/empty stop, last_date set) | no_data
```

Keep `check_quota` as a module function for now (Task 4 wraps it in `QuotaGate`).

4. Delete `scripts/fetch_single.py` and `scripts/fetch_single_ticks.py`.
5. `tests/test_trade_id.py`: `from scripts.fetch_single_ticks import _ns_to_trade_id` → `from shioaji_server.data.fetch import _ns_to_trade_id`.
6. `tests/test_instrument_provider_path.py::test_legacy_hardcoded_builders_are_gone`: read `src/shioaji_server/data/fetch.py` instead of the two deleted files; assert no `make_equity`/`contract_to_equity`, and `"from .instruments import load_instrument"` present.

**Tests:** Required — `test_trade_id` (existing, repath) guards the TradeId helper; the grep guard keeps the same-source provider invariant.

**Verification:**

Run: `uv run ruff check . && uv run pytest -q`
Expected: ruff clean; all tests pass.
Run: `uv run python -c "import asyncio, inspect; from shioaji_server.data import fetch; print([n for n in dir(fetch) if n.endswith('_one')])"`
Expected: `['fetch_bars_one', 'fetch_ticks_one', 'write_instrument_def_one']`

**Commit:**
```bash
git add -A
git commit -m "refactor: consolidate fetch_single/fetch_single_ticks into data/fetch.py callables"
```

---

### Task 4: Add `QuotaGate` + `run_batch` (shared quota gate, parallel orchestration)

**Files:**
- Modify: `src/shioaji_server/data/fetch.py`
- Test: `tests/test_batch_fetch.py`

**Implementation:**

Add the batch orchestration and the shared quota coordinator. `run_batch` is the single entry the CLI uses for all fetch commands.

1. `QuotaGate` — centralizes + throttles `/api/account/usage`, single tripped flag (lock-free; single-threaded asyncio):

```python
class QuotaGate:
    def __init__(self, client, min_remaining_mb, ttl_seconds=10.0):
        self._client = client; self._min = min_remaining_mb; self._ttl = ttl_seconds
        self._tripped = False; self._last_check = None; self._cached_remaining = None
    async def ok(self) -> bool:
        # if tripped -> False. Else, if cache stale, query usage once (shared);
        # set self._tripped when remaining_mb < self._min. Return not tripped.
    @property
    def tripped(self) -> bool: return self._tripped
```

Throttle: only re-query usage if `loop.time() - self._last_check > self._ttl` (do NOT use Date.now()/wall clock; use `asyncio.get_event_loop().time()`). `fetch_ticks_one` checks `await gate.ok()` at each ticker start and inside the day loop instead of the old per-call `check_quota`.

2. `run_batch` — bounded-concurrency orchestrator shared by all fetch subcommands:

```python
async def run_batch(codes, concurrency, per_ticker) -> list[TickerResult]:
    sem = asyncio.Semaphore(concurrency)
    async def guarded(code):
        async with sem:
            return await per_ticker(code)
    results = await asyncio.gather(*(guarded(c) for c in codes), return_exceptions=True)
    # map exceptions -> TickerResult(code, status="failed", error=str(e))
```

3. Add `format_batch_report(results) -> str` (summary line + per-ticker resume hints for `partial`/`failed`). Resume hints are **per-ticker** (`--code X --start <last_date+1>`) because each ticker's `last_date` differs.

**Tests:** Required — concurrency/quota are core logic.

```python
async def test_run_batch_isolates_failures():     # one raising ticker -> status=failed, others complete
async def test_run_batch_respects_concurrency():  # never more than N in flight (track a live counter)
async def test_quota_gate_trips_and_stays():       # remaining<min -> tripped True, ok() stays False
async def test_quota_gate_throttles_usage_query(): # within ttl, usage queried once across many ok() calls
```

Use a fake client (records `get_usage` calls, returns scripted `remaining_mb`). No gateway.

**Verification:**

Run: `uv run pytest tests/test_batch_fetch.py -v && uv run ruff check .`
Expected: new tests pass, ruff clean.

**Commit:**
```bash
git add src/shioaji_server/data/fetch.py tests/test_batch_fetch.py
git commit -m "feat: shared QuotaGate + parallel run_batch for batch ticker fetch"
```

---

### Task 5: `data/cli.py` argparse front-end + console_scripts entry

**Files:**
- Create: `src/shioaji_server/data/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`)
- Modify: `src/shioaji_server/data/inspect.py` (drop leftover `main()` if still present)
- Test: `tests/test_data_cli.py`

**Implementation:**

1. `build_parser()` — top-level parser with global `--catalog` (default `./catalog`) and `--gateway-url` (default `http://localhost:8000`) on a parent parser; 4 subparsers inheriting via `parents=[common]`:
   - `fetch-bars`, `fetch-ticks`, `instrument-def`: a **mutually-exclusive required group** `--code` XOR `--codes` XOR `--codes-file`; plus `--start`/`--end`/`--concurrency` (and `--min-remaining-mb` for ticks).
   - `inspect`: no ticker args.
2. `resolve_codes(args) -> list[str]` — `[args.code]` if single, else split `--codes` on comma / read `--codes-file` lines (strip blanks/comments).
3. `main(argv=None) -> int`:
   - Parse; pre-flight `GET {gateway_url}/api/health`; on `httpx.ConnectError` print the friendly message and `return 1`.
   - Build one `ShioajiClient`; dispatch:
     - `inspect` → `inspect_catalog(catalog_dir)`; `return 0`.
     - fetch commands → build the matching `per_ticker` closure (bars/ticks/instrument-def), `results = asyncio.run(run_batch(codes, concurrency, per_ticker))`, print `format_batch_report`, `return 0` if all complete else `2`.
   - Wrap to always `await client.close()`.
4. `pyproject.toml` `[project.scripts]`:
```toml
[project.scripts]
shioaji-server = "shioaji_server.__main__:main"
shioaji-data = "shioaji_server.data.cli:main"
```
5. Remove any leftover `main()`/argparse in `inspect.py` (the CLI now owns inspection entry).

**Tests:** Required — public API (the CLI surface).

```python
def test_parser_has_four_subcommands():            # fetch-bars/fetch-ticks/instrument-def/inspect
def test_code_and_codes_mutually_exclusive():      # both -> SystemExit (argparse)
def test_resolve_codes_from_file(tmp_path):        # --codes-file -> list, blanks/comments skipped
def test_dispatch_routes_fetch_ticks(monkeypatch): # monkeypatch run_batch, assert called with parsed codes/concurrency
def test_main_returns_1_when_gateway_down(monkeypatch):  # health check raises ConnectError -> exit 1
```

All offline (monkeypatch `run_batch` / health check; no gateway).

**Verification:**

Run: `uv run pip install -e . >/dev/null 2>&1; uv run shioaji-data --help`
Expected: help lists the 4 subcommands.
Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, ruff clean.

**Commit:**
```bash
git add src/shioaji_server/data/cli.py src/shioaji_server/data/inspect.py pyproject.toml tests/test_data_cli.py
git commit -m "feat: shioaji-data CLI (argparse, 4 subcommands, console_scripts entry)"
```

---

### Task 6: End-to-end verification + README usage

**Files:**
- Modify: `README.md` (add a `shioaji-data` usage section)

**Implementation:**

1. Full gate: `uv run ruff check .` and `uv run pytest -q` both green.
2. Live smoke (gateway up + logged in): `uv run shioaji-data inspect` prints the equity/bar summary; `uv run shioaji-data instrument-def --code 0050` writes/refreshes the def (idempotent). If no gateway available, note it and rely on the offline tests.
3. README: add a short section — install (`uv pip install -e .`), the 4 commands, a batch example (`shioaji-data fetch-ticks --codes 0050,00631L --concurrency 4`), and that the one-shot maintenance chain stays at `uv run python -m scripts.maintenance.<x>`.

**Verification:**

Run: `uv run ruff check . && uv run pytest -q`
Expected: clean + all pass.
Run: `uv run shioaji-data inspect` (if gateway up)
Expected: equity defs + bar summary table.

**Commit:**
```bash
git add README.md
git commit -m "docs: README usage for shioaji-data CLI"
```

---

## Notes for the implementer

- **Tests philosophy** (see `pragmatic-testing`): public API (CLI parser/dispatch) and core logic (`QuotaGate`, `run_batch`, TradeId helper) get tests; the thin per-ticker drivers are exercised indirectly + need a live gateway, so don't mock the whole gateway — keep batch/quota tests on fakes.
- **Behaviour parity:** `fetch_bars_one`/`fetch_ticks_one` must preserve the existing scripts' semantics (quota-aware stop, `--start` resume, instrument-def side-write, `volume<=0` tick filtering). Diff against the pre-move `fetch_single*.py` if unsure.
- **No wall clock in async code:** `QuotaGate` throttling uses `asyncio.get_event_loop().time()`, not `time.time()`/`datetime.now()`.
- **Maintenance chain untouched** except the one import repoint in Task 2; its two tests must stay green throughout.
