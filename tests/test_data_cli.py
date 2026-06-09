"""Offline tests for the ``shioaji-data`` CLI surface.

Covers the parser shape (four subcommands, the ``--code`` XOR ``--codes`` /
``--codes-file`` required group), code resolution from a file, the dispatch
wiring (``run_batch`` is invoked with the parsed codes + concurrency), and the
gateway-down exit path (exit 1). Every test is fully offline: the health probe,
``ShioajiClient``, ``ParquetDataCatalog``, and ``run_batch`` are monkeypatched,
so no gateway or network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from shioaji_server.data import cli
from shioaji_server.data.fetch import TickerResult


def test_parser_has_four_subcommands() -> None:
    """The parser exposes exactly the four planned subcommands."""
    parser = cli.build_parser()
    choices: set[str] = set()
    for action in parser._actions:
        if getattr(action, "choices", None):
            choices |= set(action.choices)
    assert {"fetch-bars", "fetch-ticks", "instrument-def", "inspect"} <= choices


def test_code_and_codes_mutually_exclusive() -> None:
    """Passing both --code and --codes to a fetch command is a SystemExit."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fetch-ticks", "--code", "0050", "--codes", "0050,2330"])


def test_ticker_selector_is_required() -> None:
    """A fetch command with no ticker selector is a SystemExit (required group)."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fetch-bars"])


def test_resolve_codes_from_file(tmp_path) -> None:
    """--codes-file yields the listed codes; blank and # comment lines are skipped."""
    codes_file = tmp_path / "tickers.txt"
    codes_file.write_text(
        "# liquidity tier A\n"
        "0050\n"
        "\n"
        "00631L\n"
        "  # trailing-note line\n"
        "2330\n"
        "   \n",
        encoding="utf-8",
    )
    parser = cli.build_parser()
    args = parser.parse_args(["fetch-ticks", "--codes-file", str(codes_file)])
    assert cli.resolve_codes(args) == ["0050", "00631L", "2330"]


def test_resolve_codes_from_codes_flag() -> None:
    """--codes comma-splits and drops empties/whitespace."""
    parser = cli.build_parser()
    args = parser.parse_args(["fetch-bars", "--codes", "0050, 2330 ,,00631L"])
    assert cli.resolve_codes(args) == ["0050", "2330", "00631L"]


def test_dispatch_routes_fetch_ticks(monkeypatch) -> None:
    """fetch-ticks dispatch calls run_batch with the parsed codes + concurrency."""
    captured: dict[str, object] = {}

    async def fake_check_gateway(gateway_url: str) -> None:
        return None

    async def fake_run_batch(codes, concurrency, per_ticker):
        captured["codes"] = list(codes)
        captured["concurrency"] = concurrency
        return [TickerResult(code=c, status="complete") for c in codes]

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def close(self) -> None:
            return None

    class FakeCatalog:
        def __init__(self, *a, **k) -> None:
            pass

    monkeypatch.setattr(cli, "_check_gateway", fake_check_gateway)
    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    monkeypatch.setattr(cli, "ShioajiClient", FakeClient)
    monkeypatch.setattr(cli, "ParquetDataCatalog", FakeCatalog)

    rc = cli.main(
        ["fetch-ticks", "--codes", "0050,00631L,2330", "--concurrency", "7"]
    )

    assert rc == 0
    assert captured["codes"] == ["0050", "00631L", "2330"]
    assert captured["concurrency"] == 7


def test_dispatch_partial_returns_exit_2(monkeypatch) -> None:
    """A batch with any non-complete ticker returns exit code 2."""

    async def fake_check_gateway(gateway_url: str) -> None:
        return None

    async def fake_run_batch(codes, concurrency, per_ticker):
        return [
            TickerResult(code="0050", status="complete"),
            TickerResult(code="2330", status="partial"),
        ]

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def close(self) -> None:
            return None

    class FakeCatalog:
        def __init__(self, *a, **k) -> None:
            pass

    monkeypatch.setattr(cli, "_check_gateway", fake_check_gateway)
    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    monkeypatch.setattr(cli, "ShioajiClient", FakeClient)
    monkeypatch.setattr(cli, "ParquetDataCatalog", FakeCatalog)

    rc = cli.main(["fetch-bars", "--codes", "0050,2330"])
    assert rc == 2


def test_main_returns_1_when_gateway_down(monkeypatch) -> None:
    """A failed health probe (ConnectError) prints a friendly message, exit 1."""

    async def boom(gateway_url: str) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cli, "_check_gateway", boom)

    rc = cli.main(["instrument-def", "--code", "0050"])
    assert rc == 1
