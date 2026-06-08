# Timezone Unification (§0 / S1) Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan.

**Goal:** Make every `ts_event` the gateway emits a true UTC nanosecond epoch, so the sinopac adapter's HTTP path, the WS path, and the download scripts all agree — eliminating the 8-hour skew.

**Architecture:** The gateway currently passes Shioaji's native `t.ts` (Taiwan-local wall-clock encoded as a UTC epoch, i.e. +8h ahead of true UTC) straight through on HTTP endpoints, while the WS path emits a `str(tick.datetime)` text that the sinopac Rust adapter correctly converts (−8h). Fix at the single source — the gateway HTTP handlers — by subtracting 8h, then back-correct the already-downloaded catalog.

**Tech Stack:** Python (FastAPI gateway), Shioaji SDK, NautilusTrader ParquetDataCatalog, polars.

**Background context (read first):**
- The skew is proven: a catalog tick file for 0050 shows first `ts_event`→UTC = 09:0x and last = 13:30, which are *Taiwan* market hours (TW 09:00–13:30), so the stored epoch is TW-local-as-UTC.
- HTTP ts source: `src/shioaji_server/routes/market_data.py` — `_fetch_ticks` / `_fetch_kbars` / `_fetch_snapshots` build `"ts": list(t.ts)` (and `k.ts`, `snapshot.ts`) with no tz conversion.
- WS ts source: `src/shioaji_server/ws/manager.py` `register_callbacks` callbacks emit `"timestamp": str(tick.datetime)` (a TW wall-clock string) — a *different* field, consumed by the sinopac Rust `parse_taiwan_timestamp` which subtracts 8h. **Changing HTTP ts must NOT touch this WS string.**
- Constant: 8 hours = `28_800_000_000_000` ns.

---

### Task 1: Confirm the skew and prove WS is independent (no code change)

**Files:**
- Read: `src/shioaji_server/routes/market_data.py`, `src/shioaji_server/ws/manager.py`

**Implementation:**

Verify the assumption before changing anything (this gates whether −8h is correct and whether it is safe):
1. Start gateway (`make up`), confirm `/api/health` `connected:true`.
2. Fetch one known trading day of ticks via HTTP and inspect the first `ts`:
   ```bash
   curl -s "http://localhost:8000/api/market/ticks?code=0050&date=2024-06-14&market=stock" \
     | python3 -c "import sys,json,datetime as d; ts=json.load(sys.stdin)['ts'][0]; \
       print('UTC', d.datetime.fromtimestamp(ts/1e9, d.timezone.utc))"
   ```
   Expected: prints ~09:00 UTC (NOT ~01:00) → confirms TW-as-UTC, so −8h is the correct fix.
3. Confirm the WS path uses a separate string field: grep `manager.py` for `str(tick.datetime)` — it must remain untouched.

**Verification:** HTTP ts decodes to TW market hours under UTC; WS uses `str(tick.datetime)`. Document findings in the commit message.

**Commit:** _(no code — findings only; proceed to Task 2)_

---

### Task 2: Subtract 8h in the gateway HTTP handlers

**Files:**
- Modify: `src/shioaji_server/routes/market_data.py`
- Test: `tests/test_timezone.py`

**Implementation:**

Add one module-level constant and apply it wherever a Shioaji `*.ts` is exposed (`_fetch_ticks`, `_fetch_kbars`, `_fetch_snapshots`). Convert TW-as-UTC → true UTC:

```python
TW_UTC_OFFSET_NS = 28_800_000_000_000  # Shioaji ts is TW wall-clock as UTC epoch

def _to_utc_ns(ts_list: list[int]) -> list[int]:
    """Convert Shioaji TW-local-as-UTC nanosecond epochs to true UTC."""
    return [t - TW_UTC_OFFSET_NS for t in ts_list]
```

Apply: `"ts": _to_utc_ns(list(t.ts))` for ticks, `_to_utc_ns(list(k.ts))` for kbars; for snapshots subtract from the single `snapshot.ts`. Add a short docstring noting WHY (the SDK encodes TW wall-clock as UTC).

**Tests:** Required (data-correctness). Feed a known TW-as-UTC ns and assert it shifts back 8h.

```python
def test_to_utc_ns_shifts_back_8h():
    tw_0900 = 1718348400_000000000  # placeholder: TW 09:00 as-UTC for the test date
    assert _to_utc_ns([tw_0900])[0] == tw_0900 - 28_800_000_000_000
```

