"""One-time migration: rewrite TradeId in existing TradeTick parquet files.

Converts from old format ``{ts_ns}-{index}`` to the live adapter convention
``{code}-{datetime}`` (e.g. ``2330-2024-10-01 09:00:01.088395``).

Usage::

    cd shioaji-server
    uv run python -m scripts.fix_trade_ids --catalog-path ./catalog
    uv run python -m scripts.fix_trade_ids --catalog-path ./catalog --dry-run
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_TW = timezone(timedelta(hours=8))


def fix_trade_ids(file_path: Path, code: str, dry_run: bool = False) -> int:
    """Rewrite trade_id column in a single parquet file. Returns row count."""
    table = pq.read_table(file_path)
    ts_events = table.column("ts_event").to_pylist()

    new_ids = []
    for ts_ns in ts_events:
        dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=_TW)
        new_ids.append(f"{code}-{dt.strftime('%Y-%m-%d %H:%M:%S.%f')}")

    if dry_run:
        return len(new_ids)

    idx = table.schema.get_field_index("trade_id")
    new_table = table.set_column(idx, "trade_id", pa.array(new_ids, type=pa.string()))
    pq.write_table(new_table, file_path, **_write_kwargs(table))
    return len(new_ids)


def _write_kwargs(table: pa.Table) -> dict:
    """Preserve original parquet metadata when rewriting."""
    return {
        "store_schema": True,
        "existing_data_behavior": "overwrite_or_ignore",
    }


def main(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog_path).resolve()
    trade_tick_dir = catalog_path / "data" / "trade_tick"

    if not trade_tick_dir.exists():
        print(f"No trade_tick data found at {trade_tick_dir}")
        return

    instrument_dirs = sorted(trade_tick_dir.iterdir())
    total_files = 0
    total_rows = 0

    for inst_dir in instrument_dirs:
        if not inst_dir.is_dir():
            continue

        # Extract code from directory name like "2330.SINOPAC"
        code = inst_dir.name.split(".")[0]
        parquet_files = sorted(inst_dir.glob("*.parquet"))

        if not parquet_files:
            continue

        print(f"{inst_dir.name}: {len(parquet_files)} files")

        for i, pf in enumerate(parquet_files, 1):
            rows = fix_trade_ids(pf, code, dry_run=args.dry_run)
            total_files += 1
            total_rows += rows

            if i % 100 == 0:
                print(f"  progress: {i}/{len(parquet_files)} files")

    action = "would rewrite" if args.dry_run else "rewrote"
    print(f"\nDone: {action} {total_rows:,} trade_ids across {total_files} files")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fix TradeId format in existing TradeTick parquet files"
    )
    parser.add_argument("--catalog-path", required=True, help="Catalog directory")
    parser.add_argument(
        "--dry-run", action="store_true", help="Count rows without writing"
    )
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
