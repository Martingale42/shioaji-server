"""One-shot repair: restore NT metadata + column types stripped by the Batch 2 migration.

The Batch 2 timezone migration (``migrate_ts_to_utc.py``, commit ``7961596``)
round-tripped every bar/trade_tick parquet through polars, which:

1. **Stripped the NT key-value metadata** (``instrument_id``,
   ``price_precision``, ``size_precision``, ``bar_type``) — only
   ``ARROW:schema`` survived.
2. **Downcast column types**: ``fixed_size_binary[16]`` → ``large_binary``,
   ``string`` → ``large_string``.

This makes ``ParquetDataCatalog.query()`` raise
``MissingMetadata("instrument_id")``.  The timestamp *values* are already
correct (true UTC) — **this script must NOT shift them again**.

Strategy: read each damaged file, ``cast()`` columns back to the NT canonical
Arrow schema, re-attach the kv metadata (from the intact instrument
definitions), deserialize to NT objects via ``ArrowSerializer``, and
re-persist via ``catalog.write_data()`` into a fresh output catalog.

Usage::

    uv run python -m scripts.restamp_catalog_metadata --catalog-path ./catalog --dry-run
    uv run python -m scripts.restamp_catalog_metadata --catalog-path ./catalog --out-path ./catalog_restamped
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.serialization.arrow.serializer import ArrowSerializer

log = logging.getLogger("restamp_catalog_metadata")

MARKER_NAME = ".metadata_restamped"

# NT canonical Arrow schemas — column types that polars downcast during migration.
TICK_CANON = pa.schema([
    pa.field("price", pa.binary(16)),
    pa.field("size", pa.binary(16)),
    pa.field("aggressor_side", pa.uint8()),
    pa.field("trade_id", pa.string()),
    pa.field("ts_event", pa.uint64()),
    pa.field("ts_init", pa.uint64()),
])

BAR_CANON = pa.schema([
    pa.field("open", pa.binary(16)),
    pa.field("high", pa.binary(16)),
    pa.field("low", pa.binary(16)),
    pa.field("close", pa.binary(16)),
    pa.field("volume", pa.binary(16)),
    pa.field("ts_event", pa.uint64()),
    pa.field("ts_init", pa.uint64()),
])


@dataclass
class RestampStats:
    """Tally of files and rows processed during a restamp run."""

    instruments: int = 0
    tick_files: int = 0
    bar_files: int = 0
    tick_rows: int = 0
    bar_rows: int = 0
    errors: list[str] = field(default_factory=list)


def _repair_table(
    tbl: pa.Table,
    canon: pa.Schema,
    iid: str,
    pp: int,
    sp: int,
    *,
    bar_type: str | None = None,
) -> pa.Table:
    """Cast damaged columns back to NT canonical types and re-attach NT kv metadata.

    Definition: Restores the Arrow schema + file kv that polars stripped/downcast,
                WITHOUT touching any value (timestamps already corrected in Batch 2).
    Formula:    repaired = tbl.cast(canonical_schema).replace_schema_metadata(kv)
    Domain:     ``tbl`` has the damaged schema (large_binary/large_string); ``canon``
                is NT's exact schema for this data type; pp/sp from the instrument def.
                ``bar_type`` is required for Bar data (e.g. ``0050.SINOPAC-1-MINUTE-LAST-EXTERNAL``).
    Returns:    An NT-deserializable pyarrow Table with all required kv metadata.
    """
    kv: dict[bytes, bytes] = {
        b"instrument_id": iid.encode(),
        b"price_precision": str(pp).encode(),
        b"size_precision": str(sp).encode(),
    }
    if bar_type is not None:
        kv[b"bar_type"] = bar_type.encode()
    return tbl.cast(canon).replace_schema_metadata(kv)


def _process_trade_ticks(
    src_catalog: ParquetDataCatalog,
    out_catalog: ParquetDataCatalog,
    iid: str,
    pp: int,
    sp: int,
    *,
    dry_run: bool,
    stats: RestampStats,
) -> None:
    """Repair and re-persist all trade_tick files for one instrument."""
    tick_dir = Path(src_catalog.path) / "data" / "trade_tick" / iid
    if not tick_dir.exists():
        return

    for parquet_file in sorted(tick_dir.glob("*.parquet")):
        tbl = pq.read_table(parquet_file)
        repaired = _repair_table(tbl, TICK_CANON, iid, pp, sp)
        ticks = ArrowSerializer.deserialize(TradeTick, repaired)
        stats.tick_files += 1
        stats.tick_rows += len(ticks)
        log.info(
            "  trade_tick %s: %d rows, first ts_event=%s",
            parquet_file.name,
            len(ticks),
            datetime.fromtimestamp(ticks[0].ts_event / 1e9, tz=timezone.utc),
        )
        if not dry_run:
            out_catalog.write_data(ticks)


def _process_bars(
    src_catalog: ParquetDataCatalog,
    out_catalog: ParquetDataCatalog,
    iid: str,
    pp: int,
    sp: int,
    *,
    dry_run: bool,
    stats: RestampStats,
) -> None:
    """Repair and re-persist all bar files for one instrument."""
    bar_base = Path(src_catalog.path) / "data" / "bar"
    if not bar_base.exists():
        return

    # Bar directories are named <instrument_id>-<bar_spec>, e.g.
    # 0050.SINOPAC-1-MINUTE-LAST-EXTERNAL
    for bar_dir in sorted(bar_base.iterdir()):
        if not bar_dir.is_dir():
            continue
        if not bar_dir.name.startswith(iid):
            continue

        bar_type_str = bar_dir.name  # e.g. "0050.SINOPAC-1-MINUTE-LAST-EXTERNAL"

        for parquet_file in sorted(bar_dir.glob("*.parquet")):
            tbl = pq.read_table(parquet_file)
            repaired = _repair_table(
                tbl, BAR_CANON, iid, pp, sp, bar_type=bar_type_str
            )
            bars = ArrowSerializer.deserialize(Bar, repaired)
            stats.bar_files += 1
            stats.bar_rows += len(bars)
            log.info(
                "  bar %s/%s: %d rows, first ts_event=%s",
                bar_type_str,
                parquet_file.name,
                len(bars),
                datetime.fromtimestamp(bars[0].ts_event / 1e9, tz=timezone.utc),
            )
            if not dry_run:
                out_catalog.write_data(bars)


def restamp(
    catalog_path: Path,
    out_path: Path,
    *,
    dry_run: bool,
) -> RestampStats:
    """Run the full catalog metadata restamp (or a dry-run report).

    Definition: Reads each damaged parquet, casts columns to canonical types,
                re-attaches NT kv metadata, deserializes to NT objects, and
                re-persists via catalog.write_data() into a fresh output catalog.
    Domain:     ``catalog_path`` must be an NT ParquetDataCatalog with intact
                instrument definitions (equity defs not affected by Batch 2
                migration). Timestamp values are NOT modified.
    Returns:    RestampStats tallying processed files and rows.
    """
    marker = out_path / MARKER_NAME
    if marker.exists():
        log.warning(
            "Marker %s exists — output catalog already restamped. Aborting.",
            marker,
        )
        return RestampStats()

    src_catalog = ParquetDataCatalog(str(catalog_path))
    instruments = {i.id.value: i for i in src_catalog.instruments()}
    log.info("Found %d instruments: %s", len(instruments), list(instruments.keys()))

    stats = RestampStats(instruments=len(instruments))

    # Create output catalog (only on real runs) and carry instrument defs over
    out_catalog: ParquetDataCatalog | None = None
    if not dry_run:
        out_catalog = ParquetDataCatalog(str(out_path))
        out_catalog.write_data(list(instruments.values()))
        log.info("Wrote %d instrument definitions to output catalog", len(instruments))

    for iid, inst in instruments.items():
        pp = inst.price_precision
        sp = inst.size_precision
        log.info("Processing %s (price_precision=%d, size_precision=%d)", iid, pp, sp)

        try:
            _process_trade_ticks(
                src_catalog,
                out_catalog if out_catalog else src_catalog,  # type: ignore[arg-type]
                iid,
                pp,
                sp,
                dry_run=dry_run,
                stats=stats,
            )
            _process_bars(
                src_catalog,
                out_catalog if out_catalog else src_catalog,  # type: ignore[arg-type]
                iid,
                pp,
                sp,
                dry_run=dry_run,
                stats=stats,
            )
        except Exception:
            msg = f"Failed to process {iid}"
            log.exception(msg)
            stats.errors.append(msg)

    if not dry_run and not stats.errors:
        marker.write_text(
            f"restamped {datetime.now(timezone.utc).isoformat()}\n"
            f"source={catalog_path}\n"
            f"instruments={stats.instruments}\n"
            f"tick_files={stats.tick_files} tick_rows={stats.tick_rows}\n"
            f"bar_files={stats.bar_files} bar_rows={stats.bar_rows}\n"
        )
        log.info("Wrote marker: %s", marker)

    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Restore NT metadata + column types stripped by the Batch 2 migration"
    )
    parser.add_argument(
        "--catalog-path", required=True, help="Path to the damaged source catalog"
    )
    parser.add_argument(
        "--out-path",
        default=None,
        help="Output catalog path (default: <catalog-path>_restamped)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts, write nothing",
    )
    args = parser.parse_args()

    catalog_path = Path(args.catalog_path).resolve()
    if not catalog_path.exists():
        log.error("Catalog not found: %s", catalog_path)
        raise SystemExit(1)

    out_path = Path(args.out_path).resolve() if args.out_path else catalog_path.with_name(
        catalog_path.name + "_restamped"
    )

    mode = "DRY-RUN" if args.dry_run else "RESTAMP"
    log.info("%s source=%s out=%s", mode, catalog_path, out_path)

    stats = restamp(catalog_path, out_path, dry_run=args.dry_run)

    log.info(
        "%s complete: instruments=%d tick_files=%d tick_rows=%d "
        "bar_files=%d bar_rows=%d errors=%d",
        mode,
        stats.instruments,
        stats.tick_files,
        stats.tick_rows,
        stats.bar_files,
        stats.bar_rows,
        len(stats.errors),
    )
    if stats.errors:
        for err in stats.errors:
            log.error("  %s", err)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
