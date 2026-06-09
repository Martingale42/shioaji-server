"""Inspect ParquetDataCatalog contents: equity definitions and bar data quality.

Usage::

    cd shioaji-server
    uv run python -m scripts.inspect_catalog --catalog-path ./catalog
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import polars as pl


def ns_to_dt(ns: int) -> datetime:
    """Convert nanosecond timestamp to datetime."""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def inspect_equities(catalog_dir: Path) -> None:
    """Check all equity instrument definitions."""
    equity_dir = catalog_dir / "data" / "equity"
    if not equity_dir.exists():
        print("No equity data found.")
        return

    dirs = sorted(equity_dir.iterdir())
    print(f"=== Equity Instruments ({len(dirs)}) ===\n")

    # NT Equity required fields
    required_fields = {
        "id", "raw_symbol", "currency", "price_precision",
        "price_increment", "lot_size", "ts_event", "ts_init",
    }

    rows = []
    issues = []
    for d in dirs:
        df = pl.read_parquet(d)
        row = df.to_dicts()[0]
        rows.append(row)

        # Check for missing required fields
        missing = required_fields - set(df.columns)
        if missing:
            issues.append(f"  {d.name}: missing fields: {missing}")

        # Check for suspicious values
        if row.get("price_precision", 0) == 0:
            issues.append(f"  {d.name}: price_precision is 0")
        if row.get("currency") is None:
            issues.append(f"  {d.name}: currency is None")

    # Summary table
    summary = pl.DataFrame(rows).select(
        "id", "raw_symbol", "currency", "price_precision",
        "price_increment", "lot_size",
    )
    print(summary)

    if issues:
        print(f"\nIssues found ({len(issues)}):")
        for issue in issues:
            print(issue)
    else:
        print("\nAll equity definitions look valid.")

    # Check for equities without corresponding bar data
    bar_dir = catalog_dir / "data" / "bar"
    equity_codes = {d.name for d in dirs}
    bar_codes = {d.name.split("-")[0] for d in bar_dir.iterdir()} if bar_dir.exists() else set()
    orphans = equity_codes - bar_codes
    if orphans:
        print(f"\nEquities without bar data ({len(orphans)}):")
        for o in sorted(orphans):
            print(f"  {o}")


def inspect_bars(catalog_dir: Path) -> None:
    """Analyze bar data: date range, row counts, and gap detection."""
    bar_dir = catalog_dir / "data" / "bar"
    if not bar_dir.exists():
        print("No bar data found.")
        return

    dirs = sorted(bar_dir.iterdir())
    print(f"\n=== Bar Data ({len(dirs)} instruments) ===\n")

    summary_rows = []

    for d in dirs:
        parquet_files = sorted(d.glob("*.parquet"))
        if not parquet_files:
            summary_rows.append({
                "instrument": d.name,
                "files": 0, "bars": 0,
                "first": None, "last": None,
                "trading_days": 0, "gap_days": 0,
            })
            continue

        # Read all parquet files for this instrument
        df = pl.read_parquet(d)
        n_bars = len(df)

        ts_col = "ts_event" if "ts_event" in df.columns else "ts"
        timestamps = df[ts_col].sort()

        first_ns = timestamps[0]
        last_ns = timestamps[-1]
        first_dt = ns_to_dt(first_ns)
        last_dt = ns_to_dt(last_ns)

        # Extract trading days
        trading_days = (
            df.with_columns(
                (pl.col(ts_col) // 1_000_000_000).cast(pl.Int64).alias("epoch_s")
            )
            .with_columns(
                pl.from_epoch("epoch_s").dt.date().alias("date")
            )
            .select("date")
            .unique()
            .sort("date")
        )

        n_trading_days = len(trading_days)
        dates = trading_days["date"].to_list()

        # Detect gaps: consecutive trading days with >3 calendar days between them
        # (normal weekends = 2 days gap, so >3 catches holidays + unexpected gaps)
        gaps = []
        for i in range(1, len(dates)):
            delta = (dates[i] - dates[i - 1]).days
            if delta > 5:  # More than a work-week gap (likely holiday + extra)
                gaps.append((dates[i - 1], dates[i], delta))

        summary_rows.append({
            "instrument": d.name.replace("-1-MINUTE-LAST-EXTERNAL", ""),
            "files": len(parquet_files),
            "bars": n_bars,
            "first": first_dt.strftime("%Y-%m-%d"),
            "last": last_dt.strftime("%Y-%m-%d"),
            "trading_days": n_trading_days,
            "gap_days": len(gaps),
        })

        if gaps:
            print(f"  {d.name.replace('-1-MINUTE-LAST-EXTERNAL', '')} gaps (>{5} calendar days):")
            for g_start, g_end, delta in gaps:
                print(f"    {g_start} → {g_end} ({delta} days)")

    summary_df = pl.DataFrame(summary_rows)
    print("\nSummary:")
    print(summary_df)

    # Overall stats
    total_bars = summary_df["bars"].sum()
    print(f"\nTotal bars across all instruments: {total_bars:,}")


def inspect_catalog(catalog_dir: Path) -> None:
    """Run the full catalog inspection: equity definitions + bar data quality."""
    if not catalog_dir.exists():
        print(f"Catalog not found: {catalog_dir}")
        return

    inspect_equities(catalog_dir)
    inspect_bars(catalog_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect ParquetDataCatalog contents")
    parser.add_argument(
        "--catalog-path", required=True, help="Path to catalog directory"
    )
    args = parser.parse_args()

    inspect_catalog(Path(args.catalog_path).resolve())


if __name__ == "__main__":
    main()
