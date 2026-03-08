"""Load bar data from ParquetDataCatalog into a polars DataFrame.

Usage::

    from scripts.filters import five_tier_liquidity, price_between, compose_filters
    from scripts.load_catalog import load_bars_as_dataframe

    # Default filter (uses saved metadata)
    df = load_bars_as_dataframe("./data/catalog")

    # Custom filter
    my_filter = compose_filters(
        price_between(50, 300),
        five_tier_liquidity(n_per_tier=5),
    )
    df = load_bars_as_dataframe("./data/catalog", stock_filter=my_filter)
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.filters import StockFilter

VENUE = Venue("SINOPAC")
BAR_SPEC = BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)


def load_bars_as_dataframe(
    catalog_path: str,
    stock_filter: StockFilter | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pl.DataFrame:
    """Load filtered bar data from a ParquetDataCatalog.

    1. Read ``metadata.parquet`` from the catalog directory
    2. Apply *stock_filter* to select stocks (all stocks if None)
    3. Query catalog for matching bar data
    4. Return a polars DataFrame with columns:
       ``code, ts, open, high, low, close, volume``
    """
    catalog_dir = Path(catalog_path).resolve()
    catalog = ParquetDataCatalog(str(catalog_dir))

    # Load and filter metadata
    meta_path = catalog_dir / "metadata.parquet"
    if not meta_path.exists():
        msg = f"metadata.parquet not found in {catalog_dir}. Run fetch_historical first."
        raise FileNotFoundError(msg)

    metadata = pl.read_parquet(meta_path)
    if stock_filter is not None:
        metadata = stock_filter(metadata)

    codes = metadata["code"].to_list()
    if not codes:
        return pl.DataFrame(
            schema={"code": pl.Utf8, "ts": pl.Int64, "open": pl.Float64,
                     "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                     "volume": pl.Int64}
        )

    # Build bar type strings and query catalog
    all_frames: list[pl.DataFrame] = []
    for code in codes:
        instrument_id = InstrumentId(Symbol(code), VENUE)
        bar_type_str = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"

        bars = catalog.bars(
            bar_types=[bar_type_str],
            start=start,
            end=end,
        )

        if not bars:
            continue

        # Convert NT Bar objects to records
        records = []
        for bar in bars:
            records.append({
                "code": code,
                "ts": bar.ts_event,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
            })

        if records:
            all_frames.append(pl.DataFrame(records))

    if not all_frames:
        return pl.DataFrame(
            schema={"code": pl.Utf8, "ts": pl.Int64, "open": pl.Float64,
                     "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                     "volume": pl.Int64}
        )

    return pl.concat(all_frames).sort("code", "ts")
