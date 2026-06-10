# Gateway Silent-Death Keepalive Watchdog — Implementation Plan

> **For Claude:** Use superpowers:executing-plans, superpowers:subagent-driven-development, or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Give the existing Solace-session recovery (`_handle_session_down`) a second trigger — a background watchdog inside `ShioajiGatewaySession` that actively probes every 5s and, after 2 consecutive failures while connected, fires recovery — so the gateway self-heals from *silent* deaths (backend stops responding without firing the SDK `session_down` callback).

**Architecture:** A long-lived asyncio task owned by `ShioajiGatewaySession`, started on successful `login()` and cancelled on `logout()`. The per-tick policy (`_keepalive_tick`) is split from the scheduling loop (`_keepalive_loop`) so the decision logic is unit-testable without real time. Recovery reuses the existing, tested `_handle_session_down` via `asyncio.create_task` (loop-native; the SDK callback path uses `run_coroutine_threadsafe` because it runs cross-thread). No new recovery logic, no app.py changes.

**Tech Stack:** Python 3.13, asyncio, `dataclasses`, pytest + pytest-asyncio (`asyncio_mode = "auto"`). ALWAYS run Python via `uv run`.

**Design doc:** `docs/plans/2026-06-10-gateway-silent-death-keepalive-design.md` (approved).

**Repo orientation (zero-context primer):**

- `src/shioaji_server/session.py` — class `ShioajiGatewaySession` (a `@dataclass`). It is the **server-side** wrapper around the Shioaji SDK that owns the single upstream broker session. Renamed this branch from `client.py`/`ShioajiClient` (commit `0bb74bf`). Key existing members and line anchors (as of this branch):
  - dataclass fields end around `session.py:37` (`_probe_lock`). Existing tunables: `session_probe_ttl=5.0`, `session_probe_timeout=5.0`, `reconnect_max_backoff=60.0`, `reconnect_warn_after=5`, plus `_reconnecting`, `_reconnect_lock`, `_probe_lock`.
  - `async def login(...)` at `session.py:60`; sets `self.connected = True` at `:82`; `return accounts` at `:94`.
  - `async def logout(self)` at `session.py:99`.
  - `async def check_session(self, force: bool = False) -> bool` at `session.py:116` — probes the live backend via `api.usage()`, caches the result for `session_probe_ttl`, single-flighted by `_probe_lock`. Returns `False` when not connected or the probe fails/raises (it swallows probe exceptions). **`force=True` bypasses the cache.** Reuse as-is.
  - `def _schedule_reconnect(self)` at `session.py:183` — the SDK `session_down` callback; runs on the SDK's thread, schedules `_handle_session_down` via `run_coroutine_threadsafe`. Do NOT call this from the watchdog (watchdog is in-loop).
  - `async def _handle_session_down(self)` at `session.py:196` — the existing recovery: serialized by `_reconnect_lock`, guarded by `_reconnecting`, sets `connected=False` early, retries `_relogin` with backoff until success. Idempotent under concurrent triggers. This is what the watchdog will fire.
- `src/shioaji_server/app.py` — `lifespan` calls `_auto_login(app.state.sj)` (→ `login()`) on startup and `await app.state.sj.logout()` on shutdown. **No app.py change needed** (keepalive lifecycle rides on login/logout).
- `tests/test_session_recovery.py` — existing offline regression suite (8 tests) for the recovery path. Uses `ShioajiGatewaySession(api=MagicMock())`, `AsyncMock`, monkeypatching; `asyncio_mode = "auto"` so `async def test_*` needs no decorator. Follow this style. The `_down_client()` helper builds a client with stored `_login_kwargs` and `connected=False`.
- Per `~/.claude/CLAUDE.md`: `uv run` always (never bare `python`); English identifiers/logs/comments; no `--no-verify`; never disable a failing test.

Baseline: the full suite is green (`96 passed, 1 warning` — the warning is a pre-existing Pydantic-v2 deprecation in the vendored `shioaji` package). It MUST stay green.

---

### Task 1: Watchdog fields + tick policy + recovery trigger

**Files:**
- Modify: `src/shioaji_server/session.py`
- Test: `tests/test_session_recovery.py` (extend)

**Implementation:**

Add three dataclass fields (next to the existing tunables, ~`session.py:37`):

