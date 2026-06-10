"""Shared 1-min bar download engine for the SinoPac per-instrument scripts.

These are the surviving primitives of the retired universe/bulk downloader
(``fetch_historical``): kbar→``Bar`` conversion, monthly chunking, a data
availability probe, and the per-stock fetch loop. Both ``fetch_single`` (bars)
and ``fetch_single_ticks`` (ticks) import from here — the former for the full
bar pipeline, the latter for the shared ``VENUE`` and ``probe_kbar_availability``
(used as a generic "does this code have data" check before a tick download).

The liquidity-tier universe selection (``build_metadata`` + ``scripts.filters``)
and its ``metadata.parquet`` were dropped: the catalog is built by curating
specific instrument ids, not by scanning the whole market.

**Contiguous-prefix invariant (load-bearing contract).** ``fetch_stock_bars``
downloads the requested range in calendar-month chunks *in order* and NEVER
skips forward past a chunk it could not fetch. A chunk is retried in place up to
``MAX_CHUNK_ATTEMPTS`` times; if all attempts fail the loop stops immediately
and reports ``truncated=True``. The consequence — and the whole point — is that
whatever bars land in the catalog always form a gap-free prefix
``[start, last_bar_date]`` with no error holes in the middle. (An empty month,
e.g. pre-listing or a trading halt, is genuine data, not an error, so the prefix
stays contiguous across it.) Downstream resume logic relies on this: "last bar
date in the catalog + 1 day" is a mathematically safe restart point only because
this invariant holds. The retired bulk downloader's cross-chunk
``MAX_CONSECUTIVE_ERRORS`` counter violated it — after 1–2 transient errors it
``continue``d to the next month, silently leaving month-sized holes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from .client import ShioajiGatewayClient

VENUE = Venue("SINOPAC")
BAR_SPEC = BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)

MAX_CHUNK_ATTEMPTS = 3


@dataclass
class BarsFetchOutcome:
    """Outcome of one fetch_stock_bars run.

    Definition: Truth-carrying result of the monthly-chunk download loop.
    Domain:     ``truncated=True`` means the loop stopped at a chunk that
                failed MAX_CHUNK_ATTEMPTS times; everything BEFORE that chunk
                was fetched (or genuinely empty) — the catalog prefix is
                contiguous. ``last_bar_date`` is the UTC date of the last bar
                actually written (None if nothing was written).
    Returns:    n_bars written this run, last_bar_date, truncated flag.
    """

    n_bars: int
    last_bar_date: date | None
    truncated: bool


def kbars_to_bars(kbar_resp: dict, bar_type: BarType) -> list[Bar]:
    """Convert a kbars API response dict to a list of NT Bar objects."""
    bars: list[Bar] = []
    timestamps = kbar_resp["ts"]
    opens = kbar_resp["open"]
    highs = kbar_resp["high"]
    lows = kbar_resp["low"]
    closes = kbar_resp["close"]
    volumes = kbar_resp["volume"]

    for i in range(len(timestamps)):
        ts_ns = timestamps[i]  # gateway returns nanoseconds
        bar = Bar(
            bar_type=bar_type,
            open=Price(opens[i], precision=2),
            high=Price(highs[i], precision=2),
            low=Price(lows[i], precision=2),
            close=Price(closes[i], precision=2),
            volume=Quantity(volumes[i], precision=0),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        bars.append(bar)
    return bars


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into month-sized chunks."""
    ranges = []
    cursor = start
    while cursor <= end:
        # End of current month
        if cursor.month == 12:
            month_end = date(cursor.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
        chunk_end = min(month_end, end)
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return ranges


async def probe_kbar_availability(
    client: ShioajiGatewayClient, code: str, end: date
) -> bool:
    """Try fetching a recent month to check if kbar data exists for this stock."""
    probe_start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
    try:
        resp = await client.get_kbars(code, probe_start.isoformat(), end.isoformat())
        return len(resp.get("ts", [])) > 0
    except Exception:
        return False


async def fetch_stock_bars(
    client: ShioajiGatewayClient,
    code: str,
    bar_type: BarType,
    start: date,
    end: date,
    catalog: ParquetDataCatalog,
) -> BarsFetchOutcome:
    """Download kbars for one stock in monthly chunks, preserving a contiguous prefix.

    Definition: Fetch ``[start, end]`` in calendar-month chunks (in order) and
                write each month's bars to ``catalog``, stopping at the first
                chunk that cannot be fetched.
    Domain:     Each chunk is retried in place up to ``MAX_CHUNK_ATTEMPTS`` times;
                the loop NEVER skips forward past a failed chunk (no ``continue``
                to the next month on error). An empty month (pre-listing / halt)
                is genuine data, not an error, so the loop keeps going. This
                guarantees the contiguous-prefix invariant documented in the
                module docstring. TWSE session is 01:00–05:30 UTC, so the UTC
                date of a bar equals its Taiwan trading date.
    Returns:    A :class:`BarsFetchOutcome` — bars written this run, the UTC date
                of the last bar written (None if none), and ``truncated`` (True
                when a chunk exhausted its attempts and the loop stopped early).
    """
    total_bars = 0
    last_bar_date: date | None = None

    for chunk_start, chunk_end in month_ranges(start, end):
        start_str = chunk_start.isoformat()
        end_str = chunk_end.isoformat()

        kbar_resp = None
        for attempt in range(1, MAX_CHUNK_ATTEMPTS + 1):
            try:
                kbar_resp = await client.get_kbars(code, start_str, end_str)
                break
            except Exception as e:
                print(
                    f"    ERROR {start_str}→{end_str} "
                    f"(attempt {attempt}/{MAX_CHUNK_ATTEMPTS}): {e!r}"
                )

        if kbar_resp is None:
            print(
                f"    TRUNCATED at {start_str}: {MAX_CHUNK_ATTEMPTS} attempts "
                f"failed — stopping so the catalog prefix stays contiguous"
            )
            return BarsFetchOutcome(total_bars, last_bar_date, truncated=True)

        bars = kbars_to_bars(kbar_resp, bar_type)
        if bars:  # empty month (pre-listing/halt) is NOT an error
            bars.sort(key=lambda b: b.ts_init)
            catalog.write_data(bars)
            total_bars += len(bars)
            last_bar_date = datetime.fromtimestamp(
                bars[-1].ts_init / 1e9, tz=timezone.utc
            ).date()

    return BarsFetchOutcome(total_bars, last_bar_date, truncated=False)


def last_bar_date_in_catalog(
    catalog: ParquetDataCatalog, bar_type: BarType
) -> date | None:
    """
    Definition: UTC date of the newest bar already persisted for *bar_type*.
    Formula:    max(ts_event) over data/bar/{bar_type}/*.parquet, ns→UTC date.
    Domain:     Lazy polars scan — never loads full bar data through NT. The
                returned date is only a safe resume point because
                fetch_stock_bars guarantees a contiguous error-free prefix.
                TWSE bars: UTC date == Taiwan trading date (session 01:00–05:30
                UTC), so day arithmetic needs no tz conversion.
    Returns:    date of last bar, or None when the instrument has no bar data.
    """
    bar_dir = Path(catalog.path) / "data" / "bar" / str(bar_type)
    if not bar_dir.exists() or not any(bar_dir.glob("*.parquet")):
        return None
    max_ns = (
        pl.scan_parquet(str(bar_dir / "*.parquet"))
        .select(pl.col("ts_event").max())
        .collect()
        .item()
    )
    if max_ns is None:
        return None
    return datetime.fromtimestamp(max_ns / 1e9, tz=timezone.utc).date()
