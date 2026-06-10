from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

import shioaji as sj

if TYPE_CHECKING:
    from shioaji_server.ws.manager import ConnectionManager

log = logging.getLogger(__name__)


@dataclass
class ShioajiGatewaySession:
    """Wraps a single Shioaji SDK instance."""

    api: sj.Shioaji = field(default_factory=sj.Shioaji)
    connected: bool = False
    simulation: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_probe_ttl: float = 5.0
    session_probe_timeout: float = 5.0
    reconnect_max_backoff: float = 60.0
    resubscribe_max_attempts: int = 5
    reconnect_warn_after: int = 5
    _session_ok: bool = False
    _session_checked_at: float = -1.0
    _login_kwargs: dict | None = None
    _manager: ConnectionManager | None = None
    _reconnecting: bool = False
    _reconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    keepalive_interval: float = 5.0
    keepalive_fail_threshold: int = 2
    _keepalive_task: asyncio.Task | None = None
    _recovery_task: asyncio.Task | None = None

    def _login_sync(
        self,
        api_key: str,
        secret_key: str,
        ca_path: str | None = None,
        ca_passwd: str | None = None,
        simulation: bool = False,
    ) -> list[dict]:
        """Synchronous login — run via executor to avoid blocking the loop."""
        self.api = sj.Shioaji(simulation=simulation)

        accounts = self.api.login(api_key=api_key, secret_key=secret_key)

        if ca_path is not None and ca_passwd is not None:
            self.api.activate_ca(ca_path=ca_path, ca_passwd=ca_passwd)

        return [
            {"account_type": str(type(a).__name__), "account_id": a.account_id}
            for a in accounts
        ]

    async def login(
        self,
        api_key: str,
        secret_key: str,
        ca_path: str | None = None,
        ca_passwd: str | None = None,
        simulation: bool = False,
    ) -> list[dict]:
        """Login and optionally activate CA certificate."""
        async with self._lock:
            if self.connected:
                raise RuntimeError("Already connected")
            loop = asyncio.get_running_loop()
            accounts = await loop.run_in_executor(
                None,
                self._login_sync,
                api_key,
                secret_key,
                ca_path,
                ca_passwd,
                simulation,
            )
            self.connected = True
            self.simulation = simulation
            self._session_ok = True
            self._session_checked_at = -1.0
            # Remember credentials so the session_down handler can re-login.
            self._login_kwargs = {
                "api_key": api_key,
                "secret_key": secret_key,
                "ca_path": ca_path,
                "ca_passwd": ca_passwd,
                "simulation": simulation,
            }
            # Start the silent-death watchdog now that the session is fully
            # established (connected + stored credentials), so a probe failure
            # can fire recovery.
            self.start_keepalive()
            return accounts

    def _logout_sync(self) -> None:
        self.api.logout()

    async def logout(self) -> None:
        # Halt the watchdog before anything else so a logout always stops
        # probing, regardless of the connected check below.
        await self.stop_keepalive()
        async with self._lock:
            if not self.connected:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._logout_sync)
            self.connected = False

    def require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("Not connected — call /api/auth/login first")

    async def run_sync(self, fn, *args):
        """Run a blocking SDK call in the executor to avoid blocking the loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def check_session(self, force: bool = False) -> bool:
        """Probe the live Shioaji backend session, not just the login flag.

        Definition: Verifies the backend Solace session actually responds by
            issuing a lightweight ``api.usage()`` call, instead of trusting the
            ``connected`` login flag — which stays True even after the backend
            session silently drops (e.g. overnight), at which point every real
            call raises ``ShioajiConnectionError ... SessionNotEstablished``.
        Domain:     Returns False immediately when not logged in. The probe result
            is cached for ``session_probe_ttl`` seconds so frequent health checks
            do not exhaust the accounting-query rate limit (25 req / 5 s). A
            single-flight ``_probe_lock`` collapses concurrent callers onto one
            in-flight ``usage`` call (others await it and read the fresh cache),
            so a burst of health checks cannot multiply the quota cost. The probe
            runs in the executor under a ``session_probe_timeout`` ceiling so a hung
            backend cannot block the event loop. Pass ``force=True`` to bypass cache.
        Returns:    True if the backend session is responsive, False otherwise.
        """
        if not self.connected:
            return False
        now = time.monotonic()
        if (
            not force
            and self._session_checked_at >= 0.0
            and (now - self._session_checked_at) < self.session_probe_ttl
        ):
            return self._session_ok
        async with self._probe_lock:
            # Re-check the cache inside the lock: while we waited for it, a
            # concurrent caller may have just refreshed the probe, so we can
            # serve its result without firing another usage() call.
            now = time.monotonic()
            if (
                not force
                and self._session_checked_at >= 0.0
                and (now - self._session_checked_at) < self.session_probe_ttl
            ):
                return self._session_ok
            try:
                await asyncio.wait_for(
                    self.run_sync(self.api.usage),
                    timeout=self.session_probe_timeout,
                )
                self._session_ok = True
            except Exception:
                self._session_ok = False
            self._session_checked_at = time.monotonic()
            return self._session_ok

    def resolve_contract(self, code: str):
        """Resolve a contract by code: stocks, then futures, then options."""
        for source in (
            self.api.Contracts.Stocks,
            self.api.Contracts.Futures,
            self.api.Contracts.Options,
        ):
            contract = source.get(code)
            if contract:
                return contract
        raise ValueError(f"Contract {code} not found")

    @staticmethod
    def to_quote_type(quote_type: str) -> sj.constant.QuoteType:
        if quote_type == "tick":
            return sj.constant.QuoteType.Tick
        return sj.constant.QuoteType.BidAsk

    def _schedule_reconnect(self) -> None:
        """Shioaji session_down callback — runs on the SDK's callback thread.

        Kept tiny and non-blocking: it only schedules the async recovery onto the
        event loop (the SDK thread must not run the event loop's coroutines).
        """
        log.warning("[shioaji-server] Solace session DOWN — scheduling re-login")
        loop = self._manager._loop if self._manager else None
        if loop is None:
            log.error("[shioaji-server] No event loop available to recover session")
            return
        asyncio.run_coroutine_threadsafe(self._handle_session_down(), loop)

    def _schedule_recovery(self) -> None:
        """Fire the existing recovery from within the event loop.

        Unlike ``_schedule_reconnect`` (the SDK callback, which runs on the SDK's
        thread and must use run_coroutine_threadsafe), the watchdog runs in the
        loop, so create_task is correct. Both paths converge on the same
        ``_handle_session_down``, whose ``_reconnecting`` guard collapses concurrent
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

    async def _handle_session_down(self) -> None:
        """Recover a dropped Solace session: re-login, re-register, re-subscribe.

        Serialized via ``_reconnect_lock`` so concurrent session-down events
        collapse into a single recovery. Retries with exponential backoff (capped
        at ``reconnect_max_backoff``) so a still-unavailable backend does not cause
        a re-login storm.
        """
        async with self._reconnect_lock:
            if self._reconnecting:
                return
            self._reconnecting = True
            # Mark the session unusable inside the lock, before any await, so no
            # request slips through require_connected() onto the dead session.
            self.connected = False
            self._session_ok = False
        backoff = 1.0
        attempts = 0
        try:
            while True:
                try:
                    await self._relogin()
                    log.info(
                        "[shioaji-server] Session recovered via re-login "
                        "(after %d failed attempt(s))",
                        attempts,
                    )
                    return
                except Exception:
                    attempts += 1
                    # Don't give up (the cause may be transient — IP whitelist,
                    # backend maintenance), but escalate to CRITICAL so the
                    # operator is alerted instead of silently spinning forever.
                    if attempts == self.reconnect_warn_after:
                        log.critical(
                            "[shioaji-server] Re-login still failing after %d "
                            "attempts — check credentials / IP whitelist / "
                            "backend status",
                            attempts,
                        )
                    log.exception(
                        "[shioaji-server] Re-login failed (attempt %d); "
                        "retrying in %.0fs",
                        attempts,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.reconnect_max_backoff)
        finally:
            self._reconnecting = False

    async def _relogin(self) -> None:
        """Re-establish the session from stored credentials and restore streams."""
        if self._login_kwargs is None:
            raise RuntimeError("No stored credentials for re-login")
        kw = self._login_kwargs
        # Hold _lock for the whole tear-down + rebuild so a concurrent manual
        # login() (which also holds _lock and rebuilds self.api) cannot run in
        # parallel — otherwise both create a new sj.Shioaji() and the orphaned
        # one leaks a Solace connection (Shioaji caps at 5 per person).
        async with self._lock:
            loop = asyncio.get_running_loop()
            # Best-effort release of the dead session; a dead-session logout may
            # error or hang — bound it and ignore failures.
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, self.api.logout), timeout=5.0
                )
            except Exception:
                pass
            await loop.run_in_executor(
                None,
                self._login_sync,
                kw["api_key"],
                kw["secret_key"],
                kw["ca_path"],
                kw["ca_passwd"],
                kw["simulation"],
            )
            self.connected = True
            self._session_ok = True
            self._session_checked_at = -1.0
        # Re-register + re-subscribe outside _lock (they don't rebuild the SDK).
        if self._manager is not None:
            self.register_callbacks(self._manager)
            await self._resubscribe()

    async def _resubscribe(self) -> None:
        """Re-subscribe all quote streams the WS manager still tracks.

        After a fresh login the SDK contract table may still be loading, so
        ``resolve_contract`` can transiently fail. Each stream is retried up to
        ``resubscribe_max_attempts`` times (spaced 1s) to let contracts populate;
        a stream that still fails is logged at ERROR (not silently dropped) so the
        lost feed is visible.
        """
        if self._manager is None:
            return
        keys = list(self._manager.subscriptions.keys())
        if not keys:
            return
        log.info("[shioaji-server] Re-subscribing %d quote stream(s)", len(keys))
        for code, quote_type in keys:
            for attempt in range(1, self.resubscribe_max_attempts + 1):
                try:
                    contract = self.resolve_contract(code)
                    qt = self.to_quote_type(quote_type)
                    await self.run_sync(
                        partial(self.api.quote.subscribe, contract, quote_type=qt)
                    )
                    break
                except Exception:
                    if attempt == self.resubscribe_max_attempts:
                        log.error(
                            "[shioaji-server] Re-subscribe FAILED after %d attempts "
                            "for %s/%s — feed will be missing until next action",
                            attempt,
                            code,
                            quote_type,
                        )
                    else:
                        await asyncio.sleep(1.0)

    def register_callbacks(self, manager: ConnectionManager) -> None:
        """Register Shioaji quote + order callbacks that route to WS manager."""
        self._manager = manager

        # Stock tick
        def on_tick_stk(exchange, tick):
            manager.broadcast_from_thread(tick.code, "tick", {
                "close": float(tick.close),
                "volume": int(tick.volume),
                "total_volume": int(tick.total_volume),
                "tick_type": int(tick.tick_type),
                "bid_side_total_vol": int(tick.bid_side_total_vol),
                "ask_side_total_vol": int(tick.ask_side_total_vol),
                "avg_price": float(tick.avg_price),
                "open": float(tick.open),
                "high": float(tick.high),
                "low": float(tick.low),
                "amount": float(tick.amount),
                "pct_chg": float(tick.pct_chg),
                "timestamp": str(tick.datetime),
            })

        # Stock bidask
        def on_bidask_stk(exchange, bidask):
            manager.broadcast_from_thread(bidask.code, "bidask", {
                "bid_price": [float(p) for p in bidask.bid_price],
                "bid_volume": [int(v) for v in bidask.bid_volume],
                "ask_price": [float(p) for p in bidask.ask_price],
                "ask_volume": [int(v) for v in bidask.ask_volume],
                "timestamp": str(bidask.datetime),
            })

        # Futures/options tick
        def on_tick_fop(exchange, tick):
            manager.broadcast_from_thread(tick.code, "tick", {
                "close": float(tick.close),
                "volume": int(tick.volume),
                "total_volume": int(tick.total_volume),
                "underlying_price": float(tick.underlying_price),
                "bid_side_total_vol": int(tick.bid_side_total_vol),
                "ask_side_total_vol": int(tick.ask_side_total_vol),
                "open": float(tick.open),
                "high": float(tick.high),
                "low": float(tick.low),
                "timestamp": str(tick.datetime),
            })

        # Futures/options bidask
        def on_bidask_fop(exchange, bidask):
            manager.broadcast_from_thread(bidask.code, "bidask", {
                "bid_price": [float(p) for p in bidask.bid_price],
                "bid_volume": [int(v) for v in bidask.bid_volume],
                "ask_price": [float(p) for p in bidask.ask_price],
                "ask_volume": [int(v) for v in bidask.ask_volume],
                "timestamp": str(bidask.datetime),
            })

        # Order/deal callback
        def on_order(stat, msg):
            manager.broadcast_order_update(str(stat), msg)

        self.api.quote.set_on_tick_stk_v1_callback(on_tick_stk)
        self.api.quote.set_on_bidask_stk_v1_callback(on_bidask_stk)
        self.api.quote.set_on_tick_fop_v1_callback(on_tick_fop)
        self.api.quote.set_on_bidask_fop_v1_callback(on_bidask_fop)
        self.api.set_order_callback(on_order)

        # Self-healing: recover the session when the Solace backend drops it.
        # This is the missing hook — without it a dropped session never recovers.
        self.api.set_session_down_callback(self._schedule_reconnect)

        def on_event(resp_code: int, event_code: int, info: str, event: str) -> None:
            # 12 = reconnecting, 13 = reconnected (Solace transient blips)
            level = logging.INFO if event_code in (12, 13) else logging.DEBUG
            log.log(level, "[shioaji-server] Solace event %d: %s", event_code, event)

        self.api.quote.set_event_callback(on_event)
