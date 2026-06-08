# Code Review: Backlog Batch 3 (BL-1 -- P3 Timed-Out Order Adoption via custom_field Token)

**Date:** 2026-06-08
**Reviewer:** Code Reviewer (Batch 3)
**Commits reviewed:**
- `shioaji-server` @ `main`: `2139ed2` (gateway custom_field plumbing), `b16a084` (docs)
- `nautilus_trader` @ `sinopac-adapter-clean`: `451ebf32d8` (Rust plumbing), `9df0581cb7` (Python adopt logic + tests)

## Verdict: APPROVED WITH NOTES

Token plumbed end-to-end. No `unwrap()`/`expect()` on external data. All `custom_field` fields are `Option<String>` with `#[serde(default)]`. Fallback behavior unchanged when token absent. All tests pass (37 shioaji-server, 72 Rust, 27 Python integration). Ruff clean. Two minor observations, zero blockers.

---

## Mandatory Checks

### 1. Rust Safety: No `unwrap()`/`expect()` on External Data

| Location | Type | Safe? |
|---|---|---|
| `http/models.rs:264-265` PlaceOrderRequest | `Option<String>`, `skip_serializing_if = "Option::is_none"` | YES |
| `http/models.rs:320-322` TradeInfo | `Option<String>`, `#[serde(default)]` | YES |
| `websocket/messages.rs:258-260` StockOrderInfo | `Option<String>`, `#[serde(default)]` | YES |
| `websocket/messages.rs:291-293` FuturesOrderInfo | `Option<String>`, `#[serde(default)]` | YES |
| `websocket/order_parse.rs:37` set_order_fields param | `Option<&str>` | YES |
| `python/http.rs:294` py_place_order param | `Option<String>` | YES |

**No `unwrap()`/`expect()` found on any `custom_field` path.** Access pattern is consistently `as_deref()` for Rust and `.get()` for Python.

### 2. End-to-End Token Plumbing

```
Python _submit_order
  -> _coid_token(client_order_id) -> 6-char base62 token
  -> HTTP place_order(custom_field=token)
  -> Rust PlaceOrderRequest { custom_field: Some(token) }
  -> serde JSON serialization -> gateway HTTP POST /api/orders/place
  -> Gateway routes/orders.py: api.Order(custom_field=req.custom_field[:6])
  -> Shioaji SDK (Order.custom_field stored)

Recovery path A (WS order-status event):
  Gateway on_order -> WS msg (verbatim) -> Rust StockOrderInfo.custom_field
  -> order_parse::set_order_fields -> Python dict["custom_field"]
  -> _handle_order_status_event -> _resolve_client_order_id(order_id, custom_field)
  -> token match against cache orders -> backfill _trade_id_to_client_order_id

Recovery path B (restart reconciliation via list_trades):
  Gateway /api/orders/trades -> custom_field: getattr(t.order, "custom_field", "")
  -> Rust TradeInfo.custom_field -> Python dict["custom_field"]
  -> generate_order_status_reports -> _resolve_client_order_id(trade_id, custom_field)
  -> token recompute against cache -> real client_order_id -> NT adopts
```

**Confirmed: token flows from submit through gateway, back via WS events and REST list_trades, and resolves to the original `client_order_id` in all three consumer paths (order-status handler, deal handler, reconciliation).**

### 3. Token Recovery Correctness

| Property | Verified |
|---|---|
| Deterministic (restart-safe) | YES -- blake2s hash of `client_order_id`, recomputable from cache |
| 6-char, ASCII-only, fits `ConStrAsciiMax6` | YES -- base62 `[0-9a-zA-Z]`, exactly 6 chars |
| Collision space sufficient | YES -- 62^6 = ~56.8 billion vs O(10) active orders |
| No persisted map required | YES -- recomputed from `_cache.orders()` in slow path |
| Backfill on first resolution | YES -- `_trade_id_to_client_order_id[lookup_key]` populated for fast-path on subsequent events |

### 4. Fallback When `custom_field` Absent

