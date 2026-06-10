"""``shioaji-data`` CLI: argparse front-end for the Shioaji→NT data pipeline.

This is the **only** CLI layer. It owns argument parsing and dispatch; all
real work lives in the pure callables of :mod:`shioaji_server.data.fetch`
(``fetch_bars_one`` / ``fetch_ticks_one`` / ``write_instrument_def_one`` +
``QuotaGate`` / ``run_batch``) and :func:`shioaji_server.data.inspect.inspect_catalog`.

Four subcommands share two global flags (``--catalog`` / ``--gateway-url``)
via an argparse parent parser:

* ``fetch-bars``      — download 1-min bars for one or many tickers.
* ``fetch-ticks``     — download trade ticks day-by-day, quota-aware.
* ``instrument-def``  — write only the NT instrument definition(s).
* ``inspect``         — equity-definition + bar-quality report (no ticker args).

Exit codes: ``0`` all complete · ``2`` partial/failed present · ``1`` gateway down.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

import httpx
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from .client import ShioajiClient
from .fetch import (
    QuotaGate,
    TickerResult,
    fetch_bars_one,
    fetch_ticks_one,
    format_batch_report,
    run_batch,
    write_instrument_def_one,
)
from .inspect import inspect_catalog

DEFAULT_CATALOG = "./catalog"
DEFAULT_GATEWAY_URL = "http://localhost:8000"
DEFAULT_START = "2020-03-02"
DEFAULT_CONCURRENCY = 4
DEFAULT_MIN_REMAINING_MB = 50.0

FETCH_COMMANDS = ("fetch-bars", "fetch-ticks", "instrument-def")


def _add_ticker_args(sub: argparse.ArgumentParser) -> None:
    """Attach the mutually-exclusive, required ticker selector to *sub*.

    Exactly one of ``--code`` / ``--codes`` / ``--codes-file`` must be given
    (argparse enforces the XOR + required-ness, raising ``SystemExit`` on
    violation). ``--codes`` is a comma-separated list; ``--codes-file`` is a
    path to a newline-delimited operator file (``#`` comments / blanks skipped).
    """
    group = sub.add_mutually_exclusive_group(required=True)
    group.add_argument("--code", help="single ticker code, e.g. 0050")
    group.add_argument("--codes", help="comma-separated ticker codes, e.g. 0050,00631L,2330")
    group.add_argument(
        "--codes-file",
        help="path to a newline-delimited ticker file (# comments and blank lines skipped)",
    )


def _add_range_args(sub: argparse.ArgumentParser) -> None:
    """Attach the shared ``--start`` / ``--end`` / ``--concurrency`` flags."""
    sub.add_argument("--start", default=DEFAULT_START, help=f"start date YYYY-MM-DD (default: {DEFAULT_START})")
    sub.add_argument("--end", default=None, help="end date YYYY-MM-DD (default: today)")
    sub.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"max tickers in flight (default: {DEFAULT_CONCURRENCY})",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``shioaji-data`` argparse parser.

    Global ``--catalog`` / ``--gateway-url`` live on a parent parser inherited
    by all four subparsers via ``parents=[common]``. The three fetch
    subcommands additionally carry the ticker selector plus ``--start`` /
    ``--end`` / ``--concurrency`` (``fetch-ticks`` also ``--min-remaining-mb``);
    ``inspect`` carries no ticker args.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--catalog",
        default=DEFAULT_CATALOG,
        help=f"path to the ParquetDataCatalog directory (default: {DEFAULT_CATALOG})",
    )
    common.add_argument(
        "--gateway-url",
        default=DEFAULT_GATEWAY_URL,
        help=f"Shioaji gateway base URL (default: {DEFAULT_GATEWAY_URL})",
    )

    parser = argparse.ArgumentParser(
        prog="shioaji-data",
        description="Batch + parallel Shioaji→NautilusTrader data pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bars = subparsers.add_parser(
        "fetch-bars",
        parents=[common],
        help="download 1-min bars for one or many tickers",
    )
    _add_ticker_args(bars)
    _add_range_args(bars)

    ticks = subparsers.add_parser(
        "fetch-ticks",
        parents=[common],
        help="download trade ticks day-by-day, quota-aware",
    )
    _add_ticker_args(ticks)
    _add_range_args(ticks)
    ticks.add_argument(
        "--min-remaining-mb", type=float, default=DEFAULT_MIN_REMAINING_MB,
        help=f"shared quota floor in MB; batch winds down below it (default: {DEFAULT_MIN_REMAINING_MB})",
    )

    instr = subparsers.add_parser(
        "instrument-def",
        parents=[common],
        help="write only the NT instrument definition(s)",
    )
    _add_ticker_args(instr)
    _add_range_args(instr)

    subparsers.add_parser(
        "inspect",
        parents=[common],
        help="equity-definition + bar-quality report",
    )

    return parser


def _read_codes_file(path: str) -> list[str]:
    """Read ticker codes from a local operator file, one per line.

    Security: the path is opened as given (a local operator-controlled file).
    Its contents are treated as plain text only — never ``eval``'d or passed to
    a shell. Blank lines and ``#`` comment lines are stripped; inline trailing
    ``#`` comments are NOT parsed (a code never contains ``#``).
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    codes: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line)
    return codes


