# 00981A Top-300 Constituent Catalog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task, or superpowers:orchestrator-driven-development for a stateful, resumable multi-session pipeline.

**Goal:** Build a survivorship-correct point-in-time **top-300-by-market-cap** constituent universe for the active ETF 00981A (統一台股增長主動式 ETF) and download its members' 1-min bars into the existing `catalog/`, reusing the 0050 `fetch-bars`/`inspect`/resume pipeline.

**Architecture:** Two halves. (A) NEW universe construction — fetch official TWSE/TPEx daily close + MOPS shares/capital-events, reconstruct point-in-time daily market caps, rank the top-300 each trading day over 00981A's life [2025-05-27 → today], union into a code list + interval membership table. (B) REUSE — feed the union to the existing `shioaji-data fetch-bars` (start 2020-03-02) and a 0050-style resume cron.

**Tech Stack:** Python 3.12 / `uv` · polars or pandas (match existing `scripts/`) · httpx for official-data HTTP · existing `shioaji-data` CLI (`src/shioaji_server/data/`) · NautilusTrader ParquetDataCatalog.

**Design:** [`2026-06-16-00981a-constituents-catalog-design.md`](2026-06-16-00981a-constituents-catalog-design.md) (rationale, decisions, risks R1–R4).

**Conventions (CLAUDE.md — apply to every task):** `uv run` only; no `print` for results (use `logging`; persist numeric outputs as Parquet/CSV with explicit schemas); full type hints + PEP 604 unions; **math docstrings (Definition/Formula/Domain/Returns) on every function doing a math operation**; reproducible config (explicit constants/paths, no magic numbers); English commits (imperative, no AI footer). Centralize shared logic — do not duplicate the 0050 bar engine.

---

## File Structure

| File | Responsibility |
|---|---|
| `docs/reference/00981a-market-data-endpoints.md` (new) | Spike deliverable: verified TWSE/TPEx/MOPS endpoints, fields, rate-limits, volume estimate, GO/NO-GO. |
| `scripts/twse_tpex_market.py` (new) | Official-data client: current shares, historical daily close, MOPS capital events — with local caching + rate-limit + resume. Network I/O. |
| `scripts/universe_ranking.py` (new) | Pure logic (no network): reconstruct daily shares, market cap, daily top-N ranking, union + interval extraction. Math docstrings. Unit-tested. |
| `scripts/build_00981a_universe.py` (new) | Orchestrator CLI: wire client + ranking, filter to common stock, write universe files, R4 validation. |
| `tests/test_universe_ranking.py` (new) | Unit tests for `universe_ranking.py` pure functions (synthetic data, no network). |
| `universe/00981a_top300_constituents.txt` (new, generated) | Union code list (the `--codes-file`). |
| `universe/membership_00981a_top300.csv` (new, generated) | Interval membership table (0050 schema). |
| `scripts/resume_00981a_fetch.sh` (new) | Clone of `resume_0050_fetch.sh` pointed at the 00981A universe. |
| `docs/BACKLOG.md` (modify) | Record the new pipeline as a BL row. |

---

## Task 1: SPIKE — de-risk official-data sourcing (R1 + R2) ⛔ DECISION GATE

**Files:**
- Create: `docs/reference/00981a-market-data-endpoints.md`

**Why first:** The whole plan depends on two unverified assumptions: (R1) point-in-time shares-outstanding is reconstructable from official sources, and (R2) historical daily close for the full market is obtainable over the window. `openapi.twse.com.tw/v1` only serves previous-day/month data, so historical close must come from the official-site `STOCK_DAY` endpoint (per-stock-per-month). Verify both before building anything.

**Implementation:**

Pick 3 probe stocks: (a) a large-cap with NO capital event in the window (e.g. `2330`), (b) a stock with a KNOWN capital event in [2025-05-27, today] (e.g. a financial that did a 現增, or any stock that paid a 股票股利 — confirm via MOPS), (c) a TPEx large-cap (e.g. `8069` 元太 or `5483` 中美晶).

