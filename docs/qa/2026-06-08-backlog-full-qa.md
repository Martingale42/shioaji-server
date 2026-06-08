# QA Report: BL-1~4 Backlog Full Verification

**Date**: 2026-06-08
**Scope**: shioaji-server (main) + nautilus_trader (sinopac-adapter-clean)
**Verdict**: **PASS**

## Summary

| Metric | Value |
|--------|-------|
| Total tests executed | **141** (37 shioaji-server + 80 Rust + 27 Python integration) |
| Tests passed | 141 |
| Tests failed | 0 |
| Bugs found | 0 |
| Ruff violations | 0 |

## Test Results by Area

### 1. shioaji-server full suite — PASS

```
37 passed, 1 warning in 1.12s
```

- `test_health.py` (6): session probe/cache/concurrency
- `test_orders_custom_field.py` (9): BL-1 gateway custom_field plumbing
- `test_restamp_metadata.py` (4): BL-3 metadata repair logic
- `test_session_recovery.py` (7): reconnect/resubscribe
- `test_timezone.py` (4): UTC conversion
- `test_trade_id.py` (3): nanosecond trade ID uniqueness
- `test_ws_resilience.py` (3): WebSocket error handling

Ruff: all checks passed across `src/`, `tests/`, `scripts/`.

### 2. Batch 2 data integrity (catalog restamp) — PASS

```
ticks 10,609,509  bars 729,315
first tick UTC: 2020-03-02 01:00:00.586000+00:00
```

- ParquetDataCatalog.query(TradeTick) and .query(Bar) succeed (no MissingMetadata)
- First tick decodes to 01:00 UTC — true UTC, NOT double-shifted to 09:00
- Counts match expected: ~10.6M ticks, ~729K bars
- Backup verified at `catalog_pre_restamp_backup/`

### 3. nautilus_trader Rust tests — PASS

```
72 unit tests + 5 HTTP integration + 3 WebSocket integration = 80 passed
```

Covers: instrument parsing, tick sizes, order side parsing, HTTP model deserialization,
WebSocket message parsing, order event parsing, deal unique key extraction (BL-1).

### 4. nautilus_trader Python integration tests — PASS

```
27 passed in 0.22s
```

- P1 (TradeId uniqueness): 3 tests — partial fills, ordno fallback, seqno collision proof
- P2 (late NEW failure): 2 tests — ignore after accept, reject before accept
- P3 (timeout adoption): 4 tests — timeout no-reject, business rejection, token adopt, synthetic fallback
- BL-1 (custom_field end-to-end): 5 tests — submit sends token, order status resolves,
  deal backfill resolves, restart recompute adopts, external order returns None
- Token utility: 3 tests — deterministic, length/ascii, distinct inputs differ
- Config: 7 tests
- Factories: 3 tests

### 5. BL-1 edge case coverage — PASS

| Scenario | Test | Status |
|----------|------|--------|
| Timeout → later event with token → adopted as SAME NT order | `test_bl1_order_status_event_with_token_resolves_timed_out_order` | PASS |
| Restart → caches cleared → token recomputed → still resolves | `test_bl1_restart_adopt_via_recomputed_hash` | PASS |
| No token → falls back to synthetic SINOPAC-{trade_id} | `test_bl1_external_order_no_token_no_mapping_returns_none` + `test_p3_reconciliation_without_token_falls_back_to_synthetic` | PASS |
| Submit order sends custom_field token | `test_bl1_submit_order_sends_custom_field_token` | PASS |
| Deal event resolves via backfilled mapping | `test_bl1_deal_event_resolves_via_backfilled_mapping` | PASS |
| Prior P1/P2/P3 no regression | 9 legacy tests | PASS |

### 6. Mechanical cleanup verification — PASS

- BL-2: `scripts/fetch_single.py` — dead imports removed, ruff clean
- BL-4: `exclude-newer` absent from both `pyproject.toml` and `python/pyproject.toml`

### 7. Cross-contamination check — PASS

- shioaji-server: `main` branch
- nautilus_trader: `sinopac-adapter-clean` branch
