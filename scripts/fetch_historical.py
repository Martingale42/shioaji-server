"""Fetch historical 1-min kbar data from Shioaji gateway into ParquetDataCatalog.

Usage::

    cd shioaji-server
    uv run python -m scripts.fetch_historical \\
        --catalog-path ./data/catalog \\
        --start 2020-03-02 \\
        --end 2026-03-08 \\
        --n-per-tier 10 \\
        --gateway-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.client import ShioajiClient
from scripts.filters import five_tier_liquidity

VENUE = Venue("SINOPAC")
TWD = Currency.from_str("TWD")
BAR_SPEC = BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)


def contract_to_equity(contract: dict) -> Equity:
    """Convert a gateway stock contract dict to an NT Equity instrument."""
    code = contract["code"]
    instrument_id = InstrumentId(Symbol(code), VENUE)
    now_ns = 0  # static instrument, timestamps don't matter
    return Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(code),
        currency=TWD,
        price_precision=2,
        price_increment=Price(0.01, precision=2),
        lot_size=Quantity(1000, precision=0),
        ts_event=now_ns,
        ts_init=now_ns,
    )


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


async def build_metadata(
    client: ShioajiClient,
) -> pl.DataFrame:
    """Fetch contracts + snapshots and join into a metadata DataFrame."""
    print("Fetching stock contracts...")
    contracts = await client.get_stock_contracts()
    print(f"  {len(contracts)} total contracts")

    # Filter out warrants (category "00") — they make up ~95% of contracts
    contracts = [c for c in contracts if c.get("category", "00") != "00"]
    print(f"  {len(contracts)} after removing warrants")

    codes = [c["code"] for c in contracts]
    print(f"Fetching snapshots for {len(codes)} stocks...")
    snapshots = await client.get_snapshots(codes)
    print(f"  {len(snapshots)} snapshots received")

    if not snapshots:
        raise RuntimeError(
            "No snapshots returned — the Shioaji backend may be offline "
            "(weekends/holidays). Try again on a trading day."
        )

    contracts_df = pl.DataFrame(contracts).select(
        "code", "name", "exchange", "reference", "day_trade", "category"
    )
    snapshots_df = pl.DataFrame(snapshots).select(
        "code", "close", "total_volume"
    )
    # Compute total_amount as close * total_volume (approximate)
    snapshots_df = snapshots_df.with_columns(
        (pl.col("close") * pl.col("total_volume")).alias("total_amount")
    )

    metadata = contracts_df.join(snapshots_df, on="code", how="inner")
    return metadata


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
    chunks = month_ranges(start, end)

    for chunk_start, chunk_end in chunks:
        start_str = chunk_start.isoformat()
        end_str = chunk_end.isoformat()

        try:
            kbar_resp = await client.get_kbars(code, start_str, end_str)
        except Exception as e:
            print(f"  ERROR {code} {start_str}→{end_str}: {e}")
            continue

        bars = kbars_to_bars(kbar_resp, bar_type)
        if bars:
            catalog.write_data(bars)
            total_bars += len(bars)

    return total_bars


async def main(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog_path).resolve()
    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(catalog_path))

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    client = ShioajiClient(base_url=args.gateway_url)
    try:
        # Step 1: Build metadata
        metadata = await build_metadata(client)
        print(f"Metadata: {metadata.shape[0]} stocks with snapshot data")

        # Step 2: Apply filter
        stock_filter = five_tier_liquidity(n_per_tier=args.n_per_tier)
        selected = stock_filter(metadata)
        print(f"Selected {selected.shape[0]} stocks after filtering")
        print(selected.select("code", "name", "close", "total_volume"))

        # Step 3: Save metadata for reuse in load_catalog
        meta_path = catalog_path / "metadata.parquet"
        selected.write_parquet(meta_path)
        print(f"Saved metadata to {meta_path}")

        # Step 4: Download kbars for each selected stock
        codes = selected["code"].to_list()
        for i, code in enumerate(codes, 1):
            instrument_id = InstrumentId(Symbol(code), VENUE)
            bar_type = BarType(instrument_id, BAR_SPEC)

            # Create and write instrument
            contract_row = selected.filter(pl.col("code") == code).to_dicts()[0]
            # Build a minimal contract dict for equity creation
            equity = contract_to_equity(contract_row)
            catalog.write_data([equity])

            print(f"[{i}/{len(codes)}] {code} ({contract_row['name']}) "
                  f"{start}→{end}...")
            n_bars = await fetch_stock_bars(
                client, code, bar_type, start, end, catalog
            )
            print(f"  → {n_bars} bars written")

        print("Done!")
    finally:
        await client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch historical kbar data from Shioaji gateway"
    )
    parser.add_argument(
        "--catalog-path",
        required=True,
        help="Path to ParquetDataCatalog directory",
    )
    parser.add_argument(
        "--start",
        default="2020-03-02",
        help="Start date YYYY-MM-DD (default: 2020-03-02)",
    )
    parser.add_argument(
        "--end",
        default=date.today().isoformat(),
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--n-per-tier",
        type=int,
        default=10,
        help="Number of stocks per price tier (default: 10)",
    )
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:8000",
        help="Shioaji gateway URL (default: http://localhost:8000)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
