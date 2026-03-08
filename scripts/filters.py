"""Composable stock filters for selecting instruments by price tier and liquidity."""

from collections.abc import Callable

import polars as pl

StockFilter = Callable[[pl.DataFrame], pl.DataFrame]


def price_between(low: float, high: float) -> StockFilter:
    """Filter stocks where ``close`` is in [low, high)."""

    def _filter(df: pl.DataFrame) -> pl.DataFrame:
        return df.filter((pl.col("close") >= low) & (pl.col("close") < high))

    return _filter


def top_n_by_volume(n: int) -> StockFilter:
    """Keep top *n* stocks by ``total_volume`` (descending)."""

    def _filter(df: pl.DataFrame) -> pl.DataFrame:
        return df.sort("total_volume", descending=True).head(n)

    return _filter


def exchange_in(exchanges: list[str]) -> StockFilter:
    """Keep only stocks whose ``exchange`` is in *exchanges* (e.g. ['TSE', 'OTC'])."""

    def _filter(df: pl.DataFrame) -> pl.DataFrame:
        return df.filter(pl.col("exchange").is_in(exchanges))

    return _filter


def exclude_codes(codes: list[str]) -> StockFilter:
    """Remove stocks whose ``code`` is in *codes*."""

    def _filter(df: pl.DataFrame) -> pl.DataFrame:
        return df.filter(~pl.col("code").is_in(codes))

    return _filter


def compose_filters(*filters: StockFilter) -> StockFilter:
    """Chain multiple filters sequentially."""

    def _filter(df: pl.DataFrame) -> pl.DataFrame:
        for f in filters:
            df = f(df)
        return df

    return _filter


def five_tier_liquidity(
    n_per_tier: int = 10,
    boundaries: list[float] | None = None,
) -> StockFilter:
    """Pick top-N most liquid stocks from each of 5 price tiers.

    Default tiers: <50, 50–100, 100–300, 300–600, 600+
    """
    if boundaries is None:
        boundaries = [0, 50, 100, 300, 600, float("inf")]

    def _filter(df: pl.DataFrame) -> pl.DataFrame:
        results = []
        for lo, hi in zip(boundaries[:-1], boundaries[1:]):
            tier = df.filter((pl.col("close") >= lo) & (pl.col("close") < hi))
            top = tier.sort("total_volume", descending=True).head(n_per_tier)
            results.append(top)
        return pl.concat(results) if results else df.head(0)

    return _filter
