# Bars Idempotent Resume + Gateway Liveness Probe — Implementation Plan

> **For Claude:** Use superpowers:executing-plans, superpowers:subagent-driven-development, or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Make `fetch-bars` idempotently resumable from the catalog itself, and make the CLI pre-flight detect a stale (fake-alive) gateway session with a real kbar probe.

**Architecture:** Change the bars chunk loop from "skip failed month, continue" to "retry same chunk 3×, then stop" — this guarantees the catalog holds a *contiguous prefix* `[start, last_bar_date]` with no error holes, so "last bar date in catalog + 1" is a mathematically safe auto-resume point. Cap `--end` to yesterday before 15:00 Taipei (bars AND ticks) so an intraday-truncated final day can never enter the catalog and poison that invariant. Pre-flight gains a real `2330` kbar probe because `/api/health` lies when the backend Solace session silently dies.

**Tech Stack:** Python 3.12, asyncio + httpx, polars (lazy scan), NautilusTrader `ParquetDataCatalog`, pytest (fully offline via monkeypatch / `httpx.MockTransport`). ALWAYS run Python via `uv run`.

**Design doc:** `docs/plans/2026-06-10-bars-idempotency-gateway-probe-design.md` (approved).

**Repo orientation (zero-context primer):**

- `src/shioaji_server/data/bars.py` — bar download engine: `kbars_to_bars`, `month_ranges`, `probe_kbar_availability`, `fetch_stock_bars`. Currently returns a bare `int` and **skips a whole month chunk after 1–2 errors** (`continue`), breaking only after 3 *consecutive* errors.
- `src/shioaji_server/data/fetch.py` — per-ticker drivers returning `TickerResult(code, status, n_written, last_date, error)` where `status ∈ {"complete","partial","no_data","failed"}`; `run_batch` orchestration; `format_batch_report`. `fetch_bars_one` currently *always* reports `"complete"`.
- `src/shioaji_server/data/cli.py` — argparse front-end. `_check_gateway` currently only does a bare `GET /api/health` without even checking the status code. Exit codes: 0 all complete, 2 partial/failed present, 1 gateway down.
- Catalog layout: bars live at `{catalog.path}/data/bar/{str(bar_type)}/` e.g. `data/bar/2330.SINOPAC-1-MINUTE-LAST-EXTERNAL/*.parquet`, column `ts_event` (int ns, true-UTC). `catalog.path` is a `str`. Note: TWSE session is 09:00–13:30 Taipei = 01:00–05:30 UTC, so the **UTC date of a bar equals its Taiwan trading date** — date arithmetic on UTC timestamps is safe here.
- Tests are fully offline. Style reference: `tests/test_batch_fetch.py` (fake clients, scripted responses), `tests/test_data_cli.py` (parser + dispatch with monkeypatch).
- Per `~/.claude/CLAUDE.md`: any function doing math gets the Definition/Formula/Domain/Returns docstring; logging in English; NEVER call `python` directly.

---

### Task 0: Commit the approved design doc (if not yet committed)

**Files:**
- `docs/plans/2026-06-10-bars-idempotency-gateway-probe-design.md` (already written)

**Verification:** `git log --oneline -1` shows the design commit; `git status` clean.

**Commit:**
```bash
git add docs/plans/2026-06-10-bars-idempotency-gateway-probe-design.md
git commit -m "docs: design for bars idempotent resume and gateway liveness probe"
```

---

### Task 1: Retry-then-break chunk loop (`BarsFetchOutcome`)

**Files:**
- Modify: `src/shioaji_server/data/bars.py`
- Test: `tests/test_bars_resume.py` (create)

**Implementation:**

Replace `MAX_CONSECUTIVE_ERRORS = 3` with `MAX_CHUNK_ATTEMPTS = 3` (the old cross-chunk counter is what produced silent month holes; `fetch.py` has its own unrelated `MAX_CONSECUTIVE_ERRORS` for ticks — leave that alone). Add:

