"""Tests for scripts.restamp_catalog_metadata — data correctness is a red line.

Verifies that ``_repair_table`` restores NT-deserializability from a
polars-damaged parquet without changing any timestamp or price value.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import polars as pl

from nautilus_trader.model.data import Bar, BarType, BarSpecification, TradeTick
from nautilus_trader.model.enums import (
    AggregationSource,
    AggressorSide,
    BarAggregation,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.serialization.arrow.serializer import ArrowSerializer

from scripts.maintenance.restamp_catalog_metadata import (
    BAR_CANON,
    TICK_CANON,
    _repair_table,
)


# ---------------------------------------------------------------------------
# Helpers: create NT objects, write via NT, damage via polars round-trip
# ---------------------------------------------------------------------------


def _make_tick(
    iid: str = "TEST.VENUE",
    price_str: str = "138.05",
    size_str: str = "10",
    ts_event: int = 1618189202891743000,  # 2021-04-12 01:00:02 UTC
    ts_init: int = 1618189202891743000,
) -> TradeTick:
    return TradeTick(
        instrument_id=InstrumentId.from_str(iid),
        price=Price.from_str(price_str),
        size=Quantity.from_str(size_str),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId("test-trade-1"),
        ts_event=ts_event,
        ts_init=ts_init,
    )


def _make_bar(
    iid: str = "TEST.VENUE",
    close_str: str = "195.35",
    ts_event: int = 1735779660000000000,  # 2025-01-02 01:01:00 UTC
    ts_init: int = 1735779660000000000,
) -> Bar:
    bar_type = BarType(
        instrument_id=InstrumentId.from_str(iid),
        bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
    return Bar(
        bar_type=bar_type,
        open=Price.from_str("190.00"),
        high=Price.from_str("196.00"),
        low=Price.from_str("189.50"),
        close=Price.from_str(close_str),
        volume=Quantity.from_str("1000"),
        ts_event=ts_event,
        ts_init=ts_init,
    )


def _write_nt_and_damage(
    objects: list,
    data_cls: type,
    subdir: str,
) -> tuple[Path, pa.Table]:
    """Write NT objects to a temp catalog, then damage via polars round-trip.

    Definition: Simulates the Batch 2 migration's polars round-trip that strips
                NT kv metadata and downcasts column types.
    Formula:    write_data() → read via polars → write via polars → read back
    Domain:     ``objects`` are valid NT data objects; ``subdir`` names the
                data subdirectory (e.g. ``trade_tick/TEST.VENUE``).
    Returns:    (damaged_file_path, original_table) where original_table is the
                NT-written table before damage (for comparison).
    """
    tmpdir = tempfile.mkdtemp()
    # Step 1: Write via NT catalog (gets proper schema + kv)
    nt_catalog = ParquetDataCatalog(tmpdir)
    nt_catalog.write_data(objects)

    # Find the written file
    import glob
    files = glob.glob(f"{tmpdir}/data/{subdir}/**/*.parquet", recursive=True)
    assert files, f"No parquet files found in {tmpdir}/data/{subdir}"
    nt_file = files[0]

    # Read the NT-written table (original, for comparison)
    original_table = pq.read_table(nt_file)

    # Step 2: Damage via polars round-trip (simulates migrate_ts_to_utc.py)
    df = pl.read_parquet(nt_file)
    damaged_path = Path(tmpdir) / "damaged.parquet"
    df.write_parquet(damaged_path)

    return damaged_path, original_table


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRepairTradeTick:
    """Verify _repair_table restores TradeTick NT-deserializability."""

    def test_repair_succeeds_and_values_match(self) -> None:
        """Repair a polars-damaged TradeTick file and verify prices/timestamps
        are byte-identical to the original NT-written table."""
        tick = _make_tick()
        damaged_path, original_table = _write_nt_and_damage(
            [tick], TradeTick, "trade_tick/TEST.VENUE"
        )

        # Confirm damage: read damaged file, check it lacks NT metadata
        damaged_tbl = pq.read_table(damaged_path)
        damaged_meta = damaged_tbl.schema.metadata or {}
        assert b"instrument_id" not in damaged_meta, "Expected damage: instrument_id should be stripped"

        # Repair
        repaired = _repair_table(damaged_tbl, TICK_CANON, "TEST.VENUE", pp=2, sp=0)

        # Verify kv metadata restored
        meta = repaired.schema.metadata
        assert meta[b"instrument_id"] == b"TEST.VENUE"
        assert meta[b"price_precision"] == b"2"
        assert meta[b"size_precision"] == b"0"

        # Verify column types restored
        for f in repaired.schema:
            orig_field = original_table.schema.field(f.name)
            assert f.type == orig_field.type, (
                f"Column {f.name}: expected {orig_field.type}, got {f.type}"
            )

        # Verify ArrowSerializer.deserialize succeeds
        ticks = ArrowSerializer.deserialize(TradeTick, repaired)
        assert len(ticks) == 1

        # Verify values are identical
        assert str(ticks[0].price) == "138.05"
        assert ticks[0].ts_event == tick.ts_event
        assert ticks[0].ts_init == tick.ts_init

    def test_ts_event_not_changed(self) -> None:
        """Critical: _repair_table must NOT modify ts_event/ts_init values."""
        tick = _make_tick()
        damaged_path, _ = _write_nt_and_damage(
            [tick], TradeTick, "trade_tick/TEST.VENUE"
        )
        damaged_tbl = pq.read_table(damaged_path)

        # Record original ts values from the damaged file (values are correct,
        # only schema is broken)
        orig_ts_event = damaged_tbl.column("ts_event")[0].as_py()
        orig_ts_init = damaged_tbl.column("ts_init")[0].as_py()

        repaired = _repair_table(damaged_tbl, TICK_CANON, "TEST.VENUE", pp=2, sp=0)

        # ts values must be byte-identical
        assert repaired.column("ts_event")[0].as_py() == orig_ts_event
        assert repaired.column("ts_init")[0].as_py() == orig_ts_init

        # Verify they decode to ~01:00 UTC (TW market open), NOT 09:00
        import datetime as d

        utc_time = d.datetime.fromtimestamp(orig_ts_event / 1e9, d.timezone.utc)
        assert utc_time.hour < 7, (
            f"ts_event decodes to hour {utc_time.hour} UTC — "
            "expected <7 (TW market hours under true UTC)"
        )


class TestRepairBar:
    """Verify _repair_table restores Bar NT-deserializability."""

    def test_repair_succeeds_and_values_match(self) -> None:
        """Repair a polars-damaged Bar file and verify prices/timestamps
        are byte-identical to the original NT-written table."""
        bar = _make_bar()
        bar_type_str = "TEST.VENUE-1-MINUTE-LAST-EXTERNAL"
        damaged_path, original_table = _write_nt_and_damage(
            [bar], Bar, f"bar/{bar_type_str}"
        )

        damaged_tbl = pq.read_table(damaged_path)
        damaged_meta = damaged_tbl.schema.metadata or {}
        assert b"bar_type" not in damaged_meta, "Expected damage: bar_type should be stripped"

        repaired = _repair_table(
            damaged_tbl,
            BAR_CANON,
            "TEST.VENUE",
            pp=2,
            sp=0,
            bar_type=bar_type_str,
        )

        # Verify kv metadata restored
        meta = repaired.schema.metadata
        assert meta[b"instrument_id"] == b"TEST.VENUE"
        assert meta[b"bar_type"] == bar_type_str.encode()
        assert meta[b"price_precision"] == b"2"
        assert meta[b"size_precision"] == b"0"

        # Verify ArrowSerializer.deserialize succeeds
        bars = ArrowSerializer.deserialize(Bar, repaired)
        assert len(bars) == 1

        # Verify values
        assert str(bars[0].close) == "195.35"
        assert bars[0].ts_event == bar.ts_event
        assert bars[0].ts_init == bar.ts_init

    def test_ts_event_not_changed(self) -> None:
        """Critical: _repair_table must NOT modify ts_event/ts_init values."""
        bar = _make_bar()
        bar_type_str = "TEST.VENUE-1-MINUTE-LAST-EXTERNAL"
        damaged_path, _ = _write_nt_and_damage(
            [bar], Bar, f"bar/{bar_type_str}"
        )
        damaged_tbl = pq.read_table(damaged_path)

        orig_ts_event = damaged_tbl.column("ts_event")[0].as_py()
        orig_ts_init = damaged_tbl.column("ts_init")[0].as_py()

        repaired = _repair_table(
            damaged_tbl,
            BAR_CANON,
            "TEST.VENUE",
            pp=2,
            sp=0,
            bar_type=bar_type_str,
        )

        assert repaired.column("ts_event")[0].as_py() == orig_ts_event
        assert repaired.column("ts_init")[0].as_py() == orig_ts_init