```python
keepalive_interval: float = 5.0
keepalive_fail_threshold: int = 2
_keepalive_task: asyncio.Task | None = None
_recovery_task: asyncio.Task | None = None
```

Add the recovery trigger and the per-tick policy. `_keepalive_tick` is the pure decision unit (no sleep) — this is what tests target:

```python
def _schedule_recovery(self) -> None:
    """Fire the existing recovery from within the event loop.

    Unlike `_schedule_reconnect` (the SDK callback, which runs on the SDK's
    thread and must use run_coroutine_threadsafe), the watchdog runs in the
    loop, so create_task is correct. Both paths converge on the same
    `_handle_session_down`, whose `_reconnecting` guard collapses concurrent
    triggers — so SDK callback + watchdog firing together is safe. The task
    reference is held so it is not garbage-collected mid-flight.
    """
    self._recovery_task = asyncio.create_task(self._handle_session_down())

async def _keepalive_tick(self, consecutive_fails: int) -> int:
    """
    Definition: One liveness check; returns the updated consecutive-failure
        count and fires recovery when it reaches the threshold.
    Domain:     Skips entirely (returns the count unchanged) when not
        connected — covers deliberate logout AND an in-progress recovery
        (which sets connected=False), so the watchdog never fights either.
        Uses check_session(force=True) to bypass the probe cache. A silent
        death is connected=True + probe False.
    Returns:    0 on a healthy probe or after firing recovery; otherwise the
        incremented failure count (< threshold).
    """
    if not self.connected:
        return consecutive_fails
    if await self.check_session(force=True):
        return 0
    consecutive_fails += 1
    if consecutive_fails >= self.keepalive_fail_threshold:
        self._schedule_recovery()
        return 0
    return consecutive_fails
```

**Tests (Required — this is the core policy):** extend `tests/test_session_recovery.py`. Build a connected client (`ShioajiGatewaySession(api=MagicMock())`, set `connected=True`), monkeypatch `check_session` to an `AsyncMock` with a scripted `side_effect` list, and monkeypatch `_schedule_recovery` to a plain spy (`MagicMock`) so no real task is created.

```python
async def test_keepalive_tick_triggers_recovery_after_2_consecutive_fails(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock())
    client.connected = True
    monkeypatch.setattr(client, "check_session", AsyncMock(return_value=False))
    spy = MagicMock()
    monkeypatch.setattr(client, "_schedule_recovery", spy)
    fails = await client._keepalive_tick(0)   # 1st fail
    assert fails == 1 and spy.call_count == 0
    fails = await client._keepalive_tick(fails)  # 2nd consecutive fail
    assert fails == 0 and spy.call_count == 1     # fired, counter reset

async def test_keepalive_tick_resets_on_success(monkeypatch):
    # probe sequence False, True, False → never reaches threshold, never fires
    client = ShioajiGatewaySession(api=MagicMock()); client.connected = True
    monkeypatch.setattr(client, "check_session",
                        AsyncMock(side_effect=[False, True, False]))
    spy = MagicMock(); monkeypatch.setattr(client, "_schedule_recovery", spy)
    f = await client._keepalive_tick(0)   # False → 1
    f = await client._keepalive_tick(f)   # True  → 0
    f = await client._keepalive_tick(f)   # False → 1
    assert f == 1 and spy.call_count == 0

async def test_keepalive_tick_skips_when_not_connected(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock()); client.connected = False
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(client, "check_session", probe)
    spy = MagicMock(); monkeypatch.setattr(client, "_schedule_recovery", spy)
    assert await client._keepalive_tick(1) == 1   # unchanged
    probe.assert_not_awaited()                      # never probed
    assert spy.call_count == 0
```

**Verification:**

Run: `uv run pytest tests/test_session_recovery.py -v`
Expected: existing 8 + 3 new all pass.

**Commit:**
```bash
git add src/shioaji_server/session.py tests/test_session_recovery.py
git commit -m "feat: keepalive tick policy that triggers recovery on consecutive probe failures"
```

---

### Task 2: Scheduling loop + start/stop lifecycle

**Files:**
- Modify: `src/shioaji_server/session.py`
- Test: `tests/test_session_recovery.py` (extend)

**Implementation:**

Add the loop (sleep + tick; must never die on an unexpected exception) and the start/stop controls:

