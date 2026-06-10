"""Unit tests for the live backend session probe (ShioajiGatewaySession.check_session).

Guards the false-positive bug where /api/health reported connected:true while
the Shioaji backend Solace session had silently dropped (ShioajiConnectionError
with SubCode(SessionNotEstablished)). The probe must reflect the real backend
state, not merely the login flag.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from shioaji_server.session import ShioajiGatewaySession


def _client(connected: bool = True) -> ShioajiGatewaySession:
    """A ShioajiGatewaySession with a mocked SDK (no real Shioaji instance/login)."""
    client = ShioajiGatewaySession(api=MagicMock())
    client.connected = connected
    return client


async def test_not_logged_in_is_not_alive():
    client = _client(connected=False)
    assert await client.check_session(force=True) is False
    client.api.usage.assert_not_called()  # no backend call when not logged in


async def test_live_session_is_alive():
    client = _client()
    client.api.usage.return_value = MagicMock()  # usage() succeeds
    assert await client.check_session(force=True) is True
    client.api.usage.assert_called_once()


async def test_dead_backend_is_not_alive():
    """Regression: login flag True but backend session dropped -> report False.

    Reproduces the exact failure mode: usage() raises NotReady /
    SessionNotEstablished. Before the fix, /api/health trusted `connected`
    and falsely reported healthy, so scheduled jobs ran against a dead session.
    """
    client = _client()
    client.api.usage.side_effect = Exception(
        "Session error code: NotReady, SubCode(SessionNotEstablished)"
    )
    assert await client.check_session(force=True) is False


async def test_probe_result_is_cached_within_ttl():
    """Frequent health checks must not hammer the accounting rate limit (25/5s)."""
    client = _client()
    client.api.usage.return_value = MagicMock()
    await client.check_session(force=True)  # primes cache (1 probe)
    await client.check_session()  # within TTL -> served from cache
    await client.check_session()  # still cached
    client.api.usage.assert_called_once()


async def test_force_bypasses_cache():
    client = _client()
    client.api.usage.return_value = MagicMock()
    await client.check_session(force=True)
    await client.check_session(force=True)
    assert client.api.usage.call_count == 2


async def test_concurrent_probes_are_single_flight():
    """A burst of concurrent check_session() must fire usage() only once.

    Regression for the unguarded-probe quota leak: without the in-flight
    _probe_lock, N concurrent callers each issued api.usage(), multiplying the
    accounting-query cost (limit 25/5s). The lock collapses them onto one probe;
    the rest await it and read the freshly-cached result.
    """
    client = _client()

    # Make the probe slow enough that all callers pile up on the lock before
    # the first one finishes and populates the cache (runs in the executor).
    def slow_usage():
        time.sleep(0.05)
        return MagicMock()

    client.api.usage.side_effect = slow_usage

    results = await asyncio.gather(*(client.check_session() for _ in range(10)))

    assert all(results)  # all callers see a live session
    client.api.usage.assert_called_once()  # but only one real probe fired
