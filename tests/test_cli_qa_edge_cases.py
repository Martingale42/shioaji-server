"""User-perspective QA edge-case tests for the ``shioaji-data`` CLI.

These complement the implementation's own ``test_data_cli.py`` /
``test_batch_fetch.py`` by exercising the failure-mode contracts a real
operator hits: mutual-exclusion / required ticker selectors, comment+blank
``--codes-file`` hygiene, gateway-down friendly exit, mid-batch quota trip
(partial status + per-ticker resume hints + exit 2), per-ticker failure
isolation, and the no_data-vs-failed distinction.

Every test is fully offline: gateway, client, and catalog are monkeypatched
or faked, so no network or live Shioaji session is touched.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from shioaji_server.data import cli
from shioaji_server.data.fetch import (
    QuotaGate,
    TickerResult,
    fetch_ticks_one,
    format_batch_report,
    run_batch,
)


# --------------------------------------------------------------------------- #
# Ticker selector: mutual exclusion + required-ness (argparse SystemExit)
# --------------------------------------------------------------------------- #


def test_code_and_codes_together_systemexit() -> None:
    """`--code` and `--codes` together is rejected by the XOR group."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fetch-bars", "--code", "0050", "--codes", "0050"])


def test_fetch_bars_no_selector_systemexit() -> None:
    """fetch-bars with no ticker selector at all is a SystemExit (required group)."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fetch-bars"])


def test_code_and_codes_file_together_systemexit() -> None:
    """`--code` and `--codes-file` together is also rejected (full 3-way XOR)."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["fetch-ticks", "--code", "0050", "--codes-file", "/tmp/x.txt"]
        )


# --------------------------------------------------------------------------- #
# --codes-file: blanks AND # comments excluded; only real tickers survive
# --------------------------------------------------------------------------- #