Verify and document, with a working `uv run python` probe for each (use `httpx`):
1. **Current shares-outstanding** — TWSE listed: `https://openapi.twse.com.tw/v1/opendata/t187ap03_L` (find the issued-shares / 實收資本額 field; record exact JSON key). TPEx equivalent on `https://www.tpex.org.tw/openapi/`. Record the field that gives 已發行普通股數 (or paid-in capital ÷ par to derive shares).
2. **Historical daily close** — `https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=XXXX` returns one stock's daily OHLCV for the month of `date`. Verify it covers 2025-05 → now for probe (a)/(b). Find + record the TPEx historical daily-close endpoint for probe (c).
3. **MOPS capital events** — locate the capital-change/股本形成 dataset (dates + share deltas) for probe (b); record the access method (MOPS form POST or any JSON mirror).

Then: reconstruct probe (b)'s shares across its in-window event by rolling its current shares backward through the event delta; sanity-check the pre-event value against an independent number (e.g. the company's pre-event 已發行股數 from a press release or 除權息 reference). Estimate full-market request volume (≈ #TWSE+TPEx commons × #months) and a polite rate-limit.

**Verification:**

Run each probe with `uv run python -c "..."` (or a scratch `uv run python scripts/_spike_00981a.py` you delete after). Each of the 3 endpoint families returns real data for the probe stocks; the reconstructed pre-event shares for probe (b) matches the independent value within rounding.

**Deliverable / Gate:** Write `docs/reference/00981a-market-data-endpoints.md` (`Updated: 2026-06-16`) with: exact URLs + params + JSON field names for the 3 families, the volume + rate-limit estimate, and a **GO / NO-GO verdict**. If NO-GO (e.g. MOPS history not machine-accessible, or close history doesn't reach 2025-05), **STOP and surface to the user** — do not proceed to Task 2. The design's R1 fallback (current-shares approximation) is only adopted on explicit user approval.

**Tests:** None (exploratory spike).

**Commit:**
```bash
git add docs/reference/00981a-market-data-endpoints.md
git commit -m "Spike and document official market-data endpoints for 00981A universe"
```

---

## Task 2: Official-data client with caching (`scripts/twse_tpex_market.py`)

**Files:**
- Create: `scripts/twse_tpex_market.py`

**Implementation:**

Implement exactly the endpoints Task 1 verified (cite the endpoint-reference doc). All functions are catalog-style idempotent — cache to `cache/twse_tpex/` and skip already-cached pulls. Use `logging`, not `print`. Persist intermediate pulls as Parquet with explicit columns.

```python
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import httpx
import polars as pl

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache/twse_tpex")
RATE_LIMIT_SECONDS = 0.6  # polite delay between official-site requests (tune per Task 1)


def fetch_current_shares(cache_dir: Path = CACHE_DIR) -> pl.DataFrame:
    """
    Definition: Current issued common-share count per listed TWSE+TPEx code.
    Formula:    shares = issued_common_shares (or paid_in_capital / par_value=10).
    Domain:     One row per common stock; ETFs/warrants/preferred excluded downstream.
    Returns:    DataFrame[code: str, name: str, shares: int]. Cached to parquet.
    """
    # GET openapi.twse.com.tw/v1/opendata/t187ap03_L (+ TPEx equiv); parse the
    # issued-shares field documented in Task 1; cache to cache_dir/current_shares.parquet.
    ...


def fetch_daily_close(
    codes: list[str], start: date, end: date, cache_dir: Path = CACHE_DIR
) -> pl.DataFrame:
    """
    Definition: Daily official closing price per code over [start, end].
    Formula:    close(code, d) from www.twse.com.tw STOCK_DAY (TWSE) / TPEx daily report.
    Domain:     Trading days only; per-code-per-month requests, rate-limited + resumable.
    Returns:    DataFrame[code: str, date: date, close: float]. Cached per code-month.
    """
    # Loop code × month; skip cached cache_dir/close/{code}/{YYYYMM}.parquet;
    # sleep RATE_LIMIT_SECONDS; on transient HTTP error retry-then-skip-month (log).
    ...


def fetch_capital_events(
    codes: list[str], start: date, end: date, cache_dir: Path = CACHE_DIR
) -> pl.DataFrame:
    """
    Definition: Share-count change events (capital increase/decrease, stock dividend).
    Formula:    each event = (code, effective_date, shares_delta).
    Domain:     Events with effective_date in [start, end]; empty for unchanged codes.
    Returns:    DataFrame[code: str, date: date, shares_delta: int, kind: str].
    """
    # MOPS capital-formation history per Task 1; cache to cache_dir/capital_events.parquet.
    ...
```

**Verification:**

Run: `uv run python -c "from scripts.twse_tpex_market import fetch_current_shares; df = fetch_current_shares(); import logging; logging.warning('rows=%d', df.height); assert df.height > 1500"`
Expected: ≥ ~1500 common-stock rows; a second run reads from cache (near-instant, logs cache hits).

**Tests:** Deferred — network/cache I/O, exploratory. The pure logic that consumes these is tested in Task 3.

**Commit:**
```bash
git add scripts/twse_tpex_market.py
git commit -m "Add cached TWSE/TPEx/MOPS market-data client"
```

---

## Task 3: Pure reconstruction + ranking + union logic (`scripts/universe_ranking.py`)

**Files:**
- Create: `scripts/universe_ranking.py`
- Test: `tests/test_universe_ranking.py`

**Implementation:**

Pure functions, no network — all inputs are DataFrames. Math docstrings mandatory.

```python
from __future__ import annotations

from datetime import date

import polars as pl


def reconstruct_daily_shares(
    current_shares: pl.DataFrame, events: pl.DataFrame, trading_days: list[date]
) -> pl.DataFrame:
    """
    Definition: Point-in-time issued-share count per code per trading day.
    Formula:    shares(code, d) = current_shares(code) - sum(delta_e
                for events e of code with effective_date > d).
                (roll backward from today's anchor through each in-window delta)
    Domain:     trading_days within the membership window; codes with no event are
                constant = current_shares; assumes events are complete (Task 1 R1).
    Returns:    DataFrame[code, date, shares: int].
    """
    ...


def compute_market_cap(close: pl.DataFrame, shares: pl.DataFrame) -> pl.DataFrame:
    """
    Definition: Point-in-time market capitalisation per code per trading day.
    Formula:    mktcap(code, d) = close(code, d) * shares(code, d).
    Domain:     Inner-join on (code, date); rows missing either factor are dropped.
    Returns:    DataFrame[code, date, mktcap: float].
    """
    ...


def daily_top_n(mktcap: pl.DataFrame, n: int = 300) -> pl.DataFrame:
    """
    Definition: The n highest-market-cap codes on each trading day.
    Formula:    top_n(d) = argsort_desc(mktcap(*, d))[:n].
    Domain:     n>0; days with < n codes return all available (logged).
    Returns:    DataFrame[date, code] (n rows per date).
    """
    ...


def build_union_and_membership(
    daily_top: pl.DataFrame, names: dict[str, str]
) -> tuple[list[str], pl.DataFrame]:
    """
    Definition: Survivorship-correct union of all codes ever in the daily top-N,
                plus per-code [effective_from, effective_to) membership intervals.
    Formula:    union = distinct(daily_top.code); intervals = maximal runs of
                consecutive trading days a code is present (gap > 0 splits a run).
    Domain:     daily_top sorted by date; effective_to is exclusive, blank=still in.
    Returns:    (sorted union codes, DataFrame[code,name,effective_from,
                effective_to,confidence,note]) matching the 0050 membership schema.
    """
    ...
```

**Tests (required — core logic):**

```python
from datetime import date
import polars as pl
from scripts.universe_ranking import (
    reconstruct_daily_shares, compute_market_cap, daily_top_n, build_union_and_membership,
)

def test_reconstruct_applies_event_backward():
    days = [date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 4)]
    current = pl.DataFrame({"code": ["A"], "name": ["a"], "shares": [1100]})
    events = pl.DataFrame({"code": ["A"], "date": [date(2025, 6, 4)],
                           "shares_delta": [100], "kind": ["stock_dividend"]})
    out = reconstruct_daily_shares(current, events, days)
    # before the 06-04 event: 1100 - 100 = 1000; on/after: 1100
    got = {r["date"]: r["shares"] for r in out.filter(pl.col("code") == "A").to_dicts()}
    assert got[date(2025, 6, 2)] == 1000
    assert got[date(2025, 6, 3)] == 1000
    assert got[date(2025, 6, 4)] == 1100

def test_top_n_and_union_intervals():
    # B is top-2 only on day 1+2, drops day 3 -> interval [d1, d3); C enters day 3.
    mc = pl.DataFrame({
        "date": [date(2025,6,2)]*3 + [date(2025,6,3)]*3 + [date(2025,6,4)]*3,
        "code": ["A","B","C"]*3,
        "mktcap": [9,8,1, 9,8,1, 9,1,8],
    })
    top = daily_top_n(mc, n=2)
    union, mem = build_union_and_membership(top, {"A":"a","B":"b","C":"c"})
    assert set(union) == {"A","B","C"}
    b = mem.filter(pl.col("code") == "B").to_dicts()[0]
    assert b["effective_from"] == date(2025,6,2) and b["effective_to"] == date(2025,6,4)
```

**Verification:**

Run: `uv run pytest tests/test_universe_ranking.py -v`
Expected: all tests pass.

**Commit:**
```bash
git add scripts/universe_ranking.py tests/test_universe_ranking.py
git commit -m "Add point-in-time market-cap ranking and membership-union logic"
```

---

## Task 4: Orchestrator CLI (`scripts/build_00981a_universe.py`)

**Files:**
- Create: `scripts/build_00981a_universe.py`

**Implementation:**

Wire Task 2 (fetch) + Task 3 (compute). Explicit config constants (no magic numbers). Filter to **common stock only** (exclude ETF/warrant/preferred/DR/受益證券) using the security-type field from `t187ap03_L` / TPEx. Write the two `universe/` artifacts. Log a summary; never `print` results.

```python
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from scripts.twse_tpex_market import (
    fetch_capital_events, fetch_current_shares, fetch_daily_close,
)
from scripts.universe_ranking import (
    build_union_and_membership, compute_market_cap, daily_top_n, reconstruct_daily_shares,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("build_00981a_universe")

MEMBERSHIP_START = date(2025, 5, 27)   # 00981A listing date
TOP_N = 300
OUT_CODES = Path("universe/00981a_top300_constituents.txt")
OUT_MEMBERSHIP = Path("universe/membership_00981a_top300.csv")


def main(end: date) -> None:
    shares0 = fetch_current_shares()
    commons = shares0.filter(...)            # security_type == common (per Task 1 field)
    codes = commons["code"].to_list()
    close = fetch_daily_close(codes, MEMBERSHIP_START, end)
    trading_days = sorted(close["date"].unique().to_list())  # calendar = dates present in close
    events = fetch_capital_events(codes, MEMBERSHIP_START, end)
    shares = reconstruct_daily_shares(commons, events, trading_days)
    union, membership = build_union_and_membership(
        daily_top_n(compute_market_cap(close, shares), n=TOP_N),
        names=dict(zip(commons["code"], commons["name"])),
    )
    OUT_CODES.write_text(
        "# 00981A (統一台股增長主動式 ETF) point-in-time top-300 market-cap UNION\n"
        f"# Membership window: {MEMBERSHIP_START}..{end}; daily rebalance; commons only.\n"
        + "\n".join(union) + "\n"
    )
    membership.write_csv(OUT_MEMBERSHIP)
    logger.info("union=%d codes, rebalance_days=%d", len(union), len(trading_days))


if __name__ == "__main__":
    main(end=date.today())
```

**R4 validation (subset check):** After writing, load 00981A's current disclosed holdings (issuer/官方 daily PCF, or a user-supplied snapshot file `universe/00981a_holdings_snapshot.txt`) and assert every holding ∈ `union`; log any misses (a miss means the top-300 cut or filter needs review — surface, do not silently widen).

**Verification:**

Run: `uv run python -m scripts.build_00981a_universe`
Expected: `universe/00981a_top300_constituents.txt` (~330–380 codes) + `universe/membership_00981a_top300.csv` written; log shows union size + rebalance-day count; R4 subset check passes (or logs explicit misses for review).

**Tests:** Covered by Task 3 unit tests for the pure logic; orchestration is integration-verified by the run above.

**Commit:**
```bash
git add scripts/build_00981a_universe.py universe/00981a_top300_constituents.txt universe/membership_00981a_top300.csv
git commit -m "Build point-in-time top-300 universe for 00981A"
```

---

## Task 5: Resume cron (`scripts/resume_00981a_fetch.sh`)

**Files:**
- Create: `scripts/resume_00981a_fetch.sh`

**Implementation:**

Copy `scripts/resume_0050_fetch.sh` verbatim, then change only: `CODES="$REPO/universe/00981a_top300_constituents.txt"`, `LOGDIR="$HOME/.shioaji-server/logs/00981a_fetch"`, and the log banners (`0050` → `00981A`). Keep `--start 2020-03-02`, `--concurrency 4`, the quota probe, and the `--catalog "$CATALOG"` (same shared `catalog/`) unchanged. `chmod +x`.

**Verification:**

Run: `bash -n scripts/resume_00981a_fetch.sh`
Expected: syntax OK (exit 0). Do not run it live yet (Task 6 is the quota-limited operational run).

**Commit:**
```bash
git add scripts/resume_00981a_fetch.sh
git commit -m "Add 00981A constituent resume-fetch cron script"
```

---

## Task 6: Kick off the bar download + acceptance (operational, multi-day)

**Files:** none (operational) — then `docs/BACKLOG.md` (modify).

**Implementation:**

This is the quota-limited multi-day download (≈7 GB ≈ ~15 days at 500 MB/day). Confirm quota first, then start one idempotent run; schedule the cron for daily resume.

1. `curl -s http://localhost:8123/api/account/usage` → confirm `remaining_mb > 0`.
2. One foreground run (or via the cron): `uv run shioaji-data fetch-bars --codes-file universe/00981a_top300_constituents.txt --catalog ./catalog --start 2020-03-02 --concurrency 4`.
3. Add the cron (coordinate timing vs live-trading quota): `5 14 * * * /home/cy/Code/MT5/shioaji-server/scripts/resume_00981a_fetch.sh >/dev/null 2>&1` — **ask the user before editing crontab** (a real account's daily quota is shared with live trading).
4. Acceptance: `uv run shioaji-data inspect --catalog ./catalog` → each 00981A union code first≈2020-03 (or listing date), last≈yesterday; reconcile `no_data` against quota state; list genuinely-delisted codes.
5. Record a BL row in `docs/BACKLOG.md` (open, with the design + plan links) per `maintaining-project-docs`.

**Verification:** `inspect` coverage shows the union codes progressing to current; existing 0050/00631L coverage unchanged (no regression).

**Commit (the BACKLOG row only):**
```bash
git add docs/BACKLOG.md
git commit -m "Track 00981A constituent catalog download in backlog"
```

---

## Notes for the executor

- **Gate at Task 1.** If the spike returns NO-GO, stop and surface — the rest of the plan assumes official historical shares + close are obtainable.
- **Quota is shared with live trading.** Tasks 2 (official data, free) and 6 (Shioaji bars, quota) have very different cost profiles; only Task 6 burns the 500 MB/day Shioaji quota. Get user sign-off before the crontab edit.
- **Do not touch existing 0050/00631L catalog data** or the 0050 universe files. The new download writes into the same `catalog/` but only for new codes (idempotent resume).
- **Reuse, don't reimplement** the bar engine — Tasks 5–6 call the existing `shioaji-data` CLI unchanged.