```python
async def _keepalive_loop(self) -> None:
    """Probe every keepalive_interval; never dies on a stray exception.

    check_session already swallows probe errors into False (counted toward the
    threshold), so a failure is a normal False, not an exception. The try/except
    is defense-in-depth: a watchdog that crashes silently is worse than none.
    Only CancelledError exits the loop (clean shutdown).
    """
    fails = 0
    while True:
        try:
            await asyncio.sleep(self.keepalive_interval)
            fails = await self._keepalive_tick(fails)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[shioaji-server] keepalive tick error; continuing")

def start_keepalive(self) -> None:
    """Start the watchdog if not already running (idempotent)."""
    if self._keepalive_task is not None and not self._keepalive_task.done():
        return
    self._keepalive_task = asyncio.create_task(self._keepalive_loop())

async def stop_keepalive(self) -> None:
    """Cancel and await the watchdog; safe to call when not running."""
    task = self._keepalive_task
    self._keepalive_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Tests (Required — lifecycle/cancellation correctness):**

```python
async def test_start_keepalive_idempotent():
    client = ShioajiGatewaySession(api=MagicMock())
    client.start_keepalive()
    t1 = client._keepalive_task
    client.start_keepalive()
    assert client._keepalive_task is t1   # no second task
    await client.stop_keepalive()

async def test_stop_keepalive_cancels():
    client = ShioajiGatewaySession(api=MagicMock())
    client.start_keepalive()
    t = client._keepalive_task
    await client.stop_keepalive()
    assert t.cancelled() or t.done()
    assert client._keepalive_task is None

async def test_keepalive_loop_survives_tick_exception(monkeypatch):
    """A raised tick is logged and the loop keeps running (doesn't die)."""
    client = ShioajiGatewaySession(api=MagicMock())
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # skip real waits
    calls = {"n": 0}
    async def boom_then_ok(_fails):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return 0
    monkeypatch.setattr(client, "_keepalive_tick", boom_then_ok)
    task = asyncio.create_task(client._keepalive_loop())
    await asyncio.sleep(0)            # let it spin a few iterations
    assert calls["n"] >= 2            # survived the exception, kept going
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
```

(Note: `test_keepalive_loop_survives_tick_exception` patches `asyncio.sleep` to an `AsyncMock` so the loop spins without real delays — same pattern as the existing `test_session_down_retries_until_success`. Bound it by cancelling after asserting.)

**Verification:**

Run: `uv run pytest tests/test_session_recovery.py -v`
Expected: existing + Task-1 + 3 new all pass.

**Commit:**
```bash
git add src/shioaji_server/session.py tests/test_session_recovery.py
git commit -m "feat: keepalive scheduling loop with idempotent start/stop lifecycle"
```

---

### Task 3: Wire start/stop into login/logout

**Files:**
- Modify: `src/shioaji_server/session.py`
- Test: `tests/test_session_recovery.py` (extend)

**Implementation:**

In `login()` (`session.py:60`), after the session is fully established — i.e. just before `return accounts` (`:94`), still appropriate to be inside or right after the `_lock` block; place it after `connected=True` and the `_login_kwargs` assignment so a started watchdog sees a connected session — call `self.start_keepalive()`.

In `logout()` (`session.py:99`), stop the watchdog. Stop it BEFORE flipping `connected=False`/tearing down, and do it whether or not currently connected is moot — simplest correct placement is at the very top of `logout()` so a logout always halts probing:

```python
async def logout(self) -> None:
    await self.stop_keepalive()
    async with self._lock:
        if not self.connected:
            return
        ...
```

Do NOT touch `_relogin()` — the watchdog that triggered recovery keeps running across the re-login; `_relogin` must not start/stop it.

**Tests (Required — the lifecycle wiring is the integration contract):**

```python
async def test_login_starts_and_logout_stops_keepalive(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock())
    monkeypatch.setattr(client, "_login_sync", MagicMock(return_value=[]))
    await client.login(api_key="k", secret_key="s")
    assert client._keepalive_task is not None and not client._keepalive_task.done()
    await client.logout()
    assert client._keepalive_task is None
