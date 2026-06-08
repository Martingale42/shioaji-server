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
class ShioajiClient:
    """Wraps a single Shioaji SDK instance."""

    api: sj.Shioaji = field(default_factory=sj.Shioaji)
    connected: bool = False
    simulation: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_probe_ttl: float = 5.0
    session_probe_timeout: float = 5.0
    reconnect_max_backoff: float = 60.0
    _session_ok: bool = False
    _session_checked_at: float = -1.0
    _login_kwargs: dict | None = None
    _manager: ConnectionManager | None = None
    _reconnecting: bool = False
    _reconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
            return accounts

    def _logout_sync(self) -> None:
        self.api.logout()

    async def logout(self) -> None:
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
            do not exhaust the accounting-query rate limit (25 req / 5 s). The probe
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
        self.connected = False
        self._session_ok = False
        backoff = 1.0
        try:
            while True:
                try:
                    await self._relogin()
                    log.info("[shioaji-server] Session recovered via re-login")
                    return
                except Exception:
                    log.exception(
                        "[shioaji-server] Re-login failed; retrying in %.0fs", backoff
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.reconnect_max_backoff)
        finally:
            self._reconnecting = False

    async def _relogin(self) -> None:
        """Re-establish the session from stored credentials and restore streams."""
        if self._login_kwargs is None:
            raise RuntimeError("No stored credentials for re-login")
        loop = asyncio.get_running_loop()
        # Best-effort release of the dead session to avoid leaking connections
        # (Shioaji caps at 5 per person); a dead-session logout may error — ignore.
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, self.api.logout), timeout=5.0
            )
        except Exception:
            pass
        kw = self._login_kwargs
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
        if self._manager is not None:
            self.register_callbacks(self._manager)
            await self._resubscribe()

    async def _resubscribe(self) -> None:
        """Re-subscribe all quote streams the WS manager still tracks."""
        if self._manager is None:
            return
        keys = list(self._manager.subscriptions.keys())
        if not keys:
            return
        log.info("[shioaji-server] Re-subscribing %d quote stream(s)", len(keys))
        for code, quote_type in keys:
            try:
                contract = self.resolve_contract(code)
                qt = self.to_quote_type(quote_type)
                await self.run_sync(
                    partial(self.api.quote.subscribe, contract, quote_type=qt)
                )
            except Exception:
                log.exception(
                    "[shioaji-server] Re-subscribe failed for %s/%s", code, quote_type
                )

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
