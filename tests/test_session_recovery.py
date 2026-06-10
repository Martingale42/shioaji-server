"""Regression tests for Solace session self-healing (ShioajiGatewaySession).

Root cause being guarded: the gateway never registered
``set_session_down_callback``, so a dropped backend session never recovered —
every API returned 500 (SessionNotEstablished) until a manual restart. These
tests verify the session_down -> re-login -> re-register -> re-subscribe path,
plus backoff and concurrency collapse.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from shioaji_server.session import ShioajiGatewaySession


def _down_client() -> ShioajiGatewaySession:
    """A client with stored credentials, simulating a dropped session."""
    client = ShioajiGatewaySession(api=MagicMock())
    client.connected = False
    client._login_kwargs = {
        "api_key": "k",
        "secret_key": "s",
        "ca_path": None,
        "ca_passwd": None,
        "simulation": True,
    }
    return client


async def test_register_callbacks_wires_session_down():
    """The actual root-cause fix: session_down + event callbacks get registered."""
    client = ShioajiGatewaySession(api=MagicMock())
    manager = MagicMock()
    client.register_callbacks(manager)
    client.api.set_session_down_callback.assert_called_once()
    client.api.quote.set_event_callback.assert_called_once()
    assert client._manager is manager


async def test_relogin_restores_connection_and_reregisters(monkeypatch):
    client = _down_client()
    monkeypatch.setattr(client, "_login_sync", MagicMock(return_value=[]))
    client._manager = MagicMock()
    client._manager.subscriptions = {}
    reg = MagicMock()
    monkeypatch.setattr(client, "register_callbacks", reg)
    await client._relogin()
    assert client.connected is True
    assert client._session_ok is True
    reg.assert_called_once_with(client._manager)


async def test_relogin_without_credentials_raises():
    client = _down_client()
    client._login_kwargs = None
    raised = False
    try:
        await client._relogin()
    except RuntimeError:
        raised = True
    assert raised


async def test_resubscribe_restores_all_streams(monkeypatch):
    client = _down_client()
    client._manager = MagicMock()
    client._manager.subscriptions = {
        ("2330", "tick"): set(),
        ("0050", "bidask"): set(),
    }
    monkeypatch.setattr(client, "resolve_contract", lambda code: f"contract:{code}")
    calls = []

    async def fake_run_sync(fn, *args):
        calls.append(fn)

    monkeypatch.setattr(client, "run_sync", fake_run_sync)
    await client._resubscribe()
    assert len(calls) == 2  # both tracked streams re-subscribed


async def test_session_down_retries_until_success(monkeypatch):
    """Backoff loop keeps retrying re-login until the backend recovers."""
    client = _down_client()
    attempts = {"n": 0}

    async def flaky_relogin():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("backend unavailable")

    monkeypatch.setattr(client, "_relogin", flaky_relogin)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # skip real backoff waits
    await client._handle_session_down()
    assert attempts["n"] == 3
    assert client._reconnecting is False  # flag cleared after recovery


async def test_concurrent_session_down_collapses_to_one():
    """Two overlapping session-down events trigger a single recovery."""
    client = _down_client()
    attempts = {"n": 0}

    async def slow_relogin():
        attempts["n"] += 1
        await asyncio.sleep(0.02)

    client._relogin = slow_relogin
    await asyncio.gather(
        client._handle_session_down(),
        client._handle_session_down(),
    )
    assert attempts["n"] == 1  # second concurrent down collapsed into the first


async def test_resubscribe_retries_until_contracts_ready(monkeypatch):
    """After re-login the contract table may still be loading; resolve_contract
    transiently fails then succeeds — the stream must be retried, not dropped.
    """
    client = _down_client()
    client.resubscribe_max_attempts = 4
    client._manager = MagicMock()
    client._manager.subscriptions = {("2330", "tick"): set()}
    calls = {"resolve": 0, "sub": 0}

    def flaky_resolve(code):
        calls["resolve"] += 1
        if calls["resolve"] < 3:
            raise ValueError("Contract not found")  # contracts still loading
        return f"contract:{code}"

    async def fake_run_sync(fn, *args):
        calls["sub"] += 1

    monkeypatch.setattr(client, "resolve_contract", flaky_resolve)
    monkeypatch.setattr(client, "run_sync", fake_run_sync)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # skip the 1s retry waits
    await client._resubscribe()
    assert calls["resolve"] == 3  # retried until contracts became ready
    assert calls["sub"] == 1  # subscribed exactly once after success


async def test_relogin_holds_lock_serializing_with_login(monkeypatch):
    """_relogin must hold _lock so it cannot rebuild self.api concurrently with
    login() (which would orphan a Solace connection and leak it).
    """
    client = _down_client()
    monkeypatch.setattr(client, "_login_sync", MagicMock(return_value=[]))
    client._manager = None  # skip re-register/resubscribe for this check

    # Hold _lock; _relogin must block until released.
    await client._lock.acquire()
    task = asyncio.create_task(client._relogin())
    await asyncio.sleep(0.02)
    assert not task.done()  # blocked on _lock — proves serialization
    client._lock.release()
    await task
    assert client.connected is True


async def test_keepalive_tick_triggers_recovery_after_2_consecutive_fails(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock())
    client.connected = True
    monkeypatch.setattr(client, "check_session", AsyncMock(return_value=False))
    spy = MagicMock()
    monkeypatch.setattr(client, "_schedule_recovery", spy)
    fails = await client._keepalive_tick(0)  # 1st fail
    assert fails == 1 and spy.call_count == 0
    fails = await client._keepalive_tick(fails)  # 2nd consecutive fail
    assert fails == 0 and spy.call_count == 1  # fired, counter reset


async def test_keepalive_tick_resets_on_success(monkeypatch):
    # probe sequence False, True, False → never reaches threshold, never fires
    client = ShioajiGatewaySession(api=MagicMock())
    client.connected = True
    monkeypatch.setattr(
        client, "check_session", AsyncMock(side_effect=[False, True, False])
    )
    spy = MagicMock()
    monkeypatch.setattr(client, "_schedule_recovery", spy)
    f = await client._keepalive_tick(0)  # False → 1
    f = await client._keepalive_tick(f)  # True  → 0
    f = await client._keepalive_tick(f)  # False → 1
    assert f == 1 and spy.call_count == 0


async def test_keepalive_tick_skips_when_not_connected(monkeypatch):
    client = ShioajiGatewaySession(api=MagicMock())
    client.connected = False
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(client, "check_session", probe)
    spy = MagicMock()
    monkeypatch.setattr(client, "_schedule_recovery", spy)
    assert await client._keepalive_tick(1) == 1  # unchanged
    probe.assert_not_awaited()  # never probed
    assert spy.call_count == 0
