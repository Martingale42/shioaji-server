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

from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from shioaji_server.data.client import ShioajiClient
from shioaji_server.data.bars import (
    BAR_SPEC,
    VENUE,
    fetch_stock_bars,
    probe_kbar_availability,
)
from shioaji_server.data.instruments import load_instrument


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

        instrument = await load_instrument(args.gateway_url, instrument_id)
        catalog.write_data([instrument])

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