**Verification:**
Run: `uv run pytest tests/test_timezone.py -v` → pass.
Then re-run Task 1's curl: first tick now decodes to ~01:00 UTC (true UTC for TW 09:00).

**Commit:**
```bash
git add src/shioaji_server/routes/market_data.py tests/test_timezone.py
git commit -m "fix: gateway HTTP ts 統一轉真 UTC（修正 TW-as-UTC 偏 8h）"
```

---

### Task 3: Verify sinopac HTTP now aligns with WS (cross-check, no code)

**Files:** Read-only validation.

**Implementation:**

The sinopac Rust HTTP parser (`crates/adapters/sinopac/src/http/parse.rs`) does `UnixNanos::from(ts)` with no conversion — now that the gateway emits true UTC, the adapter's HTTP path matches its WS path (which already produces true UTC). Confirm by fetching a kbar through the adapter or re-running the gateway curl and comparing a WS tick's decoded time for the same instrument/time.

**Verification:** HTTP-derived and WS-derived `ts_event` for the same wall-clock moment differ by 0 (was 8h). No adapter code change needed — the fix is upstream.

**Commit:** _(none)_

---

### Task 4: Back-correct the already-downloaded catalog

**Files:**
- Create: `scripts/migrate_ts_to_utc.py`

**Implementation:**

Existing catalog parquet (bars + ticks) were written TW-as-UTC. Shift `ts_event`/`ts_init` back 8h in place. NT ParquetDataCatalog filenames encode the time range, so rewrite files and re-key names (or rewrite via catalog API). Idempotency guard: stamp a marker file so a re-run doesn't double-shift.

```python
# For each parquet under catalog/data/{bar,trade_tick}/<instrument>/:
#   df = pl.read_parquet(f)
#   df = df.with_columns([(pl.col("ts_event") - 28_800_000_000_000).alias("ts_event"),
#                         (pl.col("ts_init")  - 28_800_000_000_000).alias("ts_init")])
#   write back with corrected NT filename (start_ts_end_ts)
```

Reuse `scripts/inspect_catalog.py` patterns for iterating instruments. Support `--dry-run` (report counts, no write) and a `catalog/.ts_utc_migrated` marker.

**Verification:**
Run: `uv run python -m scripts.migrate_ts_to_utc --catalog-path ./catalog --dry-run` → reports N files.
Then real run; re-read a file: first `ts_event`→UTC now ~01:00 (true UTC). `inspect_catalog` date ranges unchanged in *date*, shifted in *time*.

**Commit:**
```bash
git add scripts/migrate_ts_to_utc.py
git commit -m "feat: catalog ts_event/ts_init 批量校正為真 UTC（一次性 migration）"
```

> ⚠️ Decision point: if re-downloading is cheaper/safer than migrating (quota permitting), skip this task and re-fetch with the fixed gateway instead. Note the choice in the commit/PR.

---

### Task 5: Align download-script TradeId to nanosecond (fixes S2 + §1)

**Files:**
- Modify: `scripts/fetch_single_ticks.py`
- Test: `tests/test_trade_id.py`

**Implementation:**

`_ns_to_trade_id` uses microsecond `strftime` → same-microsecond trades collide (proven 36/1922). The sinopac HTTP adapter uses `format!("{}-{}", code, ts_ns)` (raw nanosecond int) which is unique. Align:

```python
def _ns_to_trade_id(code: str, ts_ns: int) -> TradeId:
    # Match sinopac HTTP adapter convention: nanosecond-precision integer suffix.
    return TradeId(f"{code}-{ts_ns}")
```

(Use the *corrected* true-UTC `ts_ns` so historical IDs match live HTTP IDs.)

**Tests:** Required. Two ticks in the same microsecond but different ns → distinct TradeId.

**Verification:**
Run: `uv run pytest tests/test_trade_id.py -v` → pass.
Re-fetch one day; assert `df["trade_id"].n_unique() == len(df)` (zero duplicates).

**Commit:**
```bash
git add scripts/fetch_single_ticks.py tests/test_trade_id.py
git commit -m "fix: 下載腳本 TradeId 改納秒整數，對齊 sinopac 並消除同微秒重複"
```

---

**Sequencing:** Task 1 → 2 → 3 (verify) → then 4 and 5 in parallel. Tasks 4/5 depend on 2 being correct.
