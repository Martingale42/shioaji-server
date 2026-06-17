"""Orchestrator CLI: build the 00981A point-in-time top-300 market-cap universe.

Wires the cached official-data client (``scripts.twse_tpex_market``) to the pure
ranking logic (``scripts.universe_ranking``) and writes two artifacts under
``universe/``:

  * ``00981a_top300_constituents.txt`` — the survivorship-correct UNION code list
    (feeds ``shioaji-data fetch-bars --codes-file``).
  * ``membership_00981a_top300.csv``   — [effective_from, effective_to) intervals
    in the 0050 membership schema.

Filters to common stock only (the t187ap03 datasets already exclude ETF / warrant
/ preferred / DR / 受益證券; we additionally drop 91xxxx TDR via the client's
4-digit-code filter). Logs a summary; never prints results.

Supports a SCOPED probe run (``--codes 2330,2884,8069``) to validate orchestration
end-to-end fast before committing to the ~27.6k-GET full-market daily-close pull.

Depends ONLY on polars + the two sibling scripts (which use polars + httpx); never
imports nautilus_trader or shioaji_server.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import polars as pl

from scripts.twse_tpex_market import (
    CACHE_DIR,
    CacheFetchError,
    fetch_capital_events,
    fetch_current_shares,
    fetch_daily_close,
    set_tpex_codes,
)
from scripts.universe_ranking import (
    build_union_and_membership,
    compute_market_cap,
    daily_top_n,
    reconstruct_daily_shares,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("build_00981a_universe")

# --- Reproducible config constants (no magic numbers) ------------------------
MEMBERSHIP_START = date(2025, 5, 27)  # 00981A listing date
TOP_N = 300
UNIVERSE_DIR = Path("universe")
OUT_CODES = UNIVERSE_DIR / "00981a_top300_constituents.txt"
OUT_MEMBERSHIP = UNIVERSE_DIR / "membership_00981a_top300.csv"
# R4: optional user-supplied snapshot of 00981A's disclosed holdings (one code/line).
HOLDINGS_SNAPSHOT = UNIVERSE_DIR / "00981a_holdings_snapshot.txt"


def build_universe(
    end: date,
    scope_codes: list[str] | None = None,
    cache_dir: Path = CACHE_DIR,
) -> tuple[list[str], pl.DataFrame]:
    """
    Definition: Orchestrate the point-in-time top-N market-cap universe build.
    Formula:    union, membership = build_union_and_membership(
                    daily_top_n(compute_market_cap(close, shares), TOP_N))
                where shares = reconstruct_daily_shares(commons, events, days).
    Domain:     ``end`` >= MEMBERSHIP_START. ``scope_codes`` (if given) restricts
                the universe to that hand-picked subset for a fast wiring probe;
                None means the full common-stock market. Pure orchestration — all
                numeric work lives in the two sibling modules.
    Returns:    (sorted union codes, membership DataFrame in the 0050 schema).
    """
    commons = fetch_current_shares(cache_dir=cache_dir)
    if commons.is_empty():
        raise RuntimeError(
            "fetch_current_shares returned no rows; cannot build universe"
        )

    if scope_codes:
        scope_set = set(scope_codes)
        commons = commons.filter(pl.col("code").is_in(scope_set))
        logger.info(
            "SCOPED probe: restricted to %d of %d requested codes present in market list",
            commons.height,
            len(scope_set),
        )

    # Route TPEx codes to the OTC daily-close endpoint (explicit, not guessed).
    tpex_codes = set(
        commons.filter(pl.col("market") == "tpex").select("code").to_series().to_list()
    )
    set_tpex_codes(tpex_codes)

    codes = commons.select("code").to_series().to_list()
    logger.info(
        "common-stock universe: %d codes (twse=%d, tpex=%d); window %s..%s",
        len(codes),
        len(codes) - len(tpex_codes),
        len(tpex_codes),
        MEMBERSHIP_START,
        end,
    )

    close = fetch_daily_close(codes, MEMBERSHIP_START, end, cache_dir=cache_dir)
    if close.is_empty():
        raise RuntimeError("fetch_daily_close returned no rows; cannot rank market cap")
    trading_days = sorted(close.select("date").unique().to_series().to_list())
    logger.info(
        "daily-close pull: %d rows over %d trading days",
        close.height,
        len(trading_days),
    )

    events = fetch_capital_events(codes, MEMBERSHIP_START, end, cache_dir=cache_dir)
    logger.info("capital events in window: %d", events.height)

    shares = reconstruct_daily_shares(
        commons.select(["code", "name", "shares"]), events, trading_days
    )
    mktcap = compute_market_cap(close, shares)
    daily_top = daily_top_n(mktcap, n=TOP_N)
    names = dict(zip(commons["code"].to_list(), commons["name"].to_list()))
    union, membership = build_union_and_membership(daily_top, names)

    logger.info(
        "union=%d codes, rebalance_days=%d, membership_intervals=%d",
        len(union),
        len(trading_days),
        membership.height,
    )
    return union, membership


def write_universe_files(union: list[str], membership: pl.DataFrame, end: date) -> None:
    """Write the union code list and interval membership CSV under ``universe/``.

    Definition: Persist the two universe artifacts with explicit header provenance.
    Domain:     ``union`` non-empty; ``membership`` in the 0050 schema. effective_to
                NULL is emitted as a blank CSV field (still-a-member sentinel).
    Returns:    None (writes files; logs paths).
    """
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "# 00981A (統一台股增長主動式 ETF) point-in-time top-300 market-cap UNION\n"
        f"# Membership window: {MEMBERSHIP_START}..{end}; daily rebalance; commons only.\n"
    )
    OUT_CODES.write_text(header + "\n".join(union) + "\n", encoding="utf-8")
    # Blank effective_to (None) -> empty CSV field, matching the 0050 convention.
    membership.write_csv(OUT_MEMBERSHIP, null_value="")
    logger.info("wrote %s (%d codes) and %s", OUT_CODES, len(union), OUT_MEMBERSHIP)


def validate_holdings_subset(
    union: list[str], snapshot: Path = HOLDINGS_SNAPSHOT
) -> None:
    """R4: assert 00981A's disclosed holdings are a subset of the union.

    Definition: Verify every disclosed 00981A holding appears in the top-N union.
    Formula:    misses = disclosed_holdings - set(union); a non-empty misses set
                means the top-N cut or the common-stock filter needs review.
    Domain:     ``snapshot`` is an optional one-code-per-line file. If absent, R4 is
                SKIPPED (logged) — never fabricated. Lines starting with '#' ignored.
    Returns:    None. Logs misses explicitly; does NOT silently widen the union.
    """
    if not snapshot.exists():
        logger.warning(
            "R4 subset validation SKIPPED: no holdings snapshot at %s (provide one to enable)",
            snapshot,
        )
        return
    holdings = [
        line.strip()
        for line in snapshot.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    union_set = set(union)
    misses = sorted(h for h in holdings if h not in union_set)
    if misses:
        logger.error(
            "R4 FAIL: %d/%d disclosed 00981A holdings NOT in union: %s "
            "(review top-N cut / filter; do NOT silently widen)",
            len(misses),
            len(holdings),
            misses,
        )
    else:
        logger.info(
            "R4 OK: all %d disclosed 00981A holdings are within the top-%d union",
            len(holdings),
            TOP_N,
        )


def main() -> None:
    """CLI entry: build the universe and write artifacts (full or scoped probe)."""
    parser = argparse.ArgumentParser(description="Build the 00981A top-300 universe.")
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date.today(),
        help="Membership-window end date (YYYY-MM-DD); default today.",
    )
    parser.add_argument(
        "--codes",
        type=str,
        default=None,
        help="Comma-separated scoped probe code list (e.g. 2330,2884,8069). "
        "Omit for the full common-stock market build.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Run the pipeline but do not write universe files (dry inspection).",
    )
    args = parser.parse_args()

    scope_codes = (
        [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else None
    )
    try:
        union, membership = build_universe(args.end, scope_codes=scope_codes)
    except CacheFetchError as exc:
        # F4: a data-source pull failed wholesale (transient outage). Do NOT write
        # a degraded universe; exit non-zero so a cron/orchestrator can retry.
        logger.error("DEGRADED RUN aborted (data-source fetch failure): %s", exc)
        sys.exit(2)
    if args.no_write:
        logger.info("--no-write set; skipping file output (union=%d)", len(union))
        return
    write_universe_files(union, membership, args.end)
    validate_holdings_subset(union)


if __name__ == "__main__":
    main()
