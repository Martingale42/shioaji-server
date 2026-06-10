"""Unit tests for WebSocket endpoint resilience (G5 / G6).

Guards two robustness gaps in `websocket_endpoint`:

- **G6** — a single malformed JSON frame must return an error frame and keep the
  connection alive, not drop it.
- **G5** — the backend session being down refuses (un)subscribe without touching
  the SDK; and any non-disconnect exception inside the loop (e.g. a failing
  `resolve_contract`) must NOT leave a zombie connection or an orphaned Shioaji
  subscription — the `finally` block must always clean both up.

These exercise the endpoint directly with a fake WebSocket, so the resilience
guarantee is regression-locked rather than relying on manual checks.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import WebSocketDisconnect

from shioaji_server import app as app_module
from shioaji_server.ws.manager import ConnectionManager


class _FakeWebSocket:
    """Drives `websocket_endpoint`: yields queued frames, then disconnects.

    `receive_text` replays `incoming` in order; once exhausted it raises
    `WebSocketDisconnect` to mimic the client going away and end the loop.
    """

    def __init__(self, sj_client: MagicMock, incoming: list[str]) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(sj=sj_client))
        self._incoming = list(incoming)
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _sj_client(connected: bool = True) -> MagicMock:
    """A mocked ShioajiGatewaySession: awaitable run_sync, no real SDK."""
    client = MagicMock()
    client.connected = connected
    client.run_sync = AsyncMock()
    client.to_quote_type = MagicMock(return_value="tick")
    return client


def _fresh_manager(monkeypatch) -> ConnectionManager:
    """Isolate each test from the module-level manager singleton."""
    mgr = ConnectionManager()
    monkeypatch.setattr(app_module, "manager", mgr)
    return mgr


async def test_bad_json_returns_error_and_keeps_connection(monkeypatch):
    """G6: one malformed frame -> error frame, loop survives to the next frame."""
    mgr = _fresh_manager(monkeypatch)
    sj = _sj_client()
    sj.resolve_contract = MagicMock()
    ws = _FakeWebSocket(sj, [
        "not json",
        json.dumps({"action": "subscribe", "contract_code": "2330", "quote_type": "tick"}),
    ])

    await app_module.websocket_endpoint(ws)

    # First frame -> Invalid JSON error; second frame still processed afterwards.
    assert ws.sent[0] == {"type": "error", "detail": "Invalid JSON"}
    assert any(m.get("type") == "subscribed" for m in ws.sent)
    # No zombie: the finally ran disconnect, the connection list is empty.
    assert mgr.active_connections == []


async def test_subscribe_before_login_is_refused(monkeypatch):
    """G5: with the backend down, a subscribe is refused without any SDK call."""
    mgr = _fresh_manager(monkeypatch)
    sj = _sj_client(connected=False)
    sj.resolve_contract = MagicMock()
    ws = _FakeWebSocket(sj, [
        json.dumps({"action": "subscribe", "contract_code": "2330", "quote_type": "tick"}),
    ])

    await app_module.websocket_endpoint(ws)

    assert {"type": "error", "detail": "gateway not connected"} in ws.sent
    sj.resolve_contract.assert_not_called()  # never reached the SDK
    assert mgr.subscriptions == {}           # no WS-side ghost subscription recorded
    assert mgr.active_connections == []       # cleaned up on exit


async def test_action_error_does_not_leak_zombie_or_orphan(monkeypatch):
    """G5: a failing resolve_contract mid-subscribe -> error frame, loop survives,
    and the finally releases BOTH the WS connection and the orphaned Shioaji key.

    This is the core G5 guarantee: an SDK error after `manager.subscribe` already
    recorded the key must not strand a zombie connection or an orphan feed.
    """
    mgr = _fresh_manager(monkeypatch)
    sj = _sj_client()
    sj.resolve_contract = MagicMock(side_effect=ValueError("unknown contract"))
    ws = _FakeWebSocket(sj, [
        json.dumps({"action": "subscribe", "contract_code": "BADCODE", "quote_type": "tick"}),
    ])

    await app_module.websocket_endpoint(ws)

    # The action failed gracefully with an error frame — the loop was not killed.
    assert any(
        m.get("type") == "error" and "failed" in m.get("detail", "")
        for m in ws.sent
    )
    # No zombie connection, no orphaned subscription left behind.
    assert mgr.active_connections == []
    assert mgr.subscriptions == {}
    # The orphaned ("BADCODE","tick") key was handed to the SDK cleanup path,
    # which re-resolves the contract (resolve_contract called again in _unsubscribe_orphaned).
    assert sj.resolve_contract.call_count >= 2
