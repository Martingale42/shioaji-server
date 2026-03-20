"""Fetch historical trade tick data for a single stock.

Usage::

    cd shioaji-server
    uv run python -m scripts.fetch_single_ticks --code 2330 --catalog-path ./catalog

Quota-aware: checks ``/api/account/usage`` and stops when remaining bytes
drop below ``--min-remaining-mb`` (default 50 MB).  Supports ``--start``
resume so you can pick up where yesterday's run left off.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.client import ShioajiClient
from scripts.fetch_historical import VENUE, probe_kbar_availability
from scripts.fetch_single import make_equity

MAX_CONSECUTIVE_ERRORS = 5
MAX_CONSECUTIVE_EMPTY = 10
USAGE_CHECK_INTERVAL = 20  # check quota every N days


def _tick_type_to_aggressor(tick_type: int) -> AggressorSide:
    """Map Shioaji tick_type to NT AggressorSide.

    Shioaji: 1 = 外盤 (buy-initiated), 2 = 內盤 (sell-initiated), 0 = unknown.
    """
    if tick_type == 1:
        return AggressorSide.BUYER
    elif tick_type == 2:
        return AggressorSide.SELLER
    return AggressorSide.NO_AGGRESSOR


_TW = timezone(timedelta(hours=8))


def _ns_to_trade_id(code: str, ts_ns: int) -> TradeId:
    """Build TradeId matching the live Shioaji adapter convention: ``{code}-{datetime}``.

    Live example: ``2330-2026-03-11 13:27:34.156677``
    The Rust pyo3 adapter constructs this from ``tick.code`` + ``tick.datetime``
    (microsecond-precision, Asia/Taipei wall-clock time).
    """
    dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=_TW)
    # Match the microsecond str format produced by Python's ``str(tick.datetime)``
    dt_str = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    return TradeId(f"{code}-{dt_str}")


def ticks_to_trade_ticks(
    tick_resp: dict, instrument_id: InstrumentId
) -> list[TradeTick]:
    """Convert a ticks API response dict to a list of NT TradeTick objects."""
    code = instrument_id.symbol.value
    timestamps = tick_resp["ts"]
    closes = tick_resp["close"]
    volumes = tick_resp["volume"]
    tick_types = tick_resp["tick_type"]

    trade_ticks: list[TradeTick] = []
    for i in range(len(timestamps)):
        ts_ns = timestamps[i]
        trade_ticks.append(
            TradeTick(
                instrument_id=instrument_id,
                price=Price(closes[i], precision=2),
                size=Quantity(volumes[i], precision=0),
                aggressor_side=_tick_type_to_aggressor(tick_types[i]),
                trade_id=_ns_to_trade_id(code, ts_ns),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
        )
    return trade_ticks


def trading_days(start: date, end: date) -> list[date]:
    """Generate weekdays between start and end (inclusive)."""
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:  # Mon-Fri
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


async def check_quota(client: ShioajiClient, min_remaining_mb: float) -> bool:
    """Return True if enough quota remains, False to stop."""
    try:
        usage = await client.get_usage()
        remaining_mb = usage["remaining_mb"]
        used_mb = usage["used_mb"]
        limit_mb = usage["limit_mb"]
        pct = usage["remaining_pct"]
        print(f"    quota: {used_mb:.1f} / {limit_mb:.1f} MB used ({remaining_mb:.1f} MB remaining, {pct:.1f}%)")
        if remaining_mb < min_remaining_mb:
            print(f"    STOP: remaining {remaining_mb:.1f} MB < threshold {min_remaining_mb:.1f} MB")
            return False
        return True
    except Exception as e:
        print(f"    WARNING: quota check failed: {e!r}")
        return True  # continue on failure — don't block fetch for a monitoring issue


async def fetch_stock_ticks(
    client: ShioajiClient,
    code: str,
    instrument_id: InstrumentId,
    start: date,
    end: date,
    catalog: ParquetDataCatalog,
    min_remaining_mb: float,
) -> tuple[int, date | None]:
    """Download ticks for one stock day-by-day and write to catalog.

    Returns (total_ticks, last_date_fetched).
    """
    total_ticks = 0
    consecutive_errors = 0
    consecutive_empty = 0
    days = trading_days(start, end)
    last_date: date | None = None
    days_with_data = 0
    empty_days = 0

    for i, day in enumerate(days):
        # Periodic quota check
        if i > 0 and i % USAGE_CHECK_INTERVAL == 0:
            if not await check_quota(client, min_remaining_mb):
                print(f"    Quota exhausted at {day}. Resume with --start {day}")
                break

        day_str = day.isoformat()
        try:
            tick_resp = await client.get_ticks(code, day_str)
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors <= 2:
                print(f"    ERROR {day_str}: {e!r}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"    SKIP: {MAX_CONSECUTIVE_ERRORS} consecutive errors at {day}")
                break
            continue

        consecutive_errors = 0
        ts_list = tick_resp.get("ts", [])
        if not ts_list:
            consecutive_empty += 1
            empty_days += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print(f"    STOP: {MAX_CONSECUTIVE_EMPTY} consecutive empty weekdays "
                      f"({day - timedelta(days=MAX_CONSECUTIVE_EMPTY * 2)} → {day}). "
                      f"Likely quota exhausted. Resume with --start {day}")
                break
            continue

        consecutive_empty = 0
        trade_ticks = ticks_to_trade_ticks(tick_resp, instrument_id)
        if trade_ticks:
            trade_ticks.sort(key=lambda t: t.ts_init)
            catalog.write_data(trade_ticks)
            total_ticks += len(trade_ticks)
            days_with_data += 1
            last_date = day

        if (i + 1) % 50 == 0:
            print(f"    progress: {i + 1}/{len(days)} days, "
                  f"{days_with_data} with data, {empty_days} empty, "
                  f"{total_ticks} ticks so far")

    return total_ticks, last_date


async def main(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog_path).resolve()
    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(catalog_path))

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    code = args.code

    instrument_id = InstrumentId(Symbol(code), VENUE)

    client = ShioajiClient(base_url=args.gateway_url)
    try:
        # Pre-flight quota check
        print("Checking quota...")
        if not await check_quota(client, args.min_remaining_mb):
            return

        print(f"Probing {code}...")
        ok = await probe_kbar_availability(client, code, end)
        if not ok:
            print(f"{code}: no data available")
            return

        equity = make_equity(code)
        catalog.write_data([equity])

        print(f"Fetching ticks for {code} {start} → {end}...")
        n_ticks, last_date = await fetch_stock_ticks(
            client, code, instrument_id, start, end, catalog, args.min_remaining_mb
        )
        print(f"Done: {n_ticks} ticks written")
        if last_date and last_date < end:
            next_start = last_date + timedelta(days=1)
            print(f"Resume tomorrow with: --start {next_start}")

        # Final quota report
        await check_quota(client, 0)
    finally:
        await client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch historical trade ticks for a single stock"
    )
    parser.add_argument("--code", required=True, help="Stock code (e.g. 2330)")
    parser.add_argument("--catalog-path", required=True, help="Catalog directory")
    parser.add_argument("--start", default="2020-03-02", help="Start date")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date")
    parser.add_argument("--gateway-url", default="http://localhost:8000")
    parser.add_argument(
        "--min-remaining-mb",
        type=float,
        default=50.0,
        help="Stop when remaining quota drops below this (MB, default: 50)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
