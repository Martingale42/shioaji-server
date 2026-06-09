"""Verify the restamped catalog: NT-readability, schema correctness, and backup comparison.

Three verification axes:
1. NT API — ParquetDataCatalog.query() succeeds, objects deserialize correctly
2. Arrow schema — column types match NT canonical schema, kv metadata complete
3. Backup diff — row counts match, timestamps unchanged (no re-shift)

Usage::

    cd shioaji-server

    # Full verification (compares against backup):
    uv run python -m scripts.maintenance.verify_catalog_restamp \
        --catalog-path ./catalog \
        --backup-path ./catalog_pre_restamp_backup

    # Without backup (skip diff checks):
    uv run python -m scripts.maintenance.verify_catalog_restamp --catalog-path ./catalog
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

CANONICAL_TRADE_TICK_TYPES: dict[str, pa.DataType] = {
    "price": pa.binary(16),
    "size": pa.binary(16),
    "aggressor_side": pa.uint8(),
    "trade_id": pa.string(),
    "ts_event": pa.uint64(),
    "ts_init": pa.uint64(),
}

CANONICAL_BAR_TYPES: dict[str, pa.DataType] = {
    "open": pa.binary(16),
    "high": pa.binary(16),
    "low": pa.binary(16),
    "close": pa.binary(16),
    "volume": pa.binary(16),
    "ts_event": pa.uint64(),
    "ts_init": pa.uint64(),
}

REQUIRED_KV_KEYS = {b"instrument_id", b"price_precision", b"size_precision"}


class VerificationError:
    def __init__(self, category: str, instrument: str, detail: str) -> None:
        self.category = category
        self.instrument = instrument
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.category}] {self.instrument}: {self.detail}"


def _ns_to_utc(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def verify_nt_api(catalog_path: Path, errors: list[VerificationError]) -> dict:
    """Axis 1: NT ParquetDataCatalog can query TradeTick and Bar."""
    from nautilus_trader.model.data import Bar, TradeTick
    from nautilus_trader.persistence.catalog import ParquetDataCatalog as C

    stats: dict = {}
    cat = C(str(catalog_path))

    instruments = cat.instruments()
    stats["instruments"] = len(instruments)
    log.info("NT instruments loaded: %d", len(instruments))

    for cls, label in [(TradeTick, "trade_tick"), (Bar, "bar")]:
        try:
            objs = cat.query(cls)
        except Exception as e:
            errors.append(VerificationError("nt_api", label, f"query() failed: {e}"))
            continue

        count = len(objs)
        stats[f"{label}_count"] = count
        log.info("NT query(%s): %d objects", label, count)

        if count == 0:
            errors.append(VerificationError("nt_api", label, "query() returned 0 rows"))
            continue

        first_ts = objs[0].ts_event
        last_ts = objs[-1].ts_event
        first_dt = _ns_to_utc(first_ts)
        last_dt = _ns_to_utc(last_ts)
        stats[f"{label}_first_utc"] = first_dt.isoformat()
        stats[f"{label}_last_utc"] = last_dt.isoformat()

        log.info("  range: %s → %s", first_dt, last_dt)

        if first_dt.hour >= 8:
            errors.append(VerificationError(
                "nt_api", label,
                f"first ts_event hour={first_dt.hour} — looks like local time, "
                f"expected ~01:00-06:30 UTC (true UTC after migration)",
            ))

    return stats


def verify_arrow_schema(
    catalog_path: Path,
    data_type: str,
    canonical: dict[str, pa.DataType],
    errors: list[VerificationError],
) -> dict:
    """Axis 2: Arrow schema types + kv metadata are NT-canonical."""
    data_dir = catalog_path / "data" / data_type
    stats: dict = {"files_checked": 0, "total_rows": 0}

    if not data_dir.exists():
        errors.append(VerificationError("schema", data_type, f"directory not found: {data_dir}"))
        return stats

    for iid_dir in sorted(data_dir.iterdir()):
        if not iid_dir.is_dir():
            continue

        for pf in sorted(iid_dir.glob("*.parquet")):
            stats["files_checked"] += 1
            meta = pq.read_metadata(pf)
            schema = pq.read_schema(pf)
            stats["total_rows"] += meta.num_rows

            kv = schema.metadata or {}
            missing_kv = REQUIRED_KV_KEYS - set(kv.keys())
            if missing_kv:
                errors.append(VerificationError(
                    "schema", f"{data_type}/{iid_dir.name}/{pf.name}",
                    f"missing kv keys: {sorted(k.decode() for k in missing_kv)}",
                ))

            for col_name, expected_type in canonical.items():
                idx = schema.get_field_index(col_name)
                if idx < 0:
                    errors.append(VerificationError(
                        "schema", f"{data_type}/{iid_dir.name}/{pf.name}",
                        f"missing column: {col_name}",
                    ))
                    continue
                actual_type = schema.field(idx).type
                if actual_type != expected_type:
                    errors.append(VerificationError(
                        "schema", f"{data_type}/{iid_dir.name}/{pf.name}",
                        f"column '{col_name}' type {actual_type} != expected {expected_type}",
                    ))

    log.info(
        "Arrow schema check (%s): %d files, %d rows",
        data_type, stats["files_checked"], stats["total_rows"],
    )
    return stats


def verify_timestamp_range(
    catalog_path: Path,
    data_type: str,
    errors: list[VerificationError],
) -> None:
    """Spot-check ts_event values are in true-UTC TW market range (~01:00-06:30)."""
    data_dir = catalog_path / "data" / data_type
    if not data_dir.exists():
        return

    for iid_dir in sorted(data_dir.iterdir()):
        if not iid_dir.is_dir():
            continue

        first_pf = next(iid_dir.glob("*.parquet"), None)
        if first_pf is None:
            continue

        tbl = pq.read_table(first_pf, columns=["ts_event"])
        if len(tbl) == 0:
            continue

        ts_first = tbl.column("ts_event")[0].as_py()
        ts_last = tbl.column("ts_event")[-1].as_py()
        dt_first = _ns_to_utc(ts_first)
        dt_last = _ns_to_utc(ts_last)

        log.info(
            "  %s/%s first-file range: %s → %s",
            data_type, iid_dir.name, dt_first, dt_last,
        )

        for dt, label in [(dt_first, "first"), (dt_last, "last")]:
            if dt.hour >= 8:
                errors.append(VerificationError(
                    "timestamp", f"{data_type}/{iid_dir.name}",
                    f"{label} ts_event hour={dt.hour}, looks like local time "
                    f"(expected 0-7 UTC for TW market)",
                ))


def verify_backup_diff(
    catalog_path: Path,
    backup_path: Path,
    data_type: str,
    errors: list[VerificationError],
) -> dict:
    """Axis 3: Compare restamped vs backup — row counts must match, ts values unchanged."""
    stats: dict = {"instruments_compared": 0, "rows_match": True, "ts_match": True}

    cat_dir = catalog_path / "data" / data_type
    bak_dir = backup_path / "data" / data_type

    if not bak_dir.exists():
        log.info("Backup has no %s data — skipping diff.", data_type)
        return stats

    for iid_dir in sorted(cat_dir.iterdir()):
        if not iid_dir.is_dir():
            continue

        bak_iid = bak_dir / iid_dir.name
        if not bak_iid.exists():
            errors.append(VerificationError(
                "diff", f"{data_type}/{iid_dir.name}",
                "present in restamped but not in backup",
            ))
            continue

        stats["instruments_compared"] += 1

        cat_rows = sum(pq.read_metadata(f).num_rows for f in iid_dir.glob("*.parquet"))
        bak_rows = sum(pq.read_metadata(f).num_rows for f in bak_iid.glob("*.parquet"))

        if cat_rows != bak_rows:
            stats["rows_match"] = False
            errors.append(VerificationError(
                "diff", f"{data_type}/{iid_dir.name}",
                f"row count mismatch: restamped={cat_rows} vs backup={bak_rows}",
            ))

        cat_files = sorted(iid_dir.glob("*.parquet"))
        bak_files = sorted(bak_iid.glob("*.parquet"))
        cat_first = cat_files[0] if cat_files else None
        bak_first = bak_files[0] if bak_files else None
        if cat_first and bak_first:
            cat_ts = pq.read_table(cat_first, columns=["ts_event"]).column("ts_event")
            bak_ts = pq.read_table(bak_first, columns=["ts_event"]).column("ts_event")

            n = min(len(cat_ts), len(bak_ts), 100)
            for i in range(n):
                if cat_ts[i].as_py() != bak_ts[i].as_py():
                    stats["ts_match"] = False
                    errors.append(VerificationError(
                        "diff", f"{data_type}/{iid_dir.name}",
                        f"ts_event CHANGED at row {i}: "
                        f"restamped={cat_ts[i].as_py()} vs backup={bak_ts[i].as_py()} "
                        f"— THIS SHOULD NOT HAPPEN (timestamps must not be re-shifted)",
                    ))
                    break

    log.info(
        "Backup diff (%s): %d instruments compared, rows_match=%s, ts_match=%s",
        data_type, stats["instruments_compared"], stats["rows_match"], stats["ts_match"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify restamped catalog: NT API, Arrow schema, and backup diff",
    )
    parser.add_argument("--catalog-path", required=True, help="Path to restamped catalog")
    parser.add_argument("--backup-path", default=None, help="Path to pre-restamp backup (optional)")
    args = parser.parse_args()

    catalog_path = Path(args.catalog_path).resolve()
    backup_path = Path(args.backup_path).resolve() if args.backup_path else None

    if not catalog_path.exists():
        log.error("Catalog not found: %s", catalog_path)
        sys.exit(1)

    errors: list[VerificationError] = []

    print("=" * 60)
    print("Catalog Restamp Verification")
    print(f"  catalog: {catalog_path}")
    if backup_path:
        print(f"  backup:  {backup_path}")
    print("=" * 60)

    # --- Axis 1: NT API ---
    print("\n── Axis 1: NT API readability ──")
    nt_stats = verify_nt_api(catalog_path, errors)

    # --- Axis 2: Arrow schema ---
    print("\n── Axis 2: Arrow schema + kv metadata ──")
    tick_schema = verify_arrow_schema(catalog_path, "trade_tick", CANONICAL_TRADE_TICK_TYPES, errors)
    bar_schema = verify_arrow_schema(catalog_path, "bar", CANONICAL_BAR_TYPES, errors)

    print("\n── Axis 2b: Timestamp range spot-check ──")
    verify_timestamp_range(catalog_path, "trade_tick", errors)
    verify_timestamp_range(catalog_path, "bar", errors)

    # --- Axis 3: Backup diff ---
    if backup_path and backup_path.exists():
        print("\n── Axis 3: Backup comparison ──")
        tick_diff = verify_backup_diff(catalog_path, backup_path, "trade_tick", errors)
        bar_diff = verify_backup_diff(catalog_path, backup_path, "bar", errors)
    else:
        print("\n── Axis 3: SKIPPED (no backup path) ──")
        tick_diff = bar_diff = {}

    # --- Summary ---
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(f"\nInstruments:  {nt_stats.get('instruments', '?')}")
    print(f"TradeTick:    {nt_stats.get('trade_tick_count', '?'):>12,} objects")
    print(f"  files:      {tick_schema.get('files_checked', '?'):>12,}")
    print(f"  range:      {nt_stats.get('trade_tick_first_utc', '?')}  →  {nt_stats.get('trade_tick_last_utc', '?')}")
    print(f"Bar:          {nt_stats.get('bar_count', '?'):>12,} objects")
    print(f"  files:      {bar_schema.get('files_checked', '?'):>12,}")
    print(f"  range:      {nt_stats.get('bar_first_utc', '?')}  →  {nt_stats.get('bar_last_utc', '?')}")

    if tick_diff:
        print(f"\nBackup diff (trade_tick): rows_match={tick_diff.get('rows_match')}, ts_match={tick_diff.get('ts_match')}")
    if bar_diff:
        print(f"Backup diff (bar):        rows_match={bar_diff.get('rows_match')}, ts_match={bar_diff.get('ts_match')}")

    marker = catalog_path / ".metadata_restamped"
    print(f"\nRestamp marker: {'EXISTS' if marker.exists() else 'MISSING'}")

    if errors:
        print(f"\n{'!' * 60}")
        print(f"ERRORS FOUND: {len(errors)}")
        print(f"{'!' * 60}")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print(f"\n{'─' * 60}")
        print("ALL CHECKS PASSED")
        print(f"{'─' * 60}")


if __name__ == "__main__":
    main()
