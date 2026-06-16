# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The data path has a standing contract: the gateway emits **true UTC**, and any
change to timestamp semantics or the on-disk `ParquetDataCatalog` schema is a
breaking change that requires a catalog re-stamp or re-download.

## [Unreleased]

### Added

- Instrument-definition pipeline (WS-A…D): live and backtest build instruments
  from the sinopac adapter's Shioaji-authoritative parse (BL-6).
- `shioaji-data` CLI → NautilusTrader `ParquetDataCatalog`: quota-aware resumable
  bars/ticks fetch, plus the catalog restamp/regen maintenance chain.
- Gateway keepalive / silent-death detection.

### Changed

- Timezone: the gateway emits true UTC (HTTP `ts_utc = ts_tw − 8h`) so HTTP, WS,
  and download scripts agree.
- Runtime artifacts consolidated under `~/.shioaji-server/` (`.env`,
  `Sinopac.pfx`, `logs/`).
- `scripts/` cleanup: retired the Paradigm A bulk-download path and relocated the
  one-shot maintenance chain (BL-7).

### Fixed

- Session self-heal regressions: relogin under lock, the reconnect window,
  resubscribe readiness, and bounded backoff with alerting (audit G1–G4).
- P3 timed-out orders now true-adopt via a `custom_field` token round-trip,
  removing the silent double-order exposure (BL-1).
- Catalog metadata restamp after a polars round-trip stripped NT kv + downcast
  dtypes, restoring `ParquetDataCatalog.query()` (BL-3).
- `scripts/fetch_single.py` dead-import (F401) cleanup (BL-2).

<!-- First release is not yet tagged. When tagging 0.1.0, move the above into a
     [0.1.0] — YYYY-MM-DD section and add a release link below. -->
[Unreleased]: https://github.com/Martingale42/shioaji-server/commits/main
