"""Pure point-in-time market-cap ranking and membership-union logic.

No network, no I/O — every input and output is a polars DataFrame. This is the
correctness core of the 00981A top-300 universe pipeline:

  1. ``reconstruct_daily_shares``    — roll current shares backward through events
  2. ``compute_market_cap``          — close * shares
  3. ``daily_top_n``                 — the n largest each trading day
  4. ``build_union_and_membership``  — union + [from, to) membership intervals

Depends ONLY on polars; never imports nautilus_trader or shioaji_server.
"""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

logger = logging.getLogger(__name__)

# 0050 membership schema (reused verbatim; effective_to blank == still a member).
MEMBERSHIP_COLUMNS = [
    "code",
    "name",
    "effective_from",
    "effective_to",
    "confidence",
    "note",
]


def reconstruct_daily_shares(
    current_shares: pl.DataFrame, events: pl.DataFrame, trading_days: list[date]
) -> pl.DataFrame:
    """
    Definition: Point-in-time issued-share count per code per trading day.
    Formula:    shares(code, d) = current_shares(code) - sum(delta_e
                for events e of code with effective_date > d).
                (roll backward from today's anchor through each in-window delta)
    Domain:     trading_days within the membership window; codes with no event are
                constant = current_shares; assumes events are complete (Task 1 R1).
                A capital INCREASE has shares_delta > 0, so days strictly BEFORE its
                effective_date see the smaller pre-event count (delta subtracted);
                on/after the effective_date the full current anchor stands.
    Returns:    DataFrame[code, date, shares: int].
    """
    schema = {"code": pl.Utf8, "date": pl.Date, "shares": pl.Int64}
    if not trading_days:
        return pl.DataFrame(schema=schema)

    anchor = current_shares.select(["code", "shares"]).with_columns(
        pl.col("shares").cast(pl.Int64)
    )
    # Cartesian product code x trading_day.
    days_df = pl.DataFrame({"date": sorted(trading_days)}, schema={"date": pl.Date})
    grid = anchor.join(days_df, how="cross")

    if events.is_empty():
        return grid.select(["code", "date", "shares"]).sort(["code", "date"])

    ev = events.select(
        pl.col("code"),
        pl.col("date").alias("effective_date"),
        pl.col("shares_delta").cast(pl.Int64),
    )

    # For each (code, d), subtract the deltas of that code's events with
    # effective_date STRICTLY GREATER than d (future relative to d).
    future_deltas = (
        grid.join(ev, on="code", how="left")
        .filter(pl.col("effective_date") > pl.col("date"))
        .group_by(["code", "date"])
        .agg(pl.col("shares_delta").sum().alias("future_delta"))
    )

    out = (
        grid.join(future_deltas, on=["code", "date"], how="left")
        .with_columns(pl.col("future_delta").fill_null(0))
        .with_columns((pl.col("shares") - pl.col("future_delta")).alias("shares"))
        .select(["code", "date", "shares"])
        .sort(["code", "date"])
    )
    return out


def compute_market_cap(close: pl.DataFrame, shares: pl.DataFrame) -> pl.DataFrame:
    """
    Definition: Point-in-time market capitalisation per code per trading day.
    Formula:    mktcap(code, d) = close(code, d) * shares(code, d).
    Domain:     Inner-join on (code, date); rows missing either factor are dropped.
    Returns:    DataFrame[code, date, mktcap: float].
    """
    joined = close.join(shares, on=["code", "date"], how="inner")
    return (
        joined.with_columns(
            (
                pl.col("close").cast(pl.Float64) * pl.col("shares").cast(pl.Float64)
            ).alias("mktcap")
        )
        .select(["code", "date", "mktcap"])
        .sort(["date", "code"])
    )


