"""Tests for the WS-D catalog instrument-definition regeneration (mocked).

These exercise the regen *logic* against a throwaway temp catalog seeded with a
legacy-style Equity (0.01 tick / 1000 lot) plus a few bars and trade ticks. The
SinoPac provider is mocked to return a corrected definition (0.05 tick / 2000
lot) — no live gateway and no rebuilt adapter wheel are required.

The decisive assertion is the data red line: after regen, the bar/trade_tick
row counts and first ``ts_event`` must be byte-for-byte identical, while the
instrument definition's tick/lot are the corrected provider values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nautilus_trader.model.data import Bar, BarSpecification, BarType, TradeTick
from nautilus_trader.model.enums import AggressorSide, BarAggregation, PriceType
from nautilus_trader.model.identifiers import (
    InstrumentId,
    Symbol,
    TradeId,
    Venue,
)
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.maintenance import regen_catalog_instruments as regen

VENUE = Venue("SINOPAC")
TWD = Currency.from_str("TWD")
IID = InstrumentId(Symbol("0050"), VENUE)


def _legacy_equity() -> Equity:
    """Legacy-style Equity with the hardcoded 0.01 tick / 1000 lot."""
    return Equity(
        instrument_id=IID,
        raw_symbol=Symbol("0050"),
        currency=TWD,
        price_precision=2,
        price_increment=Price(0.01, precision=2),
        lot_size=Quantity(1000, precision=0),
        ts_event=0,
        ts_init=0,
    )


def _corrected_equity() -> Equity:
    """Provider-style Equity with a corrected (tier-derived) tick / lot."""
    return Equity(
        instrument_id=IID,
        raw_symbol=Symbol("0050"),
        currency=TWD,
        price_precision=2,
        price_increment=Price(0.05, precision=2),
        lot_size=Quantity(2000, precision=0),
        ts_event=0,
        ts_init=0,
    )


def _seed_catalog(catalog_path: Path) -> tuple[int, int, int, int]:
    """Seed a temp catalog with legacy equity + bars + ticks.

    Returns (tick_count, tick_first_ts, bar_count, bar_first_ts).
    """
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([_legacy_equity()])

    base_ns = 1_583_110_801_187_000_000
    ticks = [
        TradeTick(
            instrument_id=IID,
            price=Price(100.00 + i, precision=2),
            size=Quantity(1000, precision=0),
            aggressor_side=AggressorSide.BUYER,
            trade_id=TradeId(f"0050-{base_ns + i}"),
            ts_event=base_ns + i * 1_000_000,
            ts_init=base_ns + i * 1_000_000,
        )
        for i in range(5)
    ]
    catalog.write_data(ticks)

    bar_type = BarType(
        IID, BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)
    )
    bars = [
        Bar(
            bar_type=bar_type,
            open=Price(100.00, precision=2),
            high=Price(101.00, precision=2),
            low=Price(99.00, precision=2),
            close=Price(100.50, precision=2),
            volume=Quantity(10000, precision=0),
            ts_event=base_ns + i * 60_000_000_000,
            ts_init=base_ns + i * 60_000_000_000,
        )
        for i in range(3)
    ]
    catalog.write_data(bars)

    return (len(ticks), ticks[0].ts_event, len(bars), bars[0].ts_event)


@pytest.fixture
def patched_loader(monkeypatch: pytest.MonkeyPatch):
    """Patch load_instrument so regen does not need a live gateway/adapter."""

    async def _fake_load(gateway_url: str, instrument_id: InstrumentId) -> Equity:
        assert instrument_id == IID
        return _corrected_equity()

    monkeypatch.setattr(regen, "load_instrument", _fake_load)


async def test_dry_run_writes_nothing(
    tmp_path: Path, patched_loader
):
    """Dry-run reports instruments but mutates neither data nor definition."""
    catalog_path = tmp_path / "catalog"
    tc, t0, bc, b0 = _seed_catalog(catalog_path)

    stats = await regen.regenerate(
        catalog_path, "http://localhost:8000", dry_run=True
    )

    assert stats.instruments == 1
    assert stats.regenerated == 0
    assert not (catalog_path / regen.MARKER_NAME).exists()
    assert not (tmp_path / regen.BACKUP_DIR_NAME).exists()

    # Definition unchanged (still legacy), data unchanged.
    catalog = ParquetDataCatalog(str(catalog_path))
    inst = catalog.instruments()[0]
    assert str(inst.price_increment) == "0.01"
    assert str(inst.lot_size) == "1000"
    assert len(catalog.query(data_cls=TradeTick, identifiers=["0050.SINOPAC"])) == tc
    assert len(catalog.query(data_cls=Bar, identifiers=["0050.SINOPAC"])) == bc


async def test_real_regen_fixes_def_and_preserves_data(
    tmp_path: Path, patched_loader
):
    """Real regen corrects tick/lot and leaves bar/tick data byte-identical."""
    catalog_path = tmp_path / "catalog"
    tc, t0, bc, b0 = _seed_catalog(catalog_path)

    stats = await regen.regenerate(
        catalog_path, "http://localhost:8000", dry_run=False
    )

    assert stats.instruments == 1
    assert stats.regenerated == 1
    assert not stats.errors

    # Backup created, marker written.
    backup = tmp_path / regen.BACKUP_DIR_NAME
    assert backup.exists()
    assert (catalog_path / regen.MARKER_NAME).exists()

    # Definition now corrected.
    catalog = ParquetDataCatalog(str(catalog_path))
    inst = catalog.instruments()[0]
    assert str(inst.price_increment) == "0.05"
    assert str(inst.lot_size) == "2000"

    # DATA RED LINE: counts + first ts_event identical before vs after.
    ticks = catalog.query(data_cls=TradeTick, identifiers=["0050.SINOPAC"])
    bars = catalog.query(data_cls=Bar, identifiers=["0050.SINOPAC"])
    assert len(ticks) == tc
    assert ticks[0].ts_event == t0
    assert len(bars) == bc
    assert bars[0].ts_event == b0

    # Fingerprints recorded in stats also confirm immutability.
    fp_before = stats.before["0050.SINOPAC"]
    fp_after = stats.after["0050.SINOPAC"]
    assert fp_before == fp_after


async def test_idempotent_marker_aborts_second_run(
    tmp_path: Path, patched_loader
):
    """A second real run is a no-op once the marker exists."""
    catalog_path = tmp_path / "catalog"
    _seed_catalog(catalog_path)

    await regen.regenerate(catalog_path, "http://localhost:8000", dry_run=False)
    # Second run: marker present → aborts without re-regenerating.
    stats2 = await regen.regenerate(
        catalog_path, "http://localhost:8000", dry_run=False
    )
    assert stats2.regenerated == 0


async def test_aborts_if_backup_dir_already_exists(
    tmp_path: Path, patched_loader
):
    """Refuse to clobber a pre-existing backup dir (preserve prior backup)."""
    catalog_path = tmp_path / "catalog"
    _seed_catalog(catalog_path)
    (tmp_path / regen.BACKUP_DIR_NAME).mkdir()

    stats = await regen.regenerate(
        catalog_path, "http://localhost:8000", dry_run=False
    )
    assert stats.regenerated == 0
    assert stats.errors
    assert not (catalog_path / regen.MARKER_NAME).exists()


async def test_id_suffix_filters_to_matching_instruments(
    tmp_path: Path, patched_loader
):
    """--id-suffix scopes regen to matching ids (skip non-SinoPac in a shared catalog)."""
    catalog_path = tmp_path / "catalog"
    _seed_catalog(catalog_path)  # writes 0050.SINOPAC
    # A non-SinoPac instrument that the suffix filter must exclude.
    other = Equity(
        instrument_id=InstrumentId(Symbol("BTC"), Venue("BYBIT")),
        raw_symbol=Symbol("BTC"),
        currency=TWD,
        price_precision=2,
        price_increment=Price(0.01, precision=2),
        lot_size=Quantity(1, precision=0),
        ts_event=0,
        ts_init=0,
    )
    ParquetDataCatalog(str(catalog_path)).write_data([other])

    stats = await regen.regenerate(
        catalog_path,
        "http://localhost:8000",
        dry_run=True,
        id_suffix=".SINOPAC",
    )
    assert stats.instruments == 1
    assert set(stats.before) == {"0050.SINOPAC"}
