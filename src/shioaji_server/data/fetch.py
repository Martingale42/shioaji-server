"""Single-ticker download callables for the same-source Shioaji→NT pipeline.

Consolidates the retired ``fetch_single`` (bars) and ``fetch_single_ticks``
(trade ticks) scripts into pure, reusable single-ticker drivers:

* ``write_instrument_def_one`` — load + write one NT instrument definition.
* ``fetch_bars_one`` — probe, side-write instrument def, download 1-min bars.
* ``fetch_ticks_one`` — probe, side-write instrument def, download trade ticks
  day-by-day with a quota-aware stop and ``--start`` resume support.

Each returns a :class:`TickerResult`. The argparse front-end and the
batch/quota orchestration (``QuotaGate`` / ``run_batch``) are added by the
``shioaji-data`` CLI layers; this module owns the per-ticker logic only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from nautilus_trader.model.data import BarType, TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from .bars import (
    BAR_SPEC,
    VENUE,
    fetch_stock_bars,
    probe_kbar_availability,
)
from .client import ShioajiClient
from .instruments import load_instrument

MAX_CONSECUTIVE_ERRORS = 5
MAX_CONSECUTIVE_EMPTY = 10


@dataclass
class TickerResult:
    code: str
    status: str  # "complete" | "partial" | "no_data" | "failed"
    n_written: int = 0
    last_date: date | None = None
    error: str | None = None


def _tick_type_to_aggressor(tick_type: int) -> AggressorSide:
    """Map Shioaji tick_type to NT AggressorSide.

    Shioaji: 1 = 外盤 (buy-initiated), 2 = 內盤 (sell-initiated), 0 = unknown.
    """
    if tick_type == 1:
        return AggressorSide.BUYER
    elif tick_type == 2:
        return AggressorSide.SELLER
    return AggressorSide.NO_AGGRESSOR


def _ns_to_trade_id(code: str, ts_ns: int) -> TradeId:
    """Build TradeId matching the sinopac HTTP adapter convention: ``{code}-{ts_ns}``.

    The sinopac Rust HTTP parser builds ``format!("{}-{}", code, ts_ns)`` with the
    raw nanosecond integer (``http/parse.rs:87``), which is unique per trade. The
    previous microsecond ``strftime`` form collided for trades sharing the same
    microsecond (proven 36/1922 dupes on a single day). The nanosecond integer
    eliminates those collisions AND aligns historical download IDs with the live
    HTTP adapter. ``ts_ns`` is the gateway-corrected true-UTC epoch, so the
    resulting IDs match the live HTTP path exactly.
    """
    return TradeId(f"{code}-{ts_ns}")


def ticks_to_trade_ticks(
    tick_resp: dict, instrument_id: InstrumentId
) -> tuple[list[TradeTick], int]:
    """Convert a ticks API response dict to a list of NT TradeTick objects.

    Definition: Maps each Shioaji tick row (ts/close/volume/tick_type) to an
        NT ``TradeTick``, dropping rows that carry no executed trade.
    Domain:     Shioaji occasionally returns rows with ``volume == 0`` or
        ``close <= 0`` — pre-open trial-match / auction-disclosure ticks that
        report an indicative price but no actual fill. NT's ``TradeTick``
        rejects these via ``Condition.positive_int`` on ``size`` (raising
        ``ValueError: 'size' not a positive integer, was 0``), so they are
        filtered out instead of aborting the whole trading day.
    Returns:    ``(trade_ticks, n_skipped)`` — the valid ticks plus the count
        of dropped invalid rows (for diagnostic accounting).
    """
    code = instrument_id.symbol.value
    timestamps = tick_resp["ts"]
    closes = tick_resp["close"]
    volumes = tick_resp["volume"]
    tick_types = tick_resp["tick_type"]

    trade_ticks: list[TradeTick] = []
    skipped = 0
    for i in range(len(timestamps)):
        if volumes[i] <= 0 or closes[i] <= 0:
            skipped += 1
            continue
        ts_ns = timestamps[i]
        trade_ticks.append(
            TradeTick(
                instrument_id=instrument_id,
                price=Price(closes[i], precision=2),
                size=Quantity(volumes[i], precision=0),
                aggressor_side=_tick_type_to_aggressor(tick_types[i]),
                trade_id=_ns_to_trade_id(code, ts_ns),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
        )
    return trade_ticks, skipped


def trading_days(start: date, end: date) -> list[date]:
    """Generate weekdays between start and end (inclusive)."""
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:  # Mon-Fri
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


async def check_quota(client: ShioajiClient, min_remaining_mb: float) -> bool:
    """Return True if enough quota remains, False to stop."""
    try:
        usage = await client.get_usage()
        remaining_mb = usage["remaining_mb"]
        used_mb = usage["used_mb"]
        limit_mb = usage["limit_mb"]
        pct = usage["remaining_pct"]
        print(f"    quota: {used_mb:.1f} / {limit_mb:.1f} MB used ({remaining_mb:.1f} MB remaining, {pct:.1f}%)")
        if remaining_mb < min_remaining_mb:
            print(f"    STOP: remaining {remaining_mb:.1f} MB < threshold {min_remaining_mb:.1f} MB")
            return False
        return True
    except Exception as e:
        print(f"    WARNING: quota check failed: {e!r}")
        return True  # continue on failure — don't block fetch for a monitoring issue


class QuotaGate:
    """Shared, throttled coordinator for the daily ``/api/account/usage`` quota.

    Definition: A batch-level gate that centralizes and throttles quota polling
        across many concurrent tickers, exposing a single latching ``tripped``
        flag. Parallel ``fetch_ticks_one`` workers consult one gate instead of
        each hammering ``/api/account/usage`` and each tracking their own stop
        condition.
    Domain:     Single-threaded asyncio only. Coroutines yield only at ``await``
        points, so the cache/tripped mutations are atomic and need no lock; this
        class is NOT thread-safe. ``min_remaining_mb`` is the floor in MB below
        which the gate trips. Throttling uses the **event-loop clock**
        (``asyncio.get_event_loop().time()``), never wall-clock time, so it is
        immune to system clock changes and deterministic under a fake loop.
    Returns:    ``ok()`` returns ``True`` while quota is healthy and ``False``
        once tripped. The trip is permanent for the gate's lifetime: even if a
        later poll reports recovered quota, ``ok()`` stays ``False`` (a tripped
        batch must wind down, not resume mid-flight).
    """

    def __init__(
        self,
        client: ShioajiClient,
        min_remaining_mb: float,
        ttl_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._min = min_remaining_mb
        self._ttl = ttl_seconds
        self._tripped = False
        self._last_check: float | None = None
        self._cached_remaining: float | None = None

    @property
    def tripped(self) -> bool:
        """Whether the gate has latched closed (quota fell below the floor)."""
        return self._tripped

    async def ok(self) -> bool:
        """Return whether work may proceed; query usage at most once per TTL.

        Definition: Reports ``True`` while the batch may keep launching work.
        Formula:    Re-polls ``/api/account/usage`` only when
                    ``loop.time() - last_check > ttl`` (event-loop clock); trips
                    permanently when ``remaining_mb < min_remaining_mb``.
        Domain:     Once ``tripped`` is set it short-circuits to ``False`` and
                    never re-queries. A failed usage poll is treated as healthy
                    (do not block a fetch for a monitoring hiccup), matching the
                    legacy ``check_quota`` fail-open behaviour.
        Returns:    ``not self._tripped``.
        """
        if self._tripped:
            return False

        loop = asyncio.get_event_loop()
        now = loop.time()
        stale = self._last_check is None or (now - self._last_check) > self._ttl
        if stale:
            self._last_check = now
            try:
                usage = await self._client.get_usage()
                remaining_mb = usage["remaining_mb"]
                self._cached_remaining = remaining_mb
                if remaining_mb < self._min:
                    print(
                        f"    QUOTA TRIPPED: remaining {remaining_mb:.1f} MB "
                        f"< threshold {self._min:.1f} MB — winding down batch"
                    )
                    self._tripped = True
            except Exception as e:
                # Fail open: a usage-endpoint hiccup must not stall the batch.
                print(f"    WARNING: quota check failed: {e!r}")

        return not self._tripped


async def write_instrument_def_one(
    client_url: str, code: str, catalog: ParquetDataCatalog
) -> TickerResult:
    """Load and write one NT instrument definition to the catalog."""
    instrument_id = InstrumentId(Symbol(code), VENUE)
    instrument = await load_instrument(client_url, instrument_id)
    catalog.write_data([instrument])
    return TickerResult(code=code, status="complete", n_written=1)


async def fetch_bars_one(
    client: ShioajiClient,
    gateway_url: str,
    code: str,
    start: date,
    end: date,
    catalog: ParquetDataCatalog,
) -> TickerResult:
    """Probe, side-write the instrument def, then download 1-min bars for one stock."""
    instrument_id = InstrumentId(Symbol(code), VENUE)
    bar_type = BarType(instrument_id, BAR_SPEC)

    print(f"Probing {code}...")
    ok = await probe_kbar_availability(client, code, end)
    if not ok:
        print(f"{code}: no kbar data available")
        return TickerResult(code=code, status="no_data")

    instrument = await load_instrument(gateway_url, instrument_id)
    catalog.write_data([instrument])

    print(f"Fetching {code} {start}→{end}...")
    n_bars = await fetch_stock_bars(client, code, bar_type, start, end, catalog)
    print(f"Done: {n_bars} bars written")
    return TickerResult(code=code, status="complete", n_written=n_bars, last_date=end)


async def fetch_ticks_one(
    client: ShioajiClient,
    gateway_url: str,
    code: str,
    start: date,
    end: date,
    catalog: ParquetDataCatalog,
    gate: QuotaGate,
) -> TickerResult:
    """Probe, side-write the instrument def, then download trade ticks day-by-day.

    Quota-aware via a **shared** :class:`QuotaGate`: stops launching new days
    once the gate trips (batch-wide remaining quota below its floor); returns
    ``partial`` with the last completed date so the caller can resume with
    ``--start <last_date + 1 day>``. The gate centralizes and throttles the
    ``/api/account/usage`` poll, so many concurrent tickers share one quota
    view instead of each calling ``check_quota``.
    """
    instrument_id = InstrumentId(Symbol(code), VENUE)

    # Pre-flight quota check (shared gate). A pre-flight trip means zero days
    # were completed, so last_date stays None — the caller resumes from --start.
    print("Checking quota...")
    if not await gate.ok():
        return TickerResult(code=code, status="partial")

    print(f"Probing {code}...")
    ok = await probe_kbar_availability(client, code, end)
    if not ok:
        print(f"{code}: no data available")
        return TickerResult(code=code, status="no_data")

    instrument = await load_instrument(gateway_url, instrument_id)
    catalog.write_data([instrument])

    print(f"Fetching ticks for {code} {start} → {end}...")
    total_ticks = 0
    total_skipped = 0
    consecutive_errors = 0
    consecutive_empty = 0
    days = trading_days(start, end)
    last_date: date | None = None
    days_with_data = 0
    empty_days = 0
    stopped_early = False

    for i, day in enumerate(days):
        # Quota check via the shared gate. The gate self-throttles the actual
        # /api/account/usage poll (TTL-bounded on the event-loop clock), so this
        # is cheap to call every day; it returns False once the batch trips.
        if not await gate.ok():
            print(f"    Quota exhausted at {day}. Resume with --start {day}")
            stopped_early = True
            break

        day_str = day.isoformat()
        try:
            tick_resp = await client.get_ticks(code, day_str)
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors <= 2:
                print(f"    ERROR {day_str}: {e!r}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"    SKIP: {MAX_CONSECUTIVE_ERRORS} consecutive errors at {day}")
                stopped_early = True
                break
            continue

        consecutive_errors = 0
        ts_list = tick_resp.get("ts", [])
        if not ts_list:
            consecutive_empty += 1
            empty_days += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                print(f"    STOP: {MAX_CONSECUTIVE_EMPTY} consecutive empty weekdays "
                      f"({day - timedelta(days=MAX_CONSECUTIVE_EMPTY * 2)} → {day}). "
                      f"Likely quota exhausted. Resume with --start {day}")
                stopped_early = True
                break
            continue

        consecutive_empty = 0
        trade_ticks, n_skipped = ticks_to_trade_ticks(tick_resp, instrument_id)
        total_skipped += n_skipped
        if trade_ticks:
            trade_ticks.sort(key=lambda t: t.ts_init)
            catalog.write_data(trade_ticks)
            total_ticks += len(trade_ticks)
            days_with_data += 1
            last_date = day

        if (i + 1) % 50 == 0:
            print(f"    progress: {i + 1}/{len(days)} days, "
                  f"{days_with_data} with data, {empty_days} empty, "
                  f"{total_ticks} ticks so far ({total_skipped} invalid skipped)")

    if total_skipped:
        print(f"    filtered {total_skipped} invalid tick(s) total "
              f"(volume<=0 or price<=0)")

    print(f"Done: {total_ticks} ticks written")
    if last_date and last_date < end:
        next_start = last_date + timedelta(days=1)
        print(f"Resume tomorrow with: --start {next_start}")

    # Final quota report
    await check_quota(client, 0)

    status = "partial" if stopped_early else "complete"
    return TickerResult(
        code=code, status=status, n_written=total_ticks, last_date=last_date
    )


async def run_batch(
    codes: Sequence[str],
    concurrency: int,
    per_ticker: Callable[[str], Awaitable[TickerResult]],
) -> list[TickerResult]:
    """Run ``per_ticker`` over ``codes`` with bounded concurrency, isolating failures.

    Definition: Bounded-concurrency orchestrator shared by every fetch
        subcommand. Launches at most ``concurrency`` ticker coroutines at once
        and gathers their :class:`TickerResult`s; a single ticker may run as the
        degenerate batch of one.
    Domain:     Single-threaded asyncio. ``concurrency >= 1``. ``per_ticker`` is
        a closure binding the gateway/catalog/gate args, taking only the ticker
        ``code``. A coroutine that *raises* is contained — it does NOT cancel its
        siblings — and is reported as ``TickerResult(code, status="failed",
        error=str(e))``; the others still complete.
    Returns:    One :class:`TickerResult` per input code, in input order.
    """
    sem = asyncio.Semaphore(concurrency)

    async def guarded(code: str) -> TickerResult:
        async with sem:
            return await per_ticker(code)

    raw = await asyncio.gather(
        *(guarded(code) for code in codes), return_exceptions=True
    )

    results: list[TickerResult] = []
    for code, outcome in zip(codes, raw):
        if isinstance(outcome, BaseException):
            results.append(
                TickerResult(code=code, status="failed", error=str(outcome))
            )
        else:
            results.append(outcome)
    return results


def format_batch_report(results: Sequence[TickerResult]) -> str:
    """Render a batch summary line plus per-ticker resume hints.

    Definition: One-line status tally over a batch's :class:`TickerResult`s,
        followed by one resume hint per ``partial``/``failed`` ticker.
    Domain:     Resume hints are **per-ticker** because each ticker's
        ``last_date`` differs — a single merged ``--start`` would re-download
        already-fetched days for tickers that got further, overlapping catalog
        writes. A ``partial`` ticker that tripped quota pre-flight has
        ``last_date is None`` (zero days completed); its hint emits a
        ``--start <original>`` placeholder (the caller re-uses the run's
        original ``--start``) rather than crashing or emitting ``--start None``.
    Returns:    A multi-line string: ``"<n> complete, <n> partial, <n> no_data,
        <n> failed (<total>)"`` then ``"  ⚠ <code> → resume: shioaji-data
        ... --code <code> [--start <date>]"`` lines for non-clean tickers.
    """
    tally: dict[str, int] = {
        "complete": 0,
        "partial": 0,
        "no_data": 0,
        "failed": 0,
    }
    for r in results:
        tally[r.status] = tally.get(r.status, 0) + 1

    summary = (
        f"{tally['complete']} complete, {tally['partial']} partial, "
        f"{tally['no_data']} no_data, {tally['failed']} failed "
        f"({len(results)})"
    )
    lines = [summary]

    for r in results:
        if r.status not in ("partial", "failed"):
            continue
        mark = "⚠" if r.status == "partial" else "✗"
        hint = f"  {mark} {r.code} ({r.status})"
        if r.error:
            hint += f": {r.error}"
        # last_date present → resume the day after; None (pre-flight quota stop
        # / no progress) → fall back to the ticker's original --start.
        if r.last_date is not None:
            resume_start = r.last_date + timedelta(days=1)
            hint += (
                f" → resume: shioaji-data ... --code {r.code} "
                f"--start {resume_start.isoformat()}"
            )
        else:
            hint += f" → resume: shioaji-data ... --code {r.code} --start <original>"
        lines.append(hint)

    return "\n".join(lines)
