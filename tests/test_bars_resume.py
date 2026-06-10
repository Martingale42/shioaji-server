"""Tests for the contiguous-prefix invariant of ``bars.fetch_stock_bars``.

The download loop must hold a load-bearing guarantee: whatever bars reach the
catalog form a gap-free prefix ``[start, last_bar_date]`` with no error holes in
the middle. Concretely the loop must (a) retry a failing month chunk *in place*
up to ``MAX_CHUNK_ATTEMPTS`` times, (b) stop dead — never skip forward — when a
chunk's attempts are exhausted (``truncated=True``), and (c) treat a genuinely
empty month (pre-listing / halt) as success, not truncation.

All tests run fully offline: a scripted fake ``get_kbars`` client that records
every requested ``(start, end)`` range, and a fake in-memory catalog whose
``write_data`` appends to a list. No network is involved.

``last_bar_date_in_catalog`` tests deliberately use a *real*
``ParquetDataCatalog`` against ``tmp_path`` (not the in-memory fake) to lock in
the on-disk directory-naming assumption ``data/bar/{str(bar_type)}/``: a path
that silently never matches would turn auto-resume into a permanent no-op.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from shioaji_server.data.bars import (
    BAR_SPEC,
    VENUE,
    BarsFetchOutcome,
    fetch_stock_bars,
    last_bar_date_in_catalog,
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


# --------------------------------------------------------------------------- #
# last_bar_date_in_catalog — real ParquetDataCatalog round-trip
# --------------------------------------------------------------------------- #


def _bar_at(d: date, bar_type: BarType) -> Bar:
    """Build a single real NT ``Bar`` on date ``d`` (mirrors ``kbars_to_bars``).

    Definition: Construct an NT ``Bar`` whose ``ts_event``/``ts_init`` is a
                true-UTC nanosecond timestamp at 02:00 UTC on ``d`` — inside the
                TWSE session, so the bar's UTC date equals ``d``.
    Domain:     ``d`` any calendar date; prices/volume arbitrary but valid.
    Returns:    One ``Bar`` ready for ``catalog.write_data``.
    """
    ts_ns = _ns_at(d)
    return Bar(
        bar_type=bar_type,
        open=Price(100.0, precision=2),
        high=Price(101.0, precision=2),
        low=Price(99.0, precision=2),
        close=Price(100.5, precision=2),
        volume=Quantity(1000, precision=0),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def test_last_bar_date_round_trip(tmp_path):
    """A real catalog write is read back: returned date == newest bar's UTC date.

    Locks in the ``data/bar/{str(bar_type)}/*.parquet`` directory-naming
    assumption against the real ``ParquetDataCatalog`` writer.
    """
    bar_type = _bar_type()
    catalog = ParquetDataCatalog(str(tmp_path))

    older = _bar_at(date(2024, 5, 6), bar_type)
    newer = _bar_at(date(2024, 5, 8), bar_type)
    # NT's catalog requires ts_init-non-decreasing input (same reason
    # fetch_stock_bars sorts before write_data), so write ascending. The function
    # under test reads max(ts_event), so the returned date must be the newer bar.
    catalog.write_data([older, newer])

    # Guard: the directory the function scans must actually exist on disk —
    # if str(bar_type) ever stops matching the catalog layout this fails loudly.
    bar_dir = tmp_path / "data" / "bar" / str(bar_type)
    assert bar_dir.is_dir(), f"expected catalog bar dir missing: {bar_dir}"
    assert any(bar_dir.glob("*.parquet"))

    result = last_bar_date_in_catalog(catalog, bar_type)
    assert result == date(2024, 5, 8)


def test_last_bar_date_empty_catalog_is_none(tmp_path):
    """A fresh catalog with no bars for this bar_type returns None (no crash)."""
    bar_type = _bar_type()
    catalog = ParquetDataCatalog(str(tmp_path))

    assert last_bar_date_in_catalog(catalog, bar_type) is None