def resolve_codes(args: argparse.Namespace) -> list[str]:
    """Resolve the ticker selector flags to a concrete list of codes.

    ``--code`` → ``[code]``; ``--codes`` → comma-split (blanks dropped);
    ``--codes-file`` → :func:`_read_codes_file`. argparse guarantees exactly
    one of the three is set on a fetch subcommand.
    """
    code = getattr(args, "code", None)
    codes = getattr(args, "codes", None)
    codes_file = getattr(args, "codes_file", None)

    if code:
        return [code]
    if codes:
        return [c.strip() for c in codes.split(",") if c.strip()]
    if codes_file:
        return _read_codes_file(codes_file)
    return []


def _parse_date(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` string into a :class:`datetime.date`."""
    return date.fromisoformat(value)


async def _check_gateway(gateway_url: str) -> None:
    """Pre-flight ``GET {gateway_url}/api/health``; raise on connect failure.

    Lets ``httpx.ConnectError`` propagate so ``main`` can render the friendly
    "gateway not reachable" message and exit 1.
    """
    async with httpx.AsyncClient(timeout=10.0) as probe:
        await probe.get(f"{gateway_url}/api/health")


def _build_per_ticker(
    args: argparse.Namespace,
    client: ShioajiClient,
    catalog: ParquetDataCatalog,
):
    """Build the bound ``per_ticker`` closure for the active fetch subcommand.

    Each closure binds the shared client / catalog / gateway-url (and, for
    ticks, the single shared :class:`QuotaGate`) and exposes the
    ``(code) -> Awaitable[TickerResult]`` shape ``run_batch`` expects. The
    positional wiring follows the Task 3 authoritative signatures in
    ``fetch.py`` (NOT the stale design §4.4 order).
    """
    gateway_url = args.gateway_url

    if args.command == "fetch-bars":
        start = _parse_date(args.start)
        end = _parse_date(args.end) if args.end else date.today()

        async def per_ticker(code: str) -> TickerResult:
            return await fetch_bars_one(client, gateway_url, code, start, end, catalog)

        return per_ticker

    if args.command == "fetch-ticks":
        start = _parse_date(args.start)
        end = _parse_date(args.end) if args.end else date.today()
        # ONE shared gate across the whole batch — quota is a batch-level
        # resource, not per-ticker. The old per-call min_remaining_mb now lives
        # on the gate.
        gate = QuotaGate(client, args.min_remaining_mb)

        async def per_ticker(code: str) -> TickerResult:
            return await fetch_ticks_one(client, gateway_url, code, start, end, catalog, gate=gate)

        return per_ticker

    # instrument-def — write_instrument_def_one(client_url, code, catalog).
    async def per_ticker(code: str) -> TickerResult:
        return await write_instrument_def_one(gateway_url, code, catalog)

    return per_ticker


def main(argv: list[str] | None = None) -> int:
    """Parse args, pre-flight the gateway, dispatch a subcommand, return exit code.

    Returns: ``0`` if every ticker completed, ``2`` if any partial/failed
    result is present, ``1`` if the gateway is unreachable.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    catalog_dir = Path(args.catalog).resolve()

    if args.command == "inspect":
        inspect_catalog(catalog_dir)
        return 0

    # Pre-flight: fail fast and friendly if the gateway is down.
    try:
        asyncio.run(_check_gateway(args.gateway_url))
    except httpx.ConnectError:
        print(
            f"gateway not reachable at {args.gateway_url} — container up & "
            f"logged in? (curl {args.gateway_url}/api/health)"
        )
        return 1

    codes = resolve_codes(args)

    async def _run() -> int:
        client = ShioajiClient(base_url=args.gateway_url)
        try:
            catalog = ParquetDataCatalog(str(catalog_dir))
            per_ticker = _build_per_ticker(args, client, catalog)
            results = await run_batch(codes, args.concurrency, per_ticker)
            # fetch-bars resumes from the catalog itself, so its partial hints
            # say "re-run the same command"; ticks keep --start-driven hints.
            print(
                format_batch_report(
                    results, auto_resume=(args.command == "fetch-bars")
                )
            )
            all_complete = all(r.status == "complete" for r in results)
            return 0 if all_complete else 2
        finally:
            await client.close()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
