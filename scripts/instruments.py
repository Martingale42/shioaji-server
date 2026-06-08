"""Same-source instrument loading via the SinoPac NautilusTrader adapter.

This module is the single source of truth for building NT instrument
definitions in the backtest download pipeline. Instead of hand-rolling
``Equity`` objects with hardcoded ``0.01`` tick / ``1000`` lot (the legacy
``make_equity`` / ``contract_to_equity`` path), every script constructs the
pyo3 ``SinopacHttpClient`` pointed at the gateway, wraps it in
``SinopacInstrumentProvider``, and lets the **same Rust parse the live node
uses** produce the definition (reference-tiered tick, ``unit`` lot size,
authoritative multiplier/currency for futures/options).

Usage::

    from nautilus_trader.model.identifiers import InstrumentId
    from scripts.instruments import load_instrument

    inst = await load_instrument("http://localhost:8000",
                                 InstrumentId.from_str("2330.SINOPAC"))
    catalog.write_data([inst])
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.adapters.sinopac.providers import (
        SinopacInstrumentProvider,
    )
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.instruments import Instrument


def _make_provider(gateway_url: str) -> SinopacInstrumentProvider:
    """Construct a SinopacInstrumentProvider pointed at the gateway.

    Definition: Builds the standalone pyo3 ``SinopacHttpClient`` (no live node
                required) and wraps it in the NT instrument provider.
    Domain:     ``gateway_url`` is the SinoPac gateway base URL
                (e.g. ``http://localhost:8000``); the gateway must be up and
                logged in for any subsequent ``load_*`` call to return data.
                The SinoPac adapter is imported lazily so unrelated functions
                in the download scripts stay importable in venvs without the
                rebuilt fork wheel installed.
    Returns:    A ready-to-load ``SinopacInstrumentProvider``.
    """
    from nautilus_trader.core import nautilus_pyo3
    from nautilus_trader.adapters.sinopac.providers import (
        SinopacInstrumentProvider,
    )

    client = nautilus_pyo3.sinopac.SinopacHttpClient(base_url=gateway_url)
    return SinopacInstrumentProvider(client=client)


async def load_instrument(
    gateway_url: str, instrument_id: InstrumentId
) -> Instrument:
    """Load one NT instrument definition from the gateway via the SinoPac adapter.

    Definition: Resolves a single ``InstrumentId`` to its NT instrument
                definition using the same Rust parse the live node uses.
    Domain:     ``gateway_url`` must point at a running, logged-in gateway;
                ``instrument_id`` must be a valid SinoPac contract id
                (e.g. ``2330.SINOPAC``). Raises if the contract is unknown.
    Returns:    The NT ``Instrument`` (``Equity`` / future / option) with
                reference-tiered ``price_increment``, ``unit`` ``lot_size``, and
                authoritative multiplier/currency — never the legacy hardcoded
                ``0.01`` tick / ``1000`` lot.
    """
    provider = _make_provider(gateway_url)
    await provider.load_async(instrument_id)
    inst = provider.find(instrument_id)
    if inst is None:
        raise RuntimeError(f"Instrument {instrument_id} not found via provider")
    return inst