```python
@dataclass
class BarsFetchOutcome:
    """Outcome of one fetch_stock_bars run.

    Definition: Truth-carrying result of the monthly-chunk download loop.
    Domain:     ``truncated=True`` means the loop stopped at a chunk that
                failed MAX_CHUNK_ATTEMPTS times; everything BEFORE that chunk
                was fetched (or genuinely empty) — the catalog prefix is
                contiguous. ``last_bar_date`` is the UTC date of the last bar
                actually written (None if nothing was written).
    Returns:    n_bars written this run, last_bar_date, truncated flag.
    """
    n_bars: int
    last_bar_date: date | None
    truncated: bool
```

Rewrite the `fetch_stock_bars` loop body — retry the SAME chunk, never skip forward past a failure:

```python
async def fetch_stock_bars(...) -> BarsFetchOutcome:
    total_bars = 0
    last_bar_date: date | None = None
    for chunk_start, chunk_end in month_ranges(start, end):
        kbar_resp = None
        for attempt in range(1, MAX_CHUNK_ATTEMPTS + 1):
            try:
                kbar_resp = await client.get_kbars(code, start_str, end_str)
                break
            except Exception as e:
                print(f"    ERROR {start_str}→{end_str} "
                      f"(attempt {attempt}/{MAX_CHUNK_ATTEMPTS}): {e!r}")
        if kbar_resp is None:
            print(f"    TRUNCATED at {start_str}: {MAX_CHUNK_ATTEMPTS} attempts "
                  f"failed — stopping so the catalog prefix stays contiguous")
            return BarsFetchOutcome(total_bars, last_bar_date, truncated=True)

        bars = kbars_to_bars(kbar_resp, bar_type)
        if bars:                       # empty month (pre-listing/halt) is NOT an error
            bars.sort(key=lambda b: b.ts_init)
            catalog.write_data(bars)
            total_bars += len(bars)
            last_bar_date = datetime.fromtimestamp(
                bars[-1].ts_init / 1e9, tz=timezone.utc
            ).date()
    return BarsFetchOutcome(total_bars, last_bar_date, truncated=False)
```

Update the module docstring: the contiguous-prefix invariant is the load-bearing contract — document it.

**Tests** (Required — this invariant is the foundation of Task 3):

`tests/test_bars_resume.py`, fake client records every requested `(start, end)` range and scripts per-range behavior:

```python
class ScriptedKbarClient:
    """get_kbars driven by a {start_str: behavior} script; records calls."""
```

1. `test_truncated_chunk_stops_loop_no_month_skip` — chunk 1 OK, chunk 2 raises on all 3 attempts, 3-month range: outcome `.truncated is True`, chunk 3 **never requested**, `n_bars` == chunk-1 count.
2. `test_transient_error_retries_same_chunk` — chunk 1 fails twice then succeeds on attempt 3: `.truncated is False`, chunk 1 requested 3×, all months fetched.
3. `test_empty_month_is_not_truncation` — chunk 2 returns empty `ts`: loop continues to chunk 3, `.truncated is False`.

Use a fake in-memory catalog (`write_data` appends to a list) — no NT catalog needed here.

**Verification:**

Run: `uv run pytest tests/test_bars_resume.py -v`
Expected: 3 passing.

**Commit:**
```bash
git add src/shioaji_server/data/bars.py tests/test_bars_resume.py
git commit -m "fix: retry-then-break bars chunk loop to guarantee contiguous catalog prefix"
```

---

### Task 2: `last_bar_date_in_catalog`

**Files:**
- Modify: `src/shioaji_server/data/bars.py`
- Test: `tests/test_bars_resume.py` (extend)

**Implementation:**