def daily_top_n(mktcap: pl.DataFrame, n: int = 300) -> pl.DataFrame:
    """
    Definition: The n highest-market-cap codes on each trading day.
    Formula:    top_n(d) = argsort_desc(mktcap(*, d))[:n].
    Domain:     n>0; days with < n codes return all available (logged).
    Returns:    DataFrame[date, code] (n rows per date).
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if mktcap.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "code": pl.Utf8})

    # Deterministic ordering: market cap desc, then code asc to break ties.
    ranked = (
        mktcap.sort(["date", "mktcap", "code"], descending=[False, True, False])
        .with_columns(pl.int_range(0, pl.len()).over("date").alias("rank"))
        .filter(pl.col("rank") < n)
    )

    short_days = (
        mktcap.group_by("date")
        .agg(pl.len().alias("n_codes"))
        .filter(pl.col("n_codes") < n)
    )
    for row in short_days.iter_rows(named=True):
        logger.warning(
            "trading day %s has only %d codes (< top-%d); using all available",
            row["date"],
            row["n_codes"],
            n,
        )
    return ranked.select(["date", "code"]).sort(["date", "code"])


def build_union_and_membership(
    daily_top: pl.DataFrame, names: dict[str, str]
) -> tuple[list[str], pl.DataFrame]:
    """
    Definition: Survivorship-correct union of all codes ever in the daily top-N,
                plus per-code [effective_from, effective_to) membership intervals.
    Formula:    union = distinct(daily_top.code); intervals = maximal runs of
                consecutive trading days a code is present (gap > 0 splits a run).
    Domain:     daily_top sorted by date; effective_to is exclusive, blank=still in.
                "Consecutive" is measured against the global ordered list of trading
                days present in ``daily_top`` (a missing day in between splits a run).
    Returns:    (sorted union codes, DataFrame[code,name,effective_from,
                effective_to,confidence,note]) matching the 0050 membership schema.
    """
    schema = {
        "code": pl.Utf8,
        "name": pl.Utf8,
        "effective_from": pl.Date,
        "effective_to": pl.Date,
        "confidence": pl.Utf8,
        "note": pl.Utf8,
    }
    if daily_top.is_empty():
        return [], pl.DataFrame(schema=schema)

    all_days = sorted(daily_top.select("date").unique().to_series().to_list())
    # Index each global trading day so "consecutive" == adjacent indices.
    day_index = {d: i for i, d in enumerate(all_days)}
    last_day = all_days[-1]

    union = sorted(daily_top.select("code").unique().to_series().to_list())

    records: list[dict[str, object]] = []
    for code in union:
        code_days = sorted(
            daily_top.filter(pl.col("code") == code)
            .select("date")
            .to_series()
            .to_list()
        )
        if not code_days:
            continue
        # Split into maximal runs of consecutive global-trading-day indices.
        run_start = code_days[0]
        prev = code_days[0]
        for d in code_days[1:]:
            if day_index[d] == day_index[prev] + 1:
                prev = d
                continue
            records.append(
                _make_interval(
                    code, names, run_start, prev, all_days, day_index, last_day
                )
            )
            run_start = d
            prev = d
        records.append(
            _make_interval(code, names, run_start, prev, all_days, day_index, last_day)
        )

    mem = pl.DataFrame(records, schema=schema).sort(["code", "effective_from"])
    return union, mem


def _make_interval(
    code: str,
    names: dict[str, str],
    run_start: date,
    run_end_inclusive: date,
    all_days: list[date],
    day_index: dict[date, int],
    last_day: date,
) -> dict[str, object]:
    """Build one membership-interval record with exclusive effective_to.

    Definition: Convert an inclusive run [run_start, run_end_inclusive] of present
                days into a half-open membership interval [effective_from, effective_to).
    Formula:    effective_from = run_start;
                effective_to   = NEXT global trading day after run_end_inclusive,
                                 or None (blank) if the run reaches the last day
                                 (still a member as of the latest day seen).
    Domain:     run_end_inclusive must be a present trading day in all_days.
    Returns:    A dict in the 0050 membership schema (effective_to None == blank).
    """
    idx_end = day_index[run_end_inclusive]
    if run_end_inclusive == last_day:
        effective_to: date | None = None  # still in as of latest trading day
    else:
        effective_to = all_days[idx_end + 1]  # exclusive: first day NOT a member
    return {
        "code": code,
        "name": names.get(code, ""),
        "effective_from": run_start,
        "effective_to": effective_to,
        "confidence": "high",
        "note": "point-in-time top-N market-cap membership",
    }