```

(If `logout()`'s real `_logout_sync` path needs the executor, monkeypatch `_logout_sync` to a no-op `MagicMock` as the existing tests do for `_login_sync`. Keep the test fully offline.)

**Verification:**

Run: `uv run pytest tests/test_session_recovery.py -v`
Expected: all pass (existing 8 + Task1 3 + Task2 3 + 1 = 15).
Also run the full suite to confirm the login/logout change didn't perturb other suites that drive login/logout (e.g. `test_health.py`):
Run: `uv run pytest tests/ -q`
Expected: `97 passed` (96 baseline + the net-new keepalive tests counted across the file) — confirm no failures; paste the tail.

**Commit:**
```bash
git add src/shioaji_server/session.py tests/test_session_recovery.py
git commit -m "feat: start keepalive watchdog on login, stop on logout"
```

---

### Task 4: Docs + full-suite verification

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md` (troubleshooting section, if a session-stall entry fits)

**Implementation:**

- `docs/ARCHITECTURE.md`: in the `session.py — ShioajiGatewaySession` section, document that the session now self-heals silent deaths via a background keepalive watchdog (probe `api.usage()` every `keepalive_interval`s; after `keepalive_fail_threshold` consecutive failures while connected → reuse `_handle_session_down`). Note it complements the SDK `session_down` callback (which only covers SDK-reported drops) and the CLI-side 2330 probe (downstream gatekeeping).
- `README.md` 故障排除: a short note (Traditional Chinese OK) that overnight/silent Solace drops now auto-recover without a manual restart; mention the `keepalive_interval` / `keepalive_fail_threshold` tunables.

**Verification:**

Run: `uv run pytest tests/ -q`
Expected: full suite green — paste the tail as evidence.
Run: `uv run ruff check src tests`
Expected: clean.

**Commit:**
```bash
git add docs/ARCHITECTURE.md README.md
git commit -m "docs: document gateway silent-death self-healing keepalive watchdog"
```

---

### Task 5: Make logout authoritative over a concurrent recovery (close the logout-races-recovery gap)

**Files:**
- Modify: `src/shioaji_server/session.py`
- Test: `tests/test_session_recovery.py` (extend)

**Context:** The watchdog (or the SDK `session_down` callback) can fire `_handle_session_down` → `_relogin` concurrently with a `logout()`. `stop_keepalive()` only cancels the watchdog *loop*, not an in-flight `_recovery_task`, so a recovery can re-login a session the user just logged out (`connected=True` + live session after logout). This task closes that gap.

**Mechanism — flag-based authority, NOT task cancellation.** Cancelling `_recovery_task` mid-`_relogin` is UNSAFE: `_relogin` rebuilds the SDK via `await loop.run_in_executor(None, self._login_sync)`, and `run_in_executor` futures are not truly cancellable — cancelling the asyncio task abandons the await but the thread-pool thread runs `_login_sync` to completion, reassigning `self.api` to a freshly-logged-in SDK *after* logout, racing logout's `_logout_sync` on the same `self.api`. Instead, make logout authoritative via an intent flag plus the existing `_lock` serialization (both `_relogin` and `logout` already take `_lock`), so `_relogin` is never cancelled mid-executor — it always completes under `_lock` and defers to logout via the flag.

**Implementation:**

1. New dataclass field (with the keepalive fields, ~`session.py:38-41`):
```python
_logout_requested: bool = False
```

2. `login()` — reset the flag when a login commits, so re-login after a prior logout works. Add inside the `_lock` block, right before `self.connected = True` (~`session.py:86`):
```python
self._logout_requested = False
```

3. `logout()` — set the flag FIRST, before `await self.stop_keepalive()` (~`session.py:110`), so any `_relogin` that has not yet started its rebuild will bail:
```python
async def logout(self) -> None:
    self._logout_requested = True
    await self.stop_keepalive()
    async with self._lock:
        if not self.connected:
            return
        ...  # existing _logout_sync + connected=False
```

4. `_relogin()` — at the TOP of its `async with self._lock:` block (~`session.py:337`), before the rebuild, bail if logout intervened:
```python
async with self._lock:
    if self._logout_requested:
        return  # logout is authoritative — do not resurrect a logged-out session
    loop = asyncio.get_running_loop()
    ...  # existing tear-down + _login_sync rebuild + connected=True
```

5. `_handle_session_down()` — at the TOP of the `while True:` loop (~`session.py:296`), bail so the in-flight recovery self-terminates at its next checkpoint instead of being force-cancelled:
```python
while True:
    if self._logout_requested:
        return
    try:
        await self._relogin()
    ...
```

