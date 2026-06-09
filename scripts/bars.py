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
"""

from __future__ import annotations

from datetime import date, timedelta

from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.client import ShioajiClient

VENUE = Venue("SINOPAC")
BAR_SPEC = BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)

MAX_CONSECUTIVE_ERRORS = 3


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
    client: ShioajiClient, code: str, end: date
) -> bool:
    """Try fetching a recent month to check if kbar data exists for this stock."""
    probe_start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
    try:
        resp = await client.get_kbars(code, probe_start.isoformat(), end.isoformat())
        return len(resp.get("ts", [])) > 0
    except Exception:
        return False


async def fetch_stock_bars(
    client: ShioajiClient,
    code: str,
    bar_type: BarType,
    start: date,
    end: date,
    catalog: ParquetDataCatalog,
) -> int:
    """Download kbars for one stock in monthly chunks and write to catalog."""
    total_bars = 0
    consecutive_errors = 0
    chunks = month_ranges(start, end)

    for chunk_start, chunk_end in chunks:
        start_str = chunk_start.isoformat()
        end_str = chunk_end.isoformat()

        try:
            kbar_resp = await client.get_kbars(code, start_str, end_str)
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors <= 2:
                print(f"    ERROR {start_str}→{end_str}: {e!r}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"    SKIP: {MAX_CONSECUTIVE_ERRORS} consecutive errors")
                break
            continue

        consecutive_errors = 0
        bars = kbars_to_bars(kbar_resp, bar_type)
        if bars:
            bars.sort(key=lambda b: b.ts_init)
            catalog.write_data(bars)
            total_bars += len(bars)

    return total_bars
