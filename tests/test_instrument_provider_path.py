"""Tests for the same-source instrument-loading path (WS-C).

These tests assert that the download scripts build NT instrument definitions
via ``SinopacInstrumentProvider`` (the same Rust parse the live node uses)
rather than the retired hardcoded ``make_equity`` / ``contract_to_equity``
builders.

The SinoPac adapter (``nautilus_trader.adapters.sinopac`` + the ``sinopac``
pyo3 submodule) is only present in a venv built from the rebuilt fork wheel.
To keep these unit tests runnable without that wheel (and without a live
gateway), we inject lightweight stand-in modules into ``sys.modules`` *before*
importing ``shioaji_server.data.instruments`` and then patch the helper's
collaborators.
No live data is fabricated — the provider is fully mocked and only its wiring
is asserted.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency, Price, Quantity

VENUE = Venue("SINOPAC")
TWD = Currency.from_str("TWD")
KNOWN_ID = InstrumentId(Symbol("2330"), VENUE)


def _known_equity() -> Equity:
    """A reference 2330.SINOPAC Equity with NON-legacy tick/lot (tier-derived).

    The legacy hardcoded builder produced price_increment=0.01 / lot_size=1000.
    Here we use 1.0 / 2000 to make it unambiguous that the provider's value
    (not a hardcoded default) is what flows through.
    """
    return Equity(
        instrument_id=KNOWN_ID,
        raw_symbol=Symbol("2330"),
        currency=TWD,
        price_precision=2,
        price_increment=Price(1.0, precision=2),
        lot_size=Quantity(2000, precision=0),
        ts_event=0,
        ts_init=0,
    )


@pytest.fixture
def instruments_module(monkeypatch: pytest.MonkeyPatch):
    """Import shioaji_server.data.instruments with the sinopac adapter stubbed.

    Yields the freshly imported module so each test gets a clean stub graph.
    """
    # Stub the pyo3 sinopac submodule: nautilus_pyo3.sinopac.SinopacHttpClient
    from nautilus_trader.core import nautilus_pyo3

    fake_sinopac_pyo3 = types.SimpleNamespace(
        SinopacHttpClient=MagicMock(name="SinopacHttpClient"),
    )
    monkeypatch.setattr(nautilus_pyo3, "sinopac", fake_sinopac_pyo3, raising=False)

    # Stub the Python provider module:
    # nautilus_trader.adapters.sinopac.providers.SinopacInstrumentProvider
    providers_mod = types.ModuleType(
        "nautilus_trader.adapters.sinopac.providers"
    )
    providers_mod.SinopacInstrumentProvider = MagicMock(
        name="SinopacInstrumentProvider"
    )
    sinopac_pkg = types.ModuleType("nautilus_trader.adapters.sinopac")
    sinopac_pkg.providers = providers_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(
        sys.modules, "nautilus_trader.adapters.sinopac", sinopac_pkg
    )
    monkeypatch.setitem(
        sys.modules,
        "nautilus_trader.adapters.sinopac.providers",
        providers_mod,
    )

    # (Re)import shioaji_server.data.instruments against the stubs.
    sys.modules.pop("shioaji_server.data.instruments", None)
    module = importlib.import_module("shioaji_server.data.instruments")
    yield module
    sys.modules.pop("shioaji_server.data.instruments", None)


@pytest.mark.asyncio
async def test_load_instrument_returns_provider_instrument(
    instruments_module, monkeypatch: pytest.MonkeyPatch
):
    """load_instrument returns exactly what the provider's find() yields."""
    equity = _known_equity()

    provider = MagicMock(name="provider_instance")
    provider.load_async = AsyncMock()
    provider.find = MagicMock(return_value=equity)

    monkeypatch.setattr(
        instruments_module, "_make_provider", lambda url: provider
    )

    result = await instruments_module.load_instrument(
        "http://localhost:8000", KNOWN_ID
    )

    assert result is equity
    # Provider value flows through unmodified — NOT the legacy 0.01 / 1000.
    assert str(result.price_increment) == "1.00"
    assert str(result.lot_size) == "2000"
    provider.load_async.assert_awaited_once_with(KNOWN_ID)
    provider.find.assert_called_once_with(KNOWN_ID)


@pytest.mark.asyncio
async def test_load_instrument_raises_when_not_found(
    instruments_module, monkeypatch: pytest.MonkeyPatch
):
    """load_instrument raises if the provider cannot resolve the id."""
    provider = MagicMock(name="provider_instance")
    provider.load_async = AsyncMock()
    provider.find = MagicMock(return_value=None)

    monkeypatch.setattr(
        instruments_module, "_make_provider", lambda url: provider
    )

    with pytest.raises(RuntimeError, match="not found via provider"):
        await instruments_module.load_instrument(
            "http://localhost:8000", KNOWN_ID
        )


def test_make_provider_wires_client_into_provider(
    instruments_module,
):
    """_make_provider constructs the pyo3 client and wraps it in the provider."""
    from nautilus_trader.core import nautilus_pyo3
    from nautilus_trader.adapters.sinopac import providers as providers_mod

    sentinel_client = object()
    nautilus_pyo3.sinopac.SinopacHttpClient.return_value = sentinel_client

    instruments_module._make_provider("http://localhost:8000")

    nautilus_pyo3.sinopac.SinopacHttpClient.assert_called_once_with(
        base_url="http://localhost:8000"
    )
    providers_mod.SinopacInstrumentProvider.assert_called_once_with(
        client=sentinel_client
    )


def test_script_path_writes_via_write_data(
    instruments_module, monkeypatch: pytest.MonkeyPatch
):
    """The download-script path writes the provider instrument via write_data.

    Simulates the rewired fetch_single call site: load_instrument(...) then
    catalog.write_data([inst]) — proving no hardcoded fields are injected.
    """
    equity = _known_equity()

    provider = MagicMock(name="provider_instance")
    provider.load_async = AsyncMock()
    provider.find = MagicMock(return_value=equity)
    monkeypatch.setattr(
        instruments_module, "_make_provider", lambda url: provider
    )

    catalog = MagicMock(name="catalog")

    async def _run() -> None:
        inst = await instruments_module.load_instrument(
            "http://localhost:8000", KNOWN_ID
        )
        catalog.write_data([inst])

    import asyncio

    asyncio.run(_run())

    catalog.write_data.assert_called_once_with([equity])


def test_legacy_hardcoded_builders_are_gone():
    """Grep-assert the retired builders no longer exist in the fetch module."""
    fetch_path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "shioaji_server"
        / "data"
        / "fetch.py"
    )
    fetch_src = fetch_path.read_text()

    assert "def make_equity" not in fetch_src
    assert "make_equity(" not in fetch_src
    assert "def contract_to_equity" not in fetch_src
    assert "contract_to_equity(" not in fetch_src

    # The consolidated fetch module routes instrument construction through the
    # shared same-source helper (relative import inside the package).
    assert "from .instruments import load_instrument" in fetch_src