```python
def last_bar_date_in_catalog(
    catalog: ParquetDataCatalog, bar_type: BarType
) -> date | None:
    """
    Definition: UTC date of the newest bar already persisted for *bar_type*.
    Formula:    max(ts_event) over data/bar/{bar_type}/*.parquet, ns→UTC date.
    Domain:     Lazy polars scan — never loads full bar data through NT. The
                returned date is only a safe resume point because
                fetch_stock_bars guarantees a contiguous error-free prefix.
                TWSE bars: UTC date == Taiwan trading date (session 01:00–05:30
                UTC), so day arithmetic needs no tz conversion.
    Returns:    date of last bar, or None when the instrument has no bar data.
    """
    bar_dir = Path(catalog.path) / "data" / "bar" / str(bar_type)
    if not bar_dir.exists() or not any(bar_dir.glob("*.parquet")):
        return None
    max_ns = (
        pl.scan_parquet(str(bar_dir / "*.parquet"))
        .select(pl.col("ts_event").max())
        .collect()
        .item()
    )
    if max_ns is None:
        return None
    return datetime.fromtimestamp(max_ns / 1e9, tz=timezone.utc).date()
```

Imports to add in `bars.py`: `polars as pl`, `Path`, `datetime`/`timezone`.

**Tests** (Required — public API of the resume mechanism; use the REAL `ParquetDataCatalog` against `tmp_path` to lock in the `str(bar_type)` directory-naming assumption):

4. `test_last_bar_date_round_trip` — build 2 real `Bar`s (copy construction style from `bars.kbars_to_bars` usage; ts = known ns), `catalog.write_data(bars)`, assert returned date matches the newest bar.
5. `test_last_bar_date_empty_catalog_is_none` — fresh tmp catalog → `None`.

**Verification:**

Run: `uv run pytest tests/test_bars_resume.py -v`
Expected: 5 passing.

**Commit:**
```bash
git add src/shioaji_server/data/bars.py tests/test_bars_resume.py
git commit -m "feat: read last persisted bar date from catalog for resume"
```

---

### Task 3: Auto-resume in `fetch_bars_one` + honest partial status

**Files:**
- Modify: `src/shioaji_server/data/fetch.py`
- Test: `tests/test_bars_resume.py` (extend)

**Implementation:**

Rewire `fetch_bars_one` — resume check FIRST (before probe and instrument write, so an up-to-date ticker costs zero API calls and zero duplicate instrument-def appends):

```python
async def fetch_bars_one(client, gateway_url, code, start, end, catalog) -> TickerResult:
    instrument_id = InstrumentId(Symbol(code), VENUE)
    bar_type = BarType(instrument_id, BAR_SPEC)

    last = last_bar_date_in_catalog(catalog, bar_type)
    if last is not None:
        resume_start = last + timedelta(days=1)
        if resume_start > end:
            print(f"{code}: up to date (last bar {last}) — skipping")
            return TickerResult(code=code, status="complete", n_written=0, last_date=last)
        if resume_start > start:
            print(f"{code}: resuming from {resume_start} (catalog has through {last})")
            start = resume_start

    # ... existing probe → instrument write → fetch flow ...
    outcome = await fetch_stock_bars(client, code, bar_type, start, end, catalog)
    if outcome.truncated:
        print(f"Done: {outcome.n_bars} bars written (TRUNCATED — re-run to resume)")
        return TickerResult(code=code, status="partial",
                            n_written=outcome.n_bars, last_date=outcome.last_bar_date)
    print(f"Done: {outcome.n_bars} bars written")
    return TickerResult(code=code, status="complete",
                        n_written=outcome.n_bars, last_date=end)
```

Import `last_bar_date_in_catalog` from `.bars`.

`format_batch_report` gains `auto_resume: bool = False`; when True the partial hint becomes "re-run the same command (auto-resumes from catalog)" instead of the `--start` reconstruction (ticks keep the old hints — their resume is still `--start`-driven). `cli.main` passes `auto_resume=(args.command == "fetch-bars")`. Update the `format_batch_report` docstring Domain accordingly.

