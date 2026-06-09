"""Tests for the batch/quota core of ``shioaji_server.data.fetch``.

Covers the two pieces that carry the real concurrency/quota correctness risk:

* :func:`run_batch` — bounded-concurrency orchestration with per-ticker failure
  isolation (one raising ticker must not cancel its siblings; peak in-flight
  workers must never exceed the configured concurrency).
* :class:`QuotaGate` — the shared, throttled quota coordinator: it latches
  ``tripped`` permanently and throttles the ``/api/account/usage`` poll to at
  most once per TTL window (measured on the event-loop clock, never wall time).

All tests run against a fake client that records ``get_usage`` calls and returns
scripted ``remaining_mb`` values — no gateway, no network.
"""

from __future__ import annotations

import asyncio

from shioaji_server.data.fetch import (
    QuotaGate,
    TickerResult,
    format_batch_report,
    run_batch,
)


class FakeUsageClient:
    """A stand-in :class:`ShioajiClient` that scripts ``get_usage`` responses.

    ``remaining_sequence`` supplies one ``remaining_mb`` per call; once exhausted
    the last value repeats. Every call is counted in ``calls`` so tests can
    assert how often the quota endpoint was actually hit.
    """

    def __init__(self, remaining_sequence: list[float]) -> None:
        self._sequence = list(remaining_sequence)
        self.calls = 0

    async def get_usage(self) -> dict:
        self.calls += 1
        idx = min(self.calls - 1, len(self._sequence) - 1)
        remaining = self._sequence[idx]
        return {
            "remaining_mb": remaining,
            "used_mb": 500.0 - remaining,
            "limit_mb": 500.0,
            "remaining_pct": remaining / 500.0 * 100.0,
        }


class FakeClock:
    """A monkeypatchable event-loop stand-in exposing a controllable ``time()``."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now


# --------------------------------------------------------------------------- #
# run_batch
# --------------------------------------------------------------------------- #


async def test_run_batch_isolates_failures():
    """A raising ticker becomes status='failed'; the others still complete."""

    async def per_ticker(code: str) -> TickerResult:
        if code == "BOOM":
            raise RuntimeError("kaboom")
        return TickerResult(code=code, status="complete", n_written=7)

    codes = ["0050", "BOOM", "2330"]
    results = await run_batch(codes, concurrency=4, per_ticker=per_ticker)

    by_code = {r.code: r for r in results}
    assert by_code["0050"].status == "complete"
    assert by_code["2330"].status == "complete"
    assert by_code["BOOM"].status == "failed"
    assert by_code["BOOM"].error == "kaboom"
    # Results preserve input order and cardinality.
    assert [r.code for r in results] == codes


async def test_run_batch_respects_concurrency():
    """Peak simultaneous in-flight workers never exceeds the concurrency bound."""
    concurrency = 3
    live = 0
    peak = 0

    async def per_ticker(code: str) -> TickerResult:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        # Yield control so other workers can interleave under the semaphore;
        # if the bound were broken, peak would climb past `concurrency`.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        live -= 1
        return TickerResult(code=code, status="complete")

    codes = [f"T{i}" for i in range(20)]
    results = await run_batch(codes, concurrency=concurrency, per_ticker=per_ticker)

    assert len(results) == 20
    assert all(r.status == "complete" for r in results)
    assert peak <= concurrency, f"peak in-flight {peak} exceeded bound {concurrency}"


# --------------------------------------------------------------------------- #
# QuotaGate
# --------------------------------------------------------------------------- #


async def test_quota_gate_trips_and_stays(monkeypatch):
    """remaining<min trips the gate; ok() stays False even after quota recovers."""
    clock = FakeClock()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: clock)

    # First poll: 10 MB remaining (< 50 floor) -> trips. Later polls: 400 MB
    # (recovered) — must NOT un-trip.
    client = FakeUsageClient(remaining_sequence=[10.0, 400.0, 400.0])
    gate = QuotaGate(client, min_remaining_mb=50.0, ttl_seconds=10.0)

    assert await gate.ok() is False
    assert gate.tripped is True

    # Advance well past the TTL so a stale-cache re-query *would* fire if the
    # gate were not latched — it must short-circuit before polling.
    clock.now = 1000.0
    assert await gate.ok() is False
    assert await gate.ok() is False
    assert gate.tripped is True
    # Only the initial poll happened; a tripped gate never re-queries usage.
    assert client.calls == 1


async def test_quota_gate_throttles_usage_query(monkeypatch):
    """Within one TTL window, usage is queried exactly once across many ok() calls."""
    clock = FakeClock()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: clock)

    # Healthy quota throughout so the gate never trips and keeps polling subject
    # only to the TTL throttle.
    client = FakeUsageClient(remaining_sequence=[400.0])
    gate = QuotaGate(client, min_remaining_mb=50.0, ttl_seconds=10.0)

    # Many calls inside the same TTL window (clock frozen) -> one poll.
    for _ in range(25):
        assert await gate.ok() is True
    assert client.calls == 1

    # Still inside the window (t=9 < ttl=10) -> no new poll.
    clock.now = 9.0
    assert await gate.ok() is True
    assert client.calls == 1

    # Cross the TTL boundary (t=11 > 10) -> exactly one more poll.
    clock.now = 11.0
    assert await gate.ok() is True
    assert client.calls == 2


# --------------------------------------------------------------------------- #
# format_batch_report (carry-forward: last_date=None must not crash / emit None)
# --------------------------------------------------------------------------- #


def test_format_batch_report_handles_none_last_date():
    """A pre-flight-stopped partial (last_date=None) yields a safe resume hint."""
    from datetime import date

    results = [
        TickerResult(code="0050", status="complete", n_written=100),
        # Pre-flight quota stop: zero days done, last_date is None.
        TickerResult(code="00631L", status="partial", last_date=None),
        # Mid-run quota stop: last_date set -> resume from the next day.
        TickerResult(
            code="2330", status="partial", last_date=date(2024, 3, 2), n_written=50
        ),
        TickerResult(code="2317", status="failed", error="boom"),
    ]
    report = format_batch_report(results)

    assert "1 complete" in report
    assert "2 partial" in report
    assert "1 failed" in report
    # Never emit a literal "--start None".
    assert "--start None" not in report
    # last_date present -> next-day resume.
    assert "--code 2330 --start 2024-03-03" in report
    # last_date None -> placeholder, not a crash.
    assert "--code 00631L --start <original>" in report
    # failed ticker surfaces its error and a resume hint.
    assert "2317" in report and "boom" in report
