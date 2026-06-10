"""Tests for the contiguous-prefix invariant of ``bars.fetch_stock_bars``.

The download loop must hold a load-bearing guarantee: whatever bars reach the
catalog form a gap-free prefix ``[start, last_bar_date]`` with no error holes in
the middle. Concretely the loop must (a) retry a failing month chunk *in place*
up to ``MAX_CHUNK_ATTEMPTS`` times, (b) stop dead — never skip forward — when a
chunk's attempts are exhausted (``truncated=True``), and (c) treat a genuinely
empty month (pre-listing / halt) as success, not truncation.

All tests run fully offline: a scripted fake ``get_kbars`` client that records
every requested ``(start, end)`` range, and a fake in-memory catalog whose
``write_data`` appends to a list. No real NautilusTrader ``ParquetDataCatalog``
and no network are involved.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol

from shioaji_server.data.bars import (
    BAR_SPEC,
    VENUE,
    BarsFetchOutcome,
    fetch_stock_bars,
)

# --------------------------------------------------------------------------- #
# Fixtures / fakes
# --------------------------------------------------------------------------- #


def _bar_type() -> BarType:
    """Build the 1-minute LAST BarType used throughout the bar pipeline."""
    return BarType(InstrumentId(Symbol("2330"), VENUE), BAR_SPEC)


def _ns_at(d: date) -> int:
    """Nanosecond UTC epoch for 02:00 UTC on ``d``.

    Definition: Map a calendar date to a true-UTC nanosecond timestamp that
                falls inside the TWSE session (01:00–05:30 UTC), so the bar's
                UTC date equals its Taiwan trading date ``d``.
    Formula:    ts_ns = int(datetime(d, 02:00, tzinfo=UTC).timestamp() * 1e9).
    Domain:     ``d`` is any calendar date; 02:00 UTC keeps the bar unambiguously
                on ``d`` (no midnight rollover) and inside the trading session.
    Returns:    Integer nanoseconds since the Unix epoch (the gateway's unit).
    """
    dt = datetime(d.year, d.month, d.day, 2, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _kbar_resp(dates: list[date]) -> dict:
    """Build a kbars response dict (parallel lists) with one bar per date.

    Mirrors how ``kbars_to_bars`` reads the response: keys ``ts``/``open``/
    ``high``/``low``/``close``/``volume`` are parallel lists; ``ts`` are int
    nanoseconds. Prices/volume are arbitrary but valid.
    """
    ts = [_ns_at(d) for d in dates]
    n = len(dates)
    return {
        "ts": ts,
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.5] * n,
        "volume": [1000] * n,
    }


class ScriptedKbarClient:
    """``get_kbars`` driven by a ``{start_str: behavior}`` script; records calls.

    A *behavior* is either a kbars response dict (returned as-is) or a callable
    ``() -> dict`` invoked per call — letting a test script transient failures
    (raise N times, then return) by closing over a mutable counter. Every
    requested ``(start, end)`` pair is appended to ``self.requested`` in order,
    so tests can assert which month ranges were (and were not) fetched.
    """

    def __init__(self, script: dict[str, object]) -> None:
        self._script = script
        self.requested: list[tuple[str, str]] = []

    async def get_kbars(self, code: str, start: str, end: str) -> dict:
        self.requested.append((start, end))
        behavior = self._script[start]
        if callable(behavior):
            return behavior()
        return behavior  # type: ignore[return-value]


class FakeCatalog:
    """In-memory stand-in for ParquetDataCatalog; ``write_data`` appends bars."""

    def __init__(self) -> None:
        self.written: list = []

    def write_data(self, data: list) -> None:
        self.written.extend(data)


# A 3-chunk range: month_ranges splits [2024-01-15, 2024-03-31] into
# Jan(15→31), Feb(01→29), Mar(01→31) — exactly three calendar-month chunks.
START = date(2024, 1, 15)
END = date(2024, 3, 31)
CHUNK1_START = "2024-01-15"
CHUNK2_START = "2024-02-01"
CHUNK3_START = "2024-03-01"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_truncated_chunk_stops_loop_no_month_skip():
    """A permanently-failing chunk halts the loop; later months are never touched."""

    def always_raises() -> dict:
        raise RuntimeError("gateway timeout")

    chunk1 = _kbar_resp([date(2024, 1, 20), date(2024, 1, 22)])
    client = ScriptedKbarClient(
        {
            CHUNK1_START: chunk1,
            CHUNK2_START: always_raises,  # fails on every one of the 3 attempts
            CHUNK3_START: _kbar_resp([date(2024, 3, 5)]),  # must NOT be requested
        }
    )
    catalog = FakeCatalog()

    outcome = await fetch_stock_bars(client, "2330", _bar_type(), START, END, catalog)

    assert isinstance(outcome, BarsFetchOutcome)
    assert outcome.truncated is True
    # Only chunk-1 bars were written (chunk 2 failed, the loop stopped).
    assert outcome.n_bars == 2
    assert len(catalog.written) == 2
    # last_bar_date is the contiguous prefix's last bar (in chunk 1).
    assert outcome.last_bar_date == date(2024, 1, 22)
    # Chunk 2 was attempted exactly MAX_CHUNK_ATTEMPTS (3) times in place...
    assert client.requested.count((CHUNK2_START, "2024-02-29")) == 3
    # ...and chunk 3 was NEVER requested — no forward skip past the failure.
    assert all(start != CHUNK3_START for start, _ in client.requested)


async def test_transient_error_retries_same_chunk():
    """A chunk that fails twice then succeeds is retried in place; all months fetched."""
    calls = {"n": 0}
    chunk1 = _kbar_resp([date(2024, 1, 18)])

    def flaky_chunk1() -> dict:
        calls["n"] += 1
        if calls["n"] < 3:  # raise on attempts 1 and 2
            raise RuntimeError(f"transient {calls['n']}")
        return chunk1  # succeed on attempt 3

    client = ScriptedKbarClient(
        {
            CHUNK1_START: flaky_chunk1,
            CHUNK2_START: _kbar_resp([date(2024, 2, 10)]),
            CHUNK3_START: _kbar_resp([date(2024, 3, 12)]),
        }
    )
    catalog = FakeCatalog()

    outcome = await fetch_stock_bars(client, "2330", _bar_type(), START, END, catalog)

    assert outcome.truncated is False
    assert outcome.n_bars == 3  # one bar per month, all three fetched
    assert outcome.last_bar_date == date(2024, 3, 12)
    # Chunk 1 was requested exactly 3× (2 failures + 1 success), in place.
    assert client.requested.count((CHUNK1_START, "2024-01-31")) == 3
    # All three months ended up requested (chunk 1 thrice, chunks 2 & 3 once).
    requested_starts = {start for start, _ in client.requested}
    assert requested_starts == {CHUNK1_START, CHUNK2_START, CHUNK3_START}


async def test_empty_month_is_not_truncation():
    """An empty month (no ``ts``) is genuine data; the loop continues, not truncates."""
    empty = {
        "ts": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
    }
    client = ScriptedKbarClient(
        {
            CHUNK1_START: _kbar_resp([date(2024, 1, 25)]),
            CHUNK2_START: empty,  # e.g. trading halt / pre-listing month
            CHUNK3_START: _kbar_resp([date(2024, 3, 8)]),
        }
    )
    catalog = FakeCatalog()

    outcome = await fetch_stock_bars(client, "2330", _bar_type(), START, END, catalog)

    assert outcome.truncated is False
    # The empty month wrote nothing but did not stop the loop: chunks 1 and 3 wrote.
    assert outcome.n_bars == 2
    assert outcome.last_bar_date == date(2024, 3, 8)
    # All three months were requested — the empty month did not abort the loop.
    requested_starts = [start for start, _ in client.requested]
    assert requested_starts == [CHUNK1_START, CHUNK2_START, CHUNK3_START]