Do NOT cancel `_recovery_task`. Do NOT alter the executor calls. A recovery sleeping in backoff may linger ≤`reconnect_max_backoff`s after logout, then wake, see the flag, and return — harmless (no rebuild, no live session).

**Authority argument (must hold for all interleavings → final state always logged-out):**
- logout wins `_lock` first → `connected=False`; then `_relogin` gets `_lock`, sees flag, returns. ✓
- `_relogin` gets `_lock` but flag already set → returns, no rebuild; logout tears down. ✓
- `_relogin` already past the flag-check, mid-rebuild → completes under `_lock` (never cancelled), sets `connected=True`; logout *waits* for `_lock`, then `_logout_sync` tears down the rebuilt session, `connected=False`. ✓ One wasted cycle, no leak, no orphaned thread.

**Tests (Required — the authority contract; deterministic building blocks, no real race needed):**

```python
async def test_relogin_bails_when_logout_requested(monkeypatch):
    client = _down_client()                 # has _login_kwargs, connected=False
    client._logout_requested = True
    spy = MagicMock(return_value=[])
    monkeypatch.setattr(client, "_login_sync", spy)
    client._manager = None
    await client._relogin()
    spy.assert_not_called()                 # no rebuild
    assert client.connected is False        # not resurrected

async def test_handle_session_down_stops_when_logout_requested(monkeypatch):
    client = _down_client()
    client._logout_requested = True
    relogin = AsyncMock()
    monkeypatch.setattr(client, "_relogin", relogin)
    await client._handle_session_down()
    relogin.assert_not_awaited()            # retry loop bailed at the top
    assert client._reconnecting is False    # flag cleared

async def test_logout_then_relogin_does_not_resurrect(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock())
    monkeypatch.setattr(client, "_login_sync", MagicMock(return_value=[]))
    monkeypatch.setattr(client, "_logout_sync", MagicMock())
    await client.login(api_key="k", secret_key="s")
    assert client.connected is True
    await client.logout()
    assert client._logout_requested is True and client.connected is False
    # a recovery firing after logout must NOT bring the session back
    relogin_spy = MagicMock(return_value=[])
    monkeypatch.setattr(client, "_login_sync", relogin_spy)
    client._manager = None
    await client._relogin()
    relogin_spy.assert_not_called()
    assert client.connected is False

async def test_login_resets_logout_requested(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock())
    client._logout_requested = True
    monkeypatch.setattr(client, "_login_sync", MagicMock(return_value=[]))
    await client.login(api_key="k", secret_key="s")
    assert client._logout_requested is False and client.connected is True
    await client.logout()  # clean teardown (stop_keepalive)
```

**Verification:**

Run: `timeout 120 uv run pytest tests/test_session_recovery.py -v`
Expected: prior 15 + 4 new = 19 pass.
Run: `timeout 180 uv run pytest tests/ -q`
Expected: 107 passed; paste tail. `uv run ruff check src tests` clean.

**Also update `docs/ARCHITECTURE.md`:** change the "Known limitation — logout-races-recovery" note from an open limitation to **resolved** (flag-based authority: logout sets `_logout_requested`; `_relogin`/`_handle_session_down` defer to it under `_lock`; no task cancellation, so no orphaned-executor hazard). Keep the optional `POST /api/auth/reconnect` follow-up note.

**Commit:**
```bash
git add src/shioaji_server/session.py tests/test_session_recovery.py docs/ARCHITECTURE.md
git commit -m "fix: make logout authoritative over concurrent session recovery"
```

---

## Notes for the implementer

- **Do not** call `_schedule_reconnect` from the watchdog — it is the cross-thread SDK-callback path. The watchdog is in-loop; use `_schedule_recovery` (`create_task`).
- **Do not** add a Docker HEALTHCHECK or modify `check_session` — explicitly out of scope (design §7).
- The `_reconnecting` guard in `_handle_session_down` makes the watchdog and SDK callback firing together safe — do not add extra locking.
- Keep every test fully offline (`MagicMock` SDK, `AsyncMock`, patched `asyncio.sleep`). No gateway, no network.
- If `git commit` fails with a "claude-fable-5 unavailable / classifier" message, wait briefly and retry the same commit up to 3×; else leave staged and report "commit pending".
