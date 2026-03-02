import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

log = logging.getLogger(__name__)


@dataclass
class ConnectionManager:
    """Manages WebSocket connections and routes Shioaji quotes to clients."""

    active_connections: list[WebSocket] = field(default_factory=list)
    subscriptions: dict[tuple[str, str], set[WebSocket]] = field(default_factory=dict)
    _loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active_connections:
            self.active_connections.remove(ws)
        for key in list(self.subscriptions):
            self.subscriptions[key].discard(ws)
            if not self.subscriptions[key]:
                del self.subscriptions[key]

    def subscribe(self, ws: WebSocket, code: str, quote_type: str) -> bool:
        """Returns True if this is a NEW subscription (needs Shioaji subscribe)."""
        key = (code, quote_type)
        is_new = key not in self.subscriptions
        if is_new:
            self.subscriptions[key] = set()
        self.subscriptions[key].add(ws)
        return is_new

    def unsubscribe(self, ws: WebSocket, code: str, quote_type: str) -> bool:
        """Returns True if subscription is now empty (needs Shioaji unsubscribe)."""
        key = (code, quote_type)
        if key in self.subscriptions:
            self.subscriptions[key].discard(ws)
            if not self.subscriptions[key]:
                del self.subscriptions[key]
                return True
        return False

    def broadcast_from_thread(self, code: str, quote_type: str, data: dict) -> None:
        """Called from Shioaji's callback thread. Schedules async broadcast."""
        if self._loop is None:
            return
        message = json.dumps({"type": quote_type, "code": code, "data": data})
        asyncio.run_coroutine_threadsafe(
            self._broadcast(code, quote_type, message), self._loop
        )

    async def _broadcast(self, code: str, quote_type: str, message: str) -> None:
        key = (code, quote_type)
        targets = self.subscriptions.get(key, set())
        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_order_update(self, event_type: str, data: dict) -> None:
        """Broadcast order update to ALL connected clients (from callback thread)."""
        if self._loop is None:
            return
        message = json.dumps({"type": "order_update", "event": event_type, "data": data})
        asyncio.run_coroutine_threadsafe(
            self._broadcast_all(message), self._loop
        )

    async def _broadcast_all(self, message: str) -> None:
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
