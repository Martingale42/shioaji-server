"""Regression tests for Solace session self-healing (ShioajiClient).

Root cause being guarded: the gateway never registered
``set_session_down_callback``, so a dropped backend session never recovered —
every API returned 500 (SessionNotEstablished) until a manual restart. These
tests verify the session_down -> re-login -> re-register -> re-subscribe path,
plus backoff and concurrency collapse.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from shioaji_server.client import ShioajiClient


def _down_client() -> ShioajiClient:
    """A client with stored credentials, simulating a dropped session."""
    client = ShioajiClient(api=MagicMock())
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
    client = ShioajiClient(api=MagicMock())
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