**Tests** (Required — this IS the idempotency contract):

6. `test_fetch_bars_one_skips_when_up_to_date` — real tmp catalog pre-seeded with a bar at `end`; scripted client **asserts get_kbars is never called** (probe included); result `complete`, `n_written == 0`.
7. `test_fetch_bars_one_resumes_from_catalog` — catalog holds bars through mid-range; assert the first fetched chunk starts at `last+1` (recorded ranges), not the original `--start`.
8. `test_fetch_bars_one_reports_partial_on_truncation` — scripted permanent failure on chunk 2: status `"partial"`, `last_date` == last written bar's date.
9. `test_format_batch_report_auto_resume_hint` — partial result + `auto_resume=True` → hint says re-run, contains no `--start`.

**Verification:**

Run: `uv run pytest tests/test_bars_resume.py tests/test_batch_fetch.py tests/test_data_cli.py tests/test_cli_qa_edge_cases.py -v`
Expected: all pass (existing suites prove no regression in ticks/report paths).

**Commit:**
```bash
git add src/shioaji_server/data/fetch.py src/shioaji_server/data/cli.py tests/test_bars_resume.py
git commit -m "feat: catalog-aware auto-resume and honest partial status for fetch-bars"
```

---

### Task 4: `_effective_end` intraday cap (bars + ticks)

**Files:**
- Modify: `src/shioaji_server/data/cli.py`
- Test: `tests/test_data_cli.py` (extend)

**Implementation:**

```python
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_DATA_FINAL_HOUR = 15  # 13:30 close + TWSE data-finalization buffer


def _effective_end(end: date, now_tw: datetime | None = None) -> date:
    """
    Definition: Clamp the fetch end date so an intraday-incomplete trading day
        can never be persisted.
    Formula:    end' = min(end, today_tw - 1d)  if now_tw.hour < 15 and
                end >= today_tw, else end.
    Domain:     now_tw is Asia/Taipei (injected in tests; defaults to wall
        clock). Applies to bars AND ticks — both resume day-granular from
        last_date + 1, so a partially-fetched final day would be skipped
        forever on the next run and is invisible to inspect's day-level gap
        detection. Explicit --end is clamped too: no escape hatch.
    Returns:    The capped end date (a note is printed when capping occurs).
    """
    now_tw = now_tw or datetime.now(TAIPEI)
    today_tw = now_tw.date()
    if end >= today_tw and now_tw.hour < MARKET_DATA_FINAL_HOUR:
        capped = today_tw - timedelta(days=1)
        print(f"note: capping --end {end} → {capped} "
              f"(intraday bars incomplete before 15:00 Taipei)")
        return capped
    return end
```

In `_build_per_ticker`, both `fetch-bars` and `fetch-ticks` branches: `end = _effective_end(_parse_date(args.end) if args.end else date.today())`.

**Tests** (Required — time logic; inject `now_tw`, never the real clock):

- end=today @ 10:00 TW → yesterday (capped)
- end=today @ 15:01 TW → today (uncapped)
- end=yesterday @ 10:00 TW → unchanged (historical range untouched)
- end=tomorrow @ 10:00 TW → yesterday (`>=` catches future dates too)

**Verification:**

Run: `uv run pytest tests/test_data_cli.py -v`
Expected: all pass.

**Commit:**
```bash
git add src/shioaji_server/data/cli.py tests/test_data_cli.py
git commit -m "feat: cap fetch end date before 15:00 Taipei to protect resume invariant"
```

---

### Task 5: Gateway real-liveness probe

**Files:**
- Modify: `src/shioaji_server/data/cli.py`
- Test: `tests/test_data_cli.py` (extend)

**Implementation:**