Verified in code and test (`test_p3_reconciliation_without_token_falls_back_to_synthetic`):
- `_resolve_client_order_id` returns `None` when `custom_field` is None/empty and no direct mapping exists
- Caller synthesizes `ClientOrderId("SINOPAC-{trade_id}")` -- identical to pre-BL-1 behavior
- `test_bl1_external_order_no_token_no_mapping_returns_none` also covers this

### 5. No Regression on Prior P1/P2/P3 Tests

All prior tests pass without modification:
- `test_p1_partial_fills_same_seqno_distinct_exchange_seq_yield_distinct_trade_ids` -- PASS
- `test_p1_falls_back_to_ordno_when_exchange_seq_absent` -- PASS
- `test_p1_seqno_key_would_collide_proves_regression` -- PASS
- `test_p2_late_new_failure_on_accepted_order_is_ignored` -- PASS
- `test_p2_new_failure_before_accept_still_rejects` -- PASS
- `test_p3_timeout_does_not_reject_order` -- PASS
- `test_p3_business_rejection_still_rejects` -- PASS

### 6. Deal Events Do NOT Carry `custom_field`

Confirmed by Rust struct inspection: `StockDealEventData` and `FuturesDealEventData` have no `custom_field` field. The `set_deal_fields` function does not include it. The Python deal handler `_handle_deal_event` calls `event.get("custom_field")` which returns `None` for deals -- this is harmless (falls through to fast-path direct mapping or external). The design relies on the order-status event arriving first (which it does in Shioaji's protocol) to backfill the mapping for subsequent deals.

---

## Test Verification Results

```
shioaji-server:   37 passed (uv run pytest tests/ -v)
Ruff:             All checks passed
Rust:             72 passed (cargo test -p nautilus-sinopac --features python)
Python sinopac:   27 passed (.venv/bin/python -m pytest tests/integration_tests/adapters/sinopac/ -v)
```

---

## Findings

### Minor-1: Stale comment in `_submit_order` timeout handler [execution.py:522-533]

The timeout error comment block still describes the **pre-BL-1** behavior:

```python
# Reconciliation (`generate_order_status_reports`
# -> `list_trades`) rebuilds an OrderStatusReport keyed on the venue
# `trade_id` with a *synthesized* `client_order_id` (`SINOPAC-{trade_id}`)
# when no mapping exists.
```

After BL-1, the `custom_field` token enables recovery of the real `client_order_id` in most cases. The comment should reflect that the token now provides a recovery path, with the synthesized fallback being for external/pre-feature orders only.

**Risk:** Documentation drift. No runtime impact -- code behavior is correct.

### Minor-2: No Rust-level test asserting `custom_field` value in order event pydict [order_parse.rs]

The existing Rust test `test_deal_pydict_exposes_per_fill_unique_keys` tests the deal path (which doesn't carry `custom_field`). There is no analogous Rust test for the order event path that asserts the `custom_field` value in the pydict. The test data `ws_order_stock.json` was updated with `"custom_field": "ab12cd"`, and deserialization passes, but the parsed value is only implicitly tested (serde succeeds). The Python integration tests cover the end-to-end flow, so this is defense-in-depth, not a gap.

**Risk:** Low. Python tests provide full coverage of the round-trip.

---

## Design Notes (Informational)

1. **Bit extraction in `_coid_token`**: Uses `(h >> (6*i)) % 62` for i in 0..5, extracting 36 bits from the 64-bit blake2s digest. Values 62 and 63 alias to 0 and 1, creating slight non-uniformity. With 62^6 = ~56.8 billion unique tokens vs O(10) active orders, collision probability is negligible (~10^-9 per order pair).

2. **`_resolve_client_order_id` iterates `_cache.orders()`**: The slow path scans all non-closed orders in the cache. For the expected O(10) active orders in a typical session, this is fine. If order volume ever grew to hundreds, the loop is still acceptable since `_coid_token` is fast (one blake2s per order). The backfill mechanism ensures each lookup_key only takes the slow path once.

3. **Gateway `custom_field` validation**: The gateway accepts arbitrary strings and truncates to 6 characters (`req.custom_field[:6]`). No ASCII-only validation is enforced at the gateway level. The adapter only sends base62 tokens (always ASCII), so this is not a risk in practice. Shioaji's `ConStrAsciiMax6` would reject non-ASCII at the SDK level as a second safety net.