def test_resolve_codes_file_excludes_blanks_and_comments(tmp_path) -> None:
    """resolve_codes returns only real tickers; blank + # comment lines dropped."""
    f = tmp_path / "tickers.txt"
    f.write_text(
        "# header comment\n"
        "0050\n"
        "\n"
        "   \n"
        "# another comment\n"
        "00631L\n"
        "  # indented comment\n"
        "2330\n",
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fetch-bars", "--codes-file", str(f)])
    assert cli.resolve_codes(args) == ["0050", "00631L", "2330"]


# --------------------------------------------------------------------------- #
# Gateway unreachable -> main() returns 1 with a friendly message
# --------------------------------------------------------------------------- #


def test_gateway_unreachable_returns_1(monkeypatch, capsys) -> None:
    """A ConnectError from the health probe -> exit 1 + friendly hint printed."""

    async def boom(gateway_url: str) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cli, "_check_gateway", boom)

    rc = cli.main(["fetch-ticks", "--code", "0050", "--gateway-url", "http://localhost:9999"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "gateway not reachable" in out
    assert "http://localhost:9999" in out


# --------------------------------------------------------------------------- #
# Quota tripped mid-batch -> partial status + per-ticker resume hints + exit 2
# --------------------------------------------------------------------------- #


class FakeTicksClient:
    """Fake ShioajiGatewayClient for fetch_ticks_one: scripts usage + per-day ticks.

    ``remaining_sequence`` drives ``get_usage`` (one value per call, last
    repeats). ``get_ticks`` returns one synthetic executed trade per requested
    day so progress (last_date) advances until the quota gate trips.
    """

    def __init__(self, remaining_sequence: list[float]) -> None:
        self._seq = list(remaining_sequence)
        self.usage_calls = 0

    async def get_usage(self) -> dict:
        self.usage_calls += 1
        idx = min(self.usage_calls - 1, len(self._seq) - 1)
        remaining = self._seq[idx]
        return {
            "remaining_mb": remaining,
            "used_mb": 500.0 - remaining,
            "limit_mb": 500.0,
            "remaining_pct": remaining / 500.0 * 100.0,
        }

    async def get_ticks(self, code: str, day: str) -> dict:
        # One valid executed trade for the requested day.
        ts_ns = int(date.fromisoformat(day).strftime("%s")) * 1_000_000_000
        return {
            "ts": [ts_ns],
            "close": [100.0],
            "volume": [10],
            "tick_type": [1],
        }


class _NoopCatalog:
    def write_data(self, _data) -> None:
        return None


async def test_quota_trip_mid_batch_partial_resume_and_exit2(monkeypatch) -> None:
    """Mid-batch quota trip -> status=partial, per-ticker resume hint, exit-2 logic.

    Uses the real fetch_ticks_one + a shared QuotaGate, with probe + instrument
    write stubbed so only the quota/day-loop logic exercises. The gate is
    primed healthy for the pre-flight + first day, then drops below the floor so
    the day loop stops early -> partial with a last_date set.
    """
    # Probe always true; instrument write a no-op (avoid NT instrument loading).
    async def fake_probe(_client, _code, _end) -> bool:
        return True

    async def fake_load_instrument(_url, _iid):
        return object()

    import shioaji_server.data.fetch as fetchmod

    monkeypatch.setattr(fetchmod, "probe_kbar_availability", fake_probe)
    monkeypatch.setattr(fetchmod, "load_instrument", fake_load_instrument)

    # Sequence: pre-flight ok (400), day-1 ok (400), day-2 trips (10), then 10s.
    client = FakeTicksClient(remaining_sequence=[400.0, 400.0, 10.0, 10.0])
    gate = QuotaGate(client, min_remaining_mb=50.0, ttl_seconds=0.0)  # ttl=0 -> poll every call

    start = date(2024, 3, 4)  # Monday
    end = date(2024, 3, 8)  # Friday — several trading days

    result = await fetch_ticks_one(
        client, "http://x", "2330", start, end, _NoopCatalog(), gate=gate
    )

    assert result.status == "partial"
    assert result.last_date is not None  # progressed at least one day before trip

    # format_batch_report renders a per-ticker resume hint with the next day.
    report = format_batch_report([result])
    assert "partial" in report
    assert "--code 2330 --start" in report
    assert "--start None" not in report

    # Exit-code logic: any non-complete -> not all_complete -> CLI returns 2.
    all_complete = all(r.status == "complete" for r in [result])
    assert all_complete is False  # CLI maps this to exit 2


# --------------------------------------------------------------------------- #
# One raising ticker in a batch -> others still complete (failure isolation)
# --------------------------------------------------------------------------- #


async def test_one_raising_ticker_others_complete() -> None:
    """run_batch isolates a raising ticker; siblings still complete; exit-2 logic."""

    async def per_ticker(code: str) -> TickerResult:
        if code == "BOOM":
            raise RuntimeError("simulated tick fetch crash")
        return TickerResult(code=code, status="complete", n_written=3)

    codes = ["0050", "BOOM", "2330", "00631L"]
    results = await run_batch(codes, concurrency=4, per_ticker=per_ticker)

    by_code = {r.code: r for r in results}
    assert by_code["BOOM"].status == "failed"
    assert by_code["BOOM"].error == "simulated tick fetch crash"
    assert by_code["0050"].status == "complete"
    assert by_code["2330"].status == "complete"
    assert by_code["00631L"].status == "complete"
    # Order + cardinality preserved.
    assert [r.code for r in results] == codes
    # A failed ticker means the CLI returns exit 2.
    assert all(r.status == "complete" for r in results) is False


# --------------------------------------------------------------------------- #
# Unknown ticker -> status=no_data (NOT failed); distinct from exceptions
# --------------------------------------------------------------------------- #


async def test_unknown_ticker_is_no_data_not_failed(monkeypatch) -> None:
    """A probe-false (unknown) ticker yields no_data; an exception yields failed."""
    import shioaji_server.data.fetch as fetchmod

    # Probe returns False (ticker has no data) — must NOT raise.
    async def fake_probe_false(_client, _code, _end) -> bool:
        return False

    async def fake_load_instrument(_url, _iid):
        return object()

    monkeypatch.setattr(fetchmod, "probe_kbar_availability", fake_probe_false)
    monkeypatch.setattr(fetchmod, "load_instrument", fake_load_instrument)

    client = FakeTicksClient(remaining_sequence=[400.0])
    gate = QuotaGate(client, min_remaining_mb=50.0, ttl_seconds=10.0)

    result = await fetch_ticks_one(
        client, "http://x", "9999", date(2024, 3, 4), date(2024, 3, 8),
        _NoopCatalog(), gate=gate,
    )
    assert result.status == "no_data"
    assert result.status != "failed"

    # Contrast: an exception inside per_ticker becomes status=failed via run_batch.
    async def raising(code: str) -> TickerResult:
        raise ValueError("kaboom")

    failed = await run_batch(["X"], concurrency=1, per_ticker=raising)
    assert failed[0].status == "failed"
    # no_data and failed are distinct statuses.
    assert result.status != failed[0].status