```python
PROBE_CODE = "2330"          # TSMC: always has data → near-zero false negatives
PROBE_WINDOW_DAYS = 14       # survives the longest TW holiday break (CNY ~10d)


class GatewayStaleError(RuntimeError):
    """Health endpoint OK but a real kbar probe returned no data."""


async def _check_gateway(
    gateway_url: str, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Two-stage pre-flight: HTTP health, then a real 2330 kbar probe.

    /api/health only reflects the login flag — a silently-dead Solace session
    still reports healthy (known gotcha). Only an actual market-data request
    proves the session is alive. Raises ConnectError (unreachable),
    HTTPStatusError (health/kbars non-2xx), or GatewayStaleError (empty probe).
    """
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as probe:
        health = await probe.get(f"{gateway_url}/api/health")
        health.raise_for_status()
        today = date.today()
        kb = await probe.get(
            f"{gateway_url}/api/market/kbars",
            params={
                "code": PROBE_CODE,
                "start": (today - timedelta(days=PROBE_WINDOW_DAYS)).isoformat(),
                "end": today.isoformat(),
                "market": "stock",
            },
        )
        kb.raise_for_status()
        if not kb.json().get("ts"):
            raise GatewayStaleError(
                f"health OK but {PROBE_CODE} kbar probe returned no data "
                f"— Solace session likely stale; re-login the gateway"
            )
```

`main`'s pre-flight grows a second except arm (keep messages distinct — they direct different operator actions):

```python
    except httpx.ConnectError:
        print(f"gateway not reachable at {args.gateway_url} — container up & "
              f"logged in? (curl {args.gateway_url}/api/health)")
        return 1
    except (GatewayStaleError, httpx.HTTPStatusError) as e:
        print(f"gateway session stale or unhealthy at {args.gateway_url}: {e}")
        return 1
```

`inspect` path is untouched (returns before the pre-flight — already the case).

**Tests** (Required — this is the gotcha fix; use `httpx.MockTransport` via the `transport` parameter):

- health 200 + kbars `{"ts":[...]}` non-empty → no raise
- health 200 + kbars `{"ts":[]}` → `GatewayStaleError`, and `cli.main` exit 1 with "stale" in output (monkeypatch `_check_gateway` to raise for the main-level test, matching existing `test_main_returns_1_when_gateway_down` style)
- health 500 → `HTTPStatusError` → exit 1
- confirm existing `test_main_returns_1_when_gateway_down` still passes (ConnectError arm unchanged)

**Verification:**

Run: `uv run pytest tests/test_data_cli.py tests/test_cli_qa_edge_cases.py -v`
Expected: all pass.

**Commit:**
```bash
git add src/shioaji_server/data/cli.py tests/test_data_cli.py
git commit -m "fix: detect stale gateway session with real 2330 kbar pre-flight probe"
```

---

### Task 6: README + full-suite verification

**Files:**
- Modify: `README.md` (the `shioaji-data` usage section added in the CLI work)
- Modify: `src/shioaji_server/data/cli.py` / `fetch.py` module docstrings if stale

**Implementation:**

Document (Traditional Chinese OK in Markdown):
- fetch-bars 重跑即續傳:已完成的檔秒過、partial 直接重跑同一條命令;不再需要手動刪 bar 目錄
- 盤中(台北 15:00 前)`--end` 會被 cap 到昨日(bars/ticks 皆然)及原因
- 啟動探活:health + 2330 kbar 探針;兩種 exit 1 訊息的含義(不可達 vs session 假活)

**Verification:**

Run: `uv run pytest tests/ -v`
Expected: full suite green — paste the tail of the output as evidence.

Optional live smoke (gateway running): `uv run shioaji-data fetch-bars --code 2330 --start 2026-06-01` twice — second run prints "up to date … skipping" and exits 0.

**Commit:**
```bash
git add README.md src/shioaji_server/data/cli.py src/shioaji_server/data/fetch.py
git commit -m "docs: document fetch-bars auto-resume, intraday cap, and liveness probe"
```
