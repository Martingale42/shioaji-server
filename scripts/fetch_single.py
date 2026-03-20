"""Fetch historical kbar data for a single stock.

Usage::

    cd shioaji-server
    uv run python -m scripts.fetch_single --code 2330 --catalog-path ./catalog
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.model.instruments import Equity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.client import ShioajiClient
from scripts.fetch_historical import (
    BAR_SPEC,
    TWD,
    VENUE,
    fetch_stock_bars,
    probe_kbar_availability,
)


def make_equity(code: str) -> Equity:
    instrument_id = InstrumentId(Symbol(code), VENUE)
    return Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(code),
        currency=TWD,
        price_precision=2,
        price_increment=Price(0.01, precision=2),
        lot_size=Quantity(1000, precision=0),
        ts_event=0,
        ts_init=0,
    )


async def main(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog_path).resolve()
    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(catalog_path))

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    code = args.code

    instrument_id = InstrumentId(Symbol(code), VENUE)
    bar_type = BarType(instrument_id, BAR_SPEC)

    client = ShioajiClient(base_url=args.gateway_url)
    try:
        print(f"Probing {code}...")
        ok = await probe_kbar_availability(client, code, end)
        if not ok:
            print(f"{code}: no kbar data available")
            return

        equity = make_equity(code)
        catalog.write_data([equity])

        print(f"Fetching {code} {start}→{end}...")
        n_bars = await fetch_stock_bars(client, code, bar_type, start, end, catalog)
        print(f"Done: {n_bars} bars written")
    finally:
        await client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch kbars for a single stock")
    parser.add_argument("--code", required=True, help="Stock code (e.g. 2330)")
    parser.add_argument("--catalog-path", required=True, help="Catalog directory")
    parser.add_argument("--start", default="2020-03-02", help="Start date")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date")
    parser.add_argument("--gateway-url", default="http://localhost:8000")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
