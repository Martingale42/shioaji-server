"""WS-D: Regenerate existing catalog instrument definitions (same-source).

The legacy download path wrote ``Equity`` definitions with a hardcoded
``0.01`` tick / ``1000`` lot regardless of the contract's reference price tier
or ``unit``. This one-shot script rebuilds every instrument already in the
catalog through the **same** SinoPac adapter parse the live node uses
(``scripts.instruments.load_instrument``) and overwrites ONLY the instrument
definition parquet — bar/trade_tick data is never touched.

Safety pattern (mirrors the BL-3 restamp script):
  * ``--dry-run`` reports what would change, writes nothing.
  * Backs up ``./catalog`` → ``./catalog_pre_instrument_regen_backup/`` before
    any mutation (distinct from the unrelated ``catalog_pre_restamp_backup/``).
  * Verifies bar/trade_tick row counts + first ``ts_event`` are IDENTICAL
    before vs after (the data red line: definitions only, data immutable).
  * Idempotency marker ``catalog/.instruments_regenerated``.

Usage::

    uv run python -m scripts.regen_catalog_instruments \\
        --catalog-path ./catalog --gateway-url http://localhost:8000 --dry-run
    uv run python -m scripts.regen_catalog_instruments \\
        --catalog-path ./catalog --gateway-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from scripts.instruments import load_instrument

log = logging.getLogger("regen_catalog_instruments")

MARKER_NAME = ".instruments_regenerated"
BACKUP_DIR_NAME = "catalog_pre_instrument_regen_backup"


@dataclass
class DataFingerprint:
    """Row count + first ts_event of an instrument's bar/tick data.

    Definition: A cheap immutability fingerprint over the catalog data files
                that the regen MUST leave untouched.
    Domain:     Captured before and after the regen for byte-level confidence
                that only instrument definitions changed.
    Returns:    Comparable dataclass; equality means data is unchanged.
    """

    trade_tick_count: int = 0
    trade_tick_first_ts: int | None = None
    bar_count: int = 0
    bar_first_ts: int | None = None


@dataclass
class RegenStats:
    """Tally of instruments processed during a regen run."""

    instruments: int = 0
    regenerated: int = 0
    before: dict[str, DataFingerprint] = field(default_factory=dict)
    after: dict[str, DataFingerprint] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _fingerprint(catalog: ParquetDataCatalog, instrument_id: str) -> DataFingerprint:
    """Compute the bar/tick fingerprint for one instrument.

    Definition: Counts trade_tick + bar rows and records each stream's first
                ``ts_event`` for the given instrument id.
    Domain:     ``catalog`` is an NT ParquetDataCatalog; ``instrument_id`` is a
                value like ``0050.SINOPAC``. Empty streams yield count 0 / None.
    Returns:    A ``DataFingerprint`` used to assert data immutability.
    """
    ticks = catalog.query(data_cls=TradeTick, identifiers=[instrument_id])
    bars = catalog.query(data_cls=Bar, identifiers=[instrument_id])
    return DataFingerprint(
        trade_tick_count=len(ticks),
        trade_tick_first_ts=ticks[0].ts_event if ticks else None,
        bar_count=len(bars),
        bar_first_ts=bars[0].ts_event if bars else None,
    )


def _instrument_def_files(catalog_path: Path, instrument_id: str) -> list[Path]:
    """Return the on-disk instrument-definition parquet files for one id.

    Definition: Locates the ``data/<type>/<id>/*.parquet`` files that hold the
                instrument DEFINITION (never bar/trade_tick directories).
    Domain:     Only definition data-type dirs are scanned (equity, futures_*,
                option_*, etc.); ``bar`` and ``trade_tick`` are excluded.
    Returns:    List of parquet paths to remove before overwriting the def.
    """
    data_dir = catalog_path / "data"
    files: list[Path] = []
    if not data_dir.exists():
        return files
    for type_dir in data_dir.iterdir():
        if not type_dir.is_dir():
            continue
        if type_dir.name in {"bar", "trade_tick"}:
            continue  # data red line: never touch market data
        inst_dir = type_dir / instrument_id
        if inst_dir.is_dir():
            files.extend(sorted(inst_dir.glob("*.parquet")))
    return files


async def regenerate(
    catalog_path: Path,
    gateway_url: str,
    *,
    dry_run: bool,
    id_suffix: str | None = None,
    no_backup: bool = False,
) -> RegenStats:
    """Regenerate every catalog instrument definition via the SinoPac provider.

    Definition: For each instrument id in the catalog, rebuilds its definition
                through the same-source provider and overwrites ONLY the
                instrument definition parquet; bar/trade_tick data is untouched.
    Domain:     ``catalog_path`` is an NT ParquetDataCatalog; ``gateway_url``
                points at a running, logged-in SinoPac gateway. On a real run a
                backup is taken first and data immutability is verified after.
    Returns:    ``RegenStats`` with before/after fingerprints + change log.
    """
    catalog = ParquetDataCatalog(str(catalog_path))
    instruments = {i.id.value: i for i in catalog.instruments()}
    if id_suffix:
        instruments = {
            k: v for k, v in instruments.items() if k.endswith(id_suffix)
        }
    log.info(
        "Found %d instruments: %s", len(instruments), list(instruments.keys())
    )

    stats = RegenStats(instruments=len(instruments))

    # Capture the data fingerprint BEFORE any mutation.
    for iid in instruments:
        fp = _fingerprint(catalog, iid)
        stats.before[iid] = fp
        log.info(
            "  %s BEFORE: ticks=%d (first_ts=%s) bars=%d (first_ts=%s) "
            "| def tick=%s lot=%s",
            iid,
            fp.trade_tick_count,
            fp.trade_tick_first_ts,
            fp.bar_count,
            fp.bar_first_ts,
            instruments[iid].price_increment,
            instruments[iid].lot_size,
        )

    if dry_run:
        log.info(
            "DRY-RUN: would regenerate %d instrument definitions "
            "(no backup, no write)",
            len(instruments),
        )
        return stats

    marker = catalog_path / MARKER_NAME
    if marker.exists():
        log.warning(
            "Marker %s exists — catalog already regenerated. Aborting.", marker
        )
        return stats

    # Backup the whole catalog BEFORE mutating (data red line).
    backup_path: Path | None = None
    if no_backup:
        log.warning(
            "--no-backup: skipping internal whole-catalog backup. Caller MUST "
            "have externally backed up the affected instrument data."
        )
    else:
        backup_path = catalog_path.parent / BACKUP_DIR_NAME
        if backup_path.exists():
            msg = (
                f"Backup dir {backup_path} already exists — refusing to overwrite "
                f"a prior backup. Move/remove it first."
            )
            log.error(msg)
            stats.errors.append(msg)
            return stats
        log.info("Backing up %s -> %s", catalog_path, backup_path)
        shutil.copytree(catalog_path, backup_path)

    # Rebuild + overwrite each instrument definition.
    for iid, old_inst in instruments.items():
        try:
            new_inst = await load_instrument(gateway_url, old_inst.id)
            # Remove the stale definition parquet(s), then write the rebuilt def.
            for def_file in _instrument_def_files(catalog_path, iid):
                def_file.unlink()
            catalog.write_data([new_inst])
            stats.regenerated += 1
            change = (
                f"{iid}: tick {old_inst.price_increment} -> "
                f"{new_inst.price_increment}, lot {old_inst.lot_size} -> "
                f"{new_inst.lot_size}"
            )
            stats.changes.append(change)
            log.info("  regenerated %s", change)
        except Exception:
            msg = f"Failed to regenerate {iid}"
            log.exception(msg)
            stats.errors.append(msg)

    # Verify data immutability AFTER (re-open catalog fresh).
    verify_catalog = ParquetDataCatalog(str(catalog_path))
    data_intact = True
    for iid in instruments:
        after = _fingerprint(verify_catalog, iid)
        stats.after[iid] = after
        before = stats.before[iid]
        if after != before:
            data_intact = False
            msg = (
                f"DATA CHANGED for {iid}: before={before} after={after} "
                f"— RED LINE VIOLATION"
            )
            log.error(msg)
            stats.errors.append(msg)
        else:
            log.info(
                "  %s AFTER: ticks=%d (first_ts=%s) bars=%d (first_ts=%s) "
                "— UNCHANGED",
                iid,
                after.trade_tick_count,
                after.trade_tick_first_ts,
                after.bar_count,
                after.bar_first_ts,
            )

    if data_intact and not stats.errors:
        marker.write_text(
            f"instruments_regenerated {datetime.now(timezone.utc).isoformat()}\n"
            f"gateway={gateway_url}\n"
            f"instruments={stats.instruments} regenerated={stats.regenerated}\n"
            f"backup={backup_path}\n"
            + "".join(f"change: {c}\n" for c in stats.changes)
        )
        log.info("Wrote marker: %s", marker)
    else:
        log.error(
            "Regen completed WITH ISSUES — marker NOT written. "
            "Backup preserved at %s for rollback.",
            backup_path,
        )

    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Regenerate catalog instrument definitions via the "
        "SinoPac provider (data untouched)"
    )
    parser.add_argument(
        "--catalog-path", required=True, help="Path to the ParquetDataCatalog"
    )
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:8000",
        help="SinoPac gateway URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change, write nothing",
    )
    parser.add_argument(
        "--id-suffix",
        default=None,
        help="Only regenerate instruments whose id ends with this suffix "
        "(e.g. '.SINOPAC') — for shared catalogs holding non-SinoPac instruments",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the internal whole-catalog backup (use when the affected data "
        "is backed up externally; required for large mixed catalogs)",
    )
    args = parser.parse_args()

    catalog_path = Path(args.catalog_path).resolve()
    if not catalog_path.exists():
        log.error("Catalog not found: %s", catalog_path)
        raise SystemExit(1)

    mode = "DRY-RUN" if args.dry_run else "REGEN"
    log.info("%s catalog=%s gateway=%s", mode, catalog_path, args.gateway_url)

    stats = asyncio.run(
        regenerate(
            catalog_path,
            args.gateway_url,
            dry_run=args.dry_run,
            id_suffix=args.id_suffix,
            no_backup=args.no_backup,
        )
    )

    log.info(
        "%s complete: instruments=%d regenerated=%d errors=%d",
        mode,
        stats.instruments,
        stats.regenerated,
        len(stats.errors),
    )
    if stats.errors:
        for err in stats.errors:
            log.error("  %s", err)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
