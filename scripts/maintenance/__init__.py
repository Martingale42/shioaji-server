"""One-shot catalog maintenance scripts (already executed; kept for re-use).

These repaired/regenerated an existing ParquetDataCatalog and are NOT part of
the routine download pipeline:

* ``restamp_catalog_metadata`` — restored NT kv metadata + column types that a
  superseded polars round-trip migration stripped (BL-3). Marker
  ``catalog/.metadata_restamped``.
* ``regen_catalog_instruments`` — rebuilt instrument definitions via the
  SinoPac provider (fixed ETF tick tiers), data untouched (WS-D). Marker
  ``catalog/.instruments_regenerated``.
* ``verify_catalog_restamp`` — three-axis verification (NT API / Arrow schema /
  backup diff) for the restamp.

Run as e.g. ``uv run python -m scripts.maintenance.verify_catalog_restamp``.
"""
