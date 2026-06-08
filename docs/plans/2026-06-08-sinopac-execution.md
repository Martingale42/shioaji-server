# Sinopac Adapter Execution Fixes (P1–P3) Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan.
> **⚠️ Different repo:** All work is in `/home/cy/Code/MT5/nautilus_trader`, branch `sinopac-adapter-clean` (NOT shioaji-server).

**Goal:** Fix three real-money correctness bugs in the sinopac live execution client: duplicate fill TradeId, illegal order-state transition, and timeout-as-reject losing an order that actually filled.

**Architecture:** sinopac adapter = Rust core (pyo3, `crates/adapters/sinopac/`) + Python NT client (`nautilus_trader/adapters/sinopac/`). P1 crosses the Rust/Python boundary (needs a Rust rebuild); P2/P3 are Python-only.

**Tech Stack:** Rust (pyo3), Python (NautilusTrader LiveExecutionClient), maturin/cargo.

**Background context (read first):**
- Execution client: `nautilus_trader/adapters/sinopac/execution.py`. Order lifecycle uses NT helpers `generate_order_accepted/rejected/filled/...`; NT's Rust core enforces a strict state machine (e.g. `ACCEPTED → REJECTED` is illegal and panics).
- Fill events flow: WS deal event → Rust `crates/adapters/sinopac/src/websocket/order_parse.rs` (`set_deal_fields`) → Python dict → `_handle_deal_event` → `generate_order_filled(... trade_id=TradeId(...))`.
- `_trade_id_to_client_order_id` maps venue trade_id → NT client order id; set only after a successful HTTP `place_order` returns `response["trade_id"]`.
- **Build:** confirm the rebuild command from the repo's Makefile/README (likely `uv run maturin develop` or a `make` target). A Rust change is invisible to Python until rebuilt.
- Reference behaviour: compare the Hyperliquid adapter's timeout handling (keeps mapping, waits for WS) for P3.

---

### Task 1: P1 — Unique fill TradeId via `seqno` (Rust + Python)

**Files:**
- Modify: `crates/adapters/sinopac/src/websocket/order_parse.rs` (`set_deal_fields`)
- Modify: `nautilus_trader/adapters/sinopac/execution.py` (~line 354)
- Test: `tests/integration_tests/adapters/sinopac/` (Python) + Rust unit test in `order_parse.rs`

**Implementation:**

Root cause: TradeId is built `f"{trade_id_str}-{ordno}"`, but `ordno` (brokerage order number) is identical across all partial fills of one order → duplicate TradeId → NT fill dedup corrupts the book. The per-fill unique `seqno` exists in Rust `StockDealEventData.seqno` but `set_deal_fields()` never exposes it to Python.

1. **Rust** — in `set_deal_fields`, write `seqno` into the Python dict it builds (alongside the existing fields):
   ```rust
   dict.set_item("seqno", data.seqno)?;   // expose per-fill unique sequence number
   ```
2. **Rebuild** the Rust extension (see Background) so Python sees the new key.
3. **Python** — `execution.py` ~line 354, prefer `seqno`, fall back to `ordno`:
   ```python
   seq = event.get("seqno") or ordno
   trade_id = TradeId(f"{trade_id_str}-{seq}")
   ```

**Tests:** Required (financial correctness). Rust: assert `set_deal_fields` output contains `seqno`. Python: two partial-fill events with same `ordno`, different `seqno` → two distinct TradeIds, position aggregates correctly.

**Verification:**
Run Rust tests: `cargo test -p nautilus-sinopac` (from repo root) → pass.
Run Python: `uv run pytest tests/integration_tests/adapters/sinopac/ -v` → pass.

**Commit:**
```bash
git add crates/adapters/sinopac/src/websocket/order_parse.rs nautilus_trader/adapters/sinopac/execution.py tests/
git commit -m "fix(sinopac): 成交 TradeId 改用 seqno，避免 partial fill 重複 ID 損毀帳本"
```

---

### Task 2: P2 — Reject only orders not yet in a terminal/accepted state

**Files:**
- Modify: `nautilus_trader/adapters/sinopac/execution.py` (~lines 255–266)

**Implementation:**

A WS `op_type="New", op_code!="00"` (exchange secondary rejection) arriving *after* the order was already HTTP-accepted drives an illegal `ACCEPTED → REJECTED` transition → state-machine panic. Guard it: only reject if the order is still pending.

```python
order = self._cache.order(client_order_id)
if order is not None and order.status in (
    OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
):
    self._log.warning(f"Late 'New' failure for {client_order_id} in {order.status}; ignoring")
    return
# else: normal reject path
self.generate_order_rejected(...)
```

**Tests:** Optional but valuable — simulate accepted-then-New-failure → no exception, order stays ACCEPTED + warning logged.

**Verification:**
Run: `uv run pytest tests/integration_tests/adapters/sinopac/ -v` → pass, no `InvalidStateTransition`.

**Commit:**
```bash
git add nautilus_trader/adapters/sinopac/execution.py
git commit -m "fix(sinopac): 後到的 New 失敗事件不再對已接受訂單做非法 reject"
```

---

### Task 3: P3 — Don't reject on transport timeout (keep mapping for WS/reconciliation)

**Files:**
- Modify: `nautilus_trader/adapters/sinopac/execution.py` (~lines 411–431, the `place_order` try/except)

**Implementation:**

On HTTP timeout the code calls `generate_order_rejected()` but the order may have actually reached the exchange — and the trade_id→client_order_id mapping was never set, so later WS fills are silently ignored (hidden exposure). Distinguish *transport* failure from *business* rejection:

```python
try:
    response = await self._http_client.place_order(...)
    trade_id_str = response["trade_id"]
    self._trade_id_to_client_order_id[trade_id_str] = order.client_order_id
    self.generate_order_accepted(...)
except (asyncio.TimeoutError, OSError) as e:
    # Transport failure — the order MAY be live. Do NOT reject. Leave it pending
    # (submitted) and let WS events / reconciliation resolve the true state.
    self._log.error(f"place_order transport failure for {order.client_order_id}: {e!r}; "
                    f"leaving pending for reconciliation")
    # no generate_order_rejected here
except Exception as e:
    # Genuine business rejection (validation, margin, etc.)
    self.generate_order_rejected(..., reason=str(e))
```

Ensure reconciliation (`generate_position_status_reports` / fill reports) can later adopt such an order. Cross-check the order isn't left in a stuck `SUBMITTED` forever — if NT requires an accepted/rejected within a window, document the reconciliation path.

**Tests:** Optional — mock `place_order` raising `asyncio.TimeoutError` → no reject generated, order remains submitted/pending.

**Verification:**
Run: `uv run pytest tests/integration_tests/adapters/sinopac/ -v` → pass.
Reason through: timeout + order actually filled → WS deal event finds (or reconciliation rebuilds) the order, position is correct.

**Commit:**
```bash
git add nautilus_trader/adapters/sinopac/execution.py
git commit -m "fix(sinopac): HTTP 超時不再誤 reject，保留 pending 待 WS/對帳補全，消除隱性曝險"
```

---

**Sequencing:** P2 and P3 are independent Python-only, do first (no rebuild). P1 needs a Rust rebuild — do last or in a separate build cycle. Run the full sinopac integration suite after each.
