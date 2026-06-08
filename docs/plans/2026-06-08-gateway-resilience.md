# Gateway Resilience Fixes (G5–G8) Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan.

**Goal:** Close four independent robustness gaps in the gateway so a misbehaving WebSocket client, a pure-futures account, or concurrent health checks can't degrade the service.

**Architecture:** Each fix is local and independent — no shared state, can be done/committed separately. All in `shioaji-server` repo.

**Tech Stack:** Python, FastAPI, asyncio, Shioaji SDK.

**Background context (read first):**
- WS endpoint: `src/shioaji_server/app.py` `websocket_endpoint` — a `while True: receive_text()` loop dispatching subscribe/unsubscribe; only `WebSocketDisconnect` triggers `manager.disconnect(ws)`. Any *other* exception escapes the loop, leaving the ws in `manager.active_connections` + `manager.subscriptions` (zombie) and its Shioaji subscription orphaned.
- `manager.disconnect(ws)` returns the orphaned `(code, quote_type)` keys; `_unsubscribe_orphaned(sj_client, orphaned)` already exists in `app.py` to release them.
- Connection check: `sj_client.require_connected()` raises `RuntimeError("Not connected …")` when down.
- `check_session` (client.py) caches a probe but has no in-flight lock — concurrent callers each issue `api.usage()` (accounting limit 25/5s).

---

### Task 1: G5 — WebSocket guard + guaranteed cleanup

**Files:**
- Modify: `src/shioaji_server/app.py`

**Implementation:**

Wrap the WS loop so (a) it refuses to act when not connected, and (b) any exception still cleans up. Restructure `websocket_endpoint`:

```python
await manager.connect(ws)
try:
    while True:
        text = await ws.receive_text()
        try:
            msg = json.loads(text)               # G6 handled in Task 2
        except json.JSONDecodeError:
            await ws.send_text(json.dumps({"type": "error", "detail": "invalid JSON"}))
            continue
        # ... validate code/quote_type ...
        if not sj_client.connected:               # G5: refuse when down
            await ws.send_text(json.dumps({"type": "error", "detail": "gateway not connected"}))
            continue
        # ... subscribe/unsubscribe, wrapped so resolve_contract errors don't kill the loop ...
except WebSocketDisconnect:
    pass
finally:
    orphaned = manager.disconnect(ws)             # G5: always clean up
    await _unsubscribe_orphaned(sj_client, orphaned)
```

Wrap the subscribe/unsubscribe body in try/except so `resolve_contract` (AttributeError/ValueError) sends an error frame instead of escaping.

**Verification:**
Run: `uv run python -c "import shioaji_server.app"` (imports clean).
Manual: connect a WS client, send subscribe before login → receive error frame, connection stays open; disconnect → no zombie (add a temp log in `disconnect`, confirm it fires).

**Commit:**
```bash
git add src/shioaji_server/app.py
git commit -m "fix: WebSocket 加連線檢查與 finally 清理，杜絕殭屍連線與孤兒訂閱"
```

---

### Task 2: G6 — Tolerate malformed WS JSON

**Files:**
- Modify: `src/shioaji_server/app.py`

**Implementation:**

Covered structurally by Task 1's `try: json.loads except JSONDecodeError: send error; continue`. If doing G6 standalone, that is the whole change. One bad frame must not drop the connection.

**Verification:** Send `"not json"` over WS → error frame returned, loop continues.

**Commit:**
```bash
git commit -m "fix: WebSocket 容忍非法 JSON，回錯誤幀而非斷線"
```
_(If Task 1 already includes the JSON guard, fold G6 into that commit.)_

---

### Task 3: G7 — Guard `stock_account is None` in orders

**Files:**
- Modify: `src/shioaji_server/routes/orders.py`
- Test: `tests/test_orders_guard.py` (optional)

**Implementation:**

`routes/orders.py:39` calls `api.update_status(api.stock_account)` unconditionally; a pure-futures account has `stock_account is None` → 500. Mirror the existing `futopt_account is not None` pattern:

```python
if api.stock_account is not None:
    api.update_status(api.stock_account)
# else: skip stock status; still return futures trades
```

Audit nearby handlers for the same unguarded `stock_account` access.

**Verification:**
Run: `uv run python -c "import shioaji_server.routes.orders"`.
Manual/mock: with `stock_account=None`, `/api/orders/trades` returns 200 (not 500).

**Commit:**
```bash
git add src/shioaji_server/routes/orders.py
git commit -m "fix: orders 對 stock_account=None 防守，純期貨帳號不再 500"
```

---

### Task 4: G8 — Single-flight lock on session probe

**Files:**
- Modify: `src/shioaji_server/client.py`
- Test: `tests/test_health.py` (extend)

**Implementation:**

`check_session` lets concurrent callers each fire `api.usage()`. Add a probe lock so only one probe is in flight; others await its result (or the fresh cache). Add `_probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock)` and:

```python
async def check_session(self, force: bool = False) -> bool:
    if not self.connected:
        return False
    now = time.monotonic()
    if not force and self._session_checked_at >= 0 and (now - self._session_checked_at) < self.session_probe_ttl:
        return self._session_ok
    async with self._probe_lock:
        # re-check cache inside lock — a concurrent caller may have just refreshed it
        now = time.monotonic()
        if not force and self._session_checked_at >= 0 and (now - self._session_checked_at) < self.session_probe_ttl:
            return self._session_ok
        try:
            await asyncio.wait_for(self.run_sync(self.api.usage), timeout=self.session_probe_timeout)
            self._session_ok = True
        except Exception:
            self._session_ok = False
        self._session_checked_at = time.monotonic()
        return self._session_ok
```

**Tests:** Extend `test_health.py`: fire N concurrent `check_session()` with a mocked `usage` → `usage` called once.

**Verification:** Run `uv run pytest tests/test_health.py -v` → pass, including the new single-flight test.

**Commit:**
```bash
git add src/shioaji_server/client.py tests/test_health.py
git commit -m "fix: check_session 加單飛鎖，並發健康檢查不重複打 usage 配額"
```

---

**Sequencing:** Independent — any order. Tasks 1+2 naturally combine into one commit.
