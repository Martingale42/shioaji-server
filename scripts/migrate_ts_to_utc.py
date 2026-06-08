"""One-time catalog migration: back-correct TW-as-UTC timestamps to true UTC.

Catalog parquet files written before the gateway timezone fix encode
``ts_event``/``ts_init`` as Taiwan wall-clock-as-UTC nanosecond epochs — 8h
ahead of true UTC (see ``docs/AUDIT.md`` §0). This script shifts both columns
back by 8h for every parquet under ``catalog/data/{bar,trade_tick}/<instrument>/``
and re-keys each NT filename to the corrected time range.

Idempotency: a marker file ``catalog/.ts_utc_migrated`` is written after a
successful real run. A second invocation is a no-op (refuses to double-shift)
unless ``--force`` is passed.

Usage::

    uv run python -m scripts.migrate_ts_to_utc --catalog-path ./catalog --dry-run
    uv run python -m scripts.migrate_ts_to_utc --catalog-path ./catalog
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

# Reuse NT's canonical filename builder so re-keyed names match exactly what the
# ParquetDataCatalog would write itself (single source of truth — no drift).
from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename

log = logging.getLogger("migrate_ts_to_utc")

TW_UTC_OFFSET_NS = 28_800_000_000_000  # 8 hours in nanoseconds
MARKER_NAME = ".ts_utc_migrated"
DATA_KINDS = ("bar", "trade_tick")
SHIFT_COLS = ("ts_event", "ts_init")


@dataclass
class MigrationStats:
    """Tally of files inspected/shifted/renamed during a migration run."""

    instruments: int = 0
    files_scanned: int = 0
    files_shifted: int = 0
    files_renamed: int = 0
    rows_shifted: int = 0


def _decode_utc(ns: int) -> datetime:
    """Decode a nanosecond epoch as a UTC datetime (for logging/verification)."""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def _shift_one_file(parquet_path: Path, *, dry_run: bool, stats: MigrationStats) -> None:
    """Shift ts_event/ts_init back 8h in one parquet and re-key its filename.

    Definition: Subtracts the TW->UTC offset from every timestamp column and
                rewrites the file under the NT-correct ``<start>_<end>.parquet``
                name derived from the shifted min/max ``ts_event``.
    Formula:    ts_utc = ts_tw - 28_800_000_000_000  (per column, per row)
    Domain:     `parquet_path` is an NT-format data parquet containing both
                ``ts_event`` and ``ts_init`` int64 ns columns. Caller guarantees
                idempotency (no double-shift) via the marker file.
    Returns:    None. Mutates the filesystem unless `dry_run`.
    """
    df = pl.read_parquet(parquet_path)
    stats.files_scanned += 1

    missing = [c for c in SHIFT_COLS if c not in df.columns]
    if missing:
        log.warning("Skipping %s: missing columns %s", parquet_path.name, missing)
        return

    shifted = df.with_columns(
        [(pl.col(c) - TW_UTC_OFFSET_NS).alias(c) for c in SHIFT_COLS]
    )
    stats.files_shifted += 1
    stats.rows_shifted += len(shifted)

    ts_sorted = shifted["ts_event"].sort()
    new_start = int(ts_sorted[0])
    new_end = int(ts_sorted[-1])
    new_name = _timestamps_to_filename(new_start, new_end)
    new_path = parquet_path.with_name(new_name)

    log.info(
        "%s: %s -> %s | first ts_event %s -> %s",
        parquet_path.parent.name,
        parquet_path.name,
        new_name,
        _decode_utc(int(df["ts_event"].sort()[0])),
        _decode_utc(new_start),
    )

    if dry_run:
        return

    # Write the corrected file under the new name, then drop the old one. If the
    # range is unchanged (start==end map to same name) the rename is a rewrite.
    shifted.write_parquet(new_path)
    if new_path != parquet_path:
        parquet_path.unlink()
        stats.files_renamed += 1


def _iter_data_dirs(catalog_path: Path) -> list[Path]:
    """Return every per-instrument directory under the migratable data kinds."""
    dirs: list[Path] = []
    for kind in DATA_KINDS:
        kind_dir = catalog_path / "data" / kind
        if not kind_dir.exists():
            continue
        dirs.extend(sorted(d for d in kind_dir.iterdir() if d.is_dir()))
    return dirs


def migrate(catalog_path: Path, *, dry_run: bool, force: bool) -> MigrationStats:
    """Run the full catalog migration (or a dry-run report).

    Definition: Applies the −8h TW->UTC correction across the catalog, guarded
                by an idempotency marker so a real run cannot double-shift.
    Domain:     `catalog_path` points at an NT ParquetDataCatalog root. `force`
                bypasses the marker (use only to re-run a known-incomplete run).
    Returns:    MigrationStats tallying scanned/shifted/renamed files and rows.
    """
    marker = catalog_path / MARKER_NAME
    if marker.exists() and not force:
        # Honour the marker in dry-run too: once migrated, a dry-run must report a
        # no-op (0 files), NOT re-propose a second −8h on already-true-UTC data —
        # a misleading report could induce a destructive --force double-shift.
        log.warning(
            "Marker %s exists — catalog already migrated. Re-run is a no-op "
            "(pass --force to override).",
            marker,
        )
        return MigrationStats()

    stats = MigrationStats()
    for instrument_dir in _iter_data_dirs(catalog_path):
        stats.instruments += 1
        for parquet_file in sorted(instrument_dir.glob("*.parquet")):
            _shift_one_file(parquet_file, dry_run=dry_run, stats=stats)

    if not dry_run:
        marker.write_text(
            f"migrated {datetime.now(timezone.utc).isoformat()} "
            f"offset_ns=-{TW_UTC_OFFSET_NS} "
            f"files={stats.files_shifted} rows={stats.rows_shifted}\n"
        )
        log.info("Wrote idempotency marker: %s", marker)

    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Back-correct catalog ts_event/ts_init from TW-as-UTC to true UTC"
    )
    parser.add_argument("--catalog-path", required=True, help="Path to catalog dir")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report counts, write nothing"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the idempotency marker (re-run an incomplete migration)",
    )
    args = parser.parse_args()

    catalog_path = Path(args.catalog_path).resolve()
    if not catalog_path.exists():
        log.error("Catalog not found: %s", catalog_path)
        raise SystemExit(1)

    mode = "DRY-RUN" if args.dry_run else "MIGRATE"
    log.info("%s catalog=%s offset=-8h (%d ns)", mode, catalog_path, TW_UTC_OFFSET_NS)

    stats = migrate(catalog_path, dry_run=args.dry_run, force=args.force)

    log.info(
        "%s complete: instruments=%d files_scanned=%d files_shifted=%d "
        "files_renamed=%d rows_shifted=%d",
        mode,
        stats.instruments,
        stats.files_scanned,
        stats.files_shifted,
        stats.files_renamed,
        stats.rows_shifted,
    )


if __name__ == "__main__":
    main()
