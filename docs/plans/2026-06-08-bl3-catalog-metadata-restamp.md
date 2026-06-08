# Catalog Metadata Restamp (BL-3) Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Restore NT-readability of the catalog. The Batch 2 timezone migration (`7961596`) round-tripped every bar/trade_tick parquet through polars, which **stripped the NT key-value metadata** (`instrument_id`, `price_precision`, `size_precision`) AND **downcast the column types** (`fixed_size_binary[16]→large_binary`, `string→large_string`), so `ParquetDataCatalog.query()` now raises `MissingMetadata("instrument_id")`. The timestamp *values* are correct (true UTC) — this plan must NOT shift them again, only repair schema + metadata.

**Architecture:** A one-shot repair script reads each damaged parquet, **casts columns back to NT's canonical Arrow schema**, re-attaches the kv metadata (`instrument_id` from the directory name; `price_precision`/`size_precision` from the intact instrument definitions, which were excluded from the Batch 2 migration), deserializes to NT objects, and re-persists via `catalog.write_data()` into a fresh catalog that is then verified and swapped in. Idempotent via a new marker.

**Tech Stack:** Python, polars/pyarrow, NautilusTrader `ParquetDataCatalog` + `ArrowSerializer`.

**Design reference:** `docs/plans/2026-06-08-backlog-fixes-design.md` (BL-3). All work in `shioaji-server@main`.

**Validated facts (probed 2026-06-08 — rely on these):**
- `catalog.instruments()` works (instrument defs intact) → e.g. `0050.SINOPAC` `price_precision=2`, `size_precision=0`.
- Canonical **trade_tick** schema: `price`,`size`=`fixed_size_binary[16]`; `aggressor_side`=`uint8`; `trade_id`=`string`; `ts_event`,`ts_init`=`uint64`.
- Canonical **bar** schema: `open`,`high`,`low`,`close`,`volume`=`fixed_size_binary[16]`; `ts_event`,`ts_init`=`uint64`.
- `table.cast(canonical_schema)` + re-attach kv → `ArrowSerializer.deserialize(Type, table)` succeeds; values intact (0050 first tick `138.05` @ `2021-04-12T01:00:02Z`; first bar close `195.35` @ hour 01 UTC).
- Damaged dirs: `catalog/data/trade_tick/<iid>/` and `catalog/data/bar/<iid>/`. Instrument defs (`equity`) were NOT migrated and remain NT-readable.

**Project rules:** Python via `uv run` only. Data correctness is a red line — verify against real data, NEVER re-shift timestamps. Commits explain WHY in Traditional Chinese, NO AI attribution, never `--no-verify`.

---

### Task 1: Verify the damage scope + reconfirm the repair on real data (gate)

**Files:** Read-only probe.

**Implementation:**

Before writing anything, confirm the damage is uniform and the repair is sound across the catalog (not just 0050):

1. Enumerate instruments and data dirs:
   ```bash
   uv run python -c "from nautilus_trader.persistence.catalog import ParquetDataCatalog as C; print([i.id.value for i in C('catalog').instruments()])"
   ```
2. For 2-3 instruments, confirm a sample bar AND trade_tick file has the damaged schema (`large_binary`/`large_string`, only `ARROW:schema` kv) and that `cast(canonical)+kv → ArrowSerializer.deserialize` succeeds with `ts_event` decoding to TW market hours under true UTC (~01:00–06:30 UTC, NOT 09:00).

**Verification:** All sampled instruments repair cleanly and decode to true UTC.
**GATE:** If any instrument has a DIFFERENT schema, or `ts_event` decodes to ~09:00 UTC (i.e. NOT yet shifted), STOP and report — do not run the repair (it assumes already-shifted values).

**Commit:** _(no code — findings only)_

---

### Task 2: Write `scripts/restamp_catalog_metadata.py`

**Files:**
- Create: `scripts/restamp_catalog_metadata.py`
- Test: `tests/test_restamp_metadata.py`

**Implementation:**

A script that rebuilds an NT-correct catalog from the damaged one. Type hints + `logging` (not print). Support `--catalog-path`, `--out-path` (default `<catalog>_restamped`), `--dry-run`.

Core per-instrument logic:

```python
CANON: dict[str, pa.Schema]  # built once from a fresh catalog.write_data sample, per type
# trade_tick: price,size=fixed_size_binary(16); aggressor_side=uint8; trade_id=string; ts_event,ts_init=uint64
# bar:        open,high,low,close,volume=fixed_size_binary(16); ts_event,ts_init=uint64

def _repair_table(tbl: pa.Table, canon: pa.Schema, iid: str, pp: int, sp: int) -> pa.Table:
    """Cast damaged columns back to NT canonical types and re-attach NT kv metadata.

    Definition: Restores the Arrow schema + file kv that polars stripped/downcast,
                WITHOUT touching any value (timestamps already corrected in Batch 2).
    Domain:     `tbl` has the damaged schema (large_binary/large_string); `canon`
                is NT's exact schema for this data type; pp/sp from the instrument def.
    Returns:    An NT-deserializable pyarrow Table.
    """
    kv = {**(canon.metadata or {}),
          b"instrument_id": iid.encode(),
          b"price_precision": str(pp).encode(),
          b"size_precision": str(sp).encode()}
    return tbl.cast(canon).replace_schema_metadata(kv)
```

Pipeline:
1. Load source `ParquetDataCatalog(catalog_path)`; `instruments()` → map `iid -> (price_precision, size_precision)`.
2. Build the canonical schemas once (write one synthetic `TradeTick` and one `Bar` to a temp catalog, read `.schema`).
3. Create `ParquetDataCatalog(out_path)`; `out.write_data(instruments)` to carry the defs over.
4. For each `data/{trade_tick,bar}/<iid>/*.parquet`: `pq.read_table` → `_repair_table` → `ArrowSerializer.deserialize(TradeTick|Bar, repaired)` → `out.write_data(objs)`.
5. `--dry-run`: report instrument/file/row counts, write nothing.
6. Write marker `out_path/.metadata_restamped` (records source, file/row counts). Do NOT reuse `.ts_utc_migrated`.

**Tests (Required — data correctness):** `tests/test_restamp_metadata.py`:
- Build a tiny damaged file (write a TradeTick via NT, then polars round-trip to strip/downcast), run `_repair_table`, assert `ArrowSerializer.deserialize` succeeds and the price/ts values are byte-identical to the original.
- Assert `_repair_table` does NOT change `ts_event` (no re-shift).

**Verification:**
Run: `uv run pytest tests/test_restamp_metadata.py -v` → pass.
Run: `uv run python -m scripts.restamp_catalog_metadata --catalog-path ./catalog --dry-run` → reports ~3193 files across the migrated instruments.

**Commit:**
```bash
git add scripts/restamp_catalog_metadata.py tests/test_restamp_metadata.py
git commit -m "feat: catalog metadata restamp 腳本——還原 Batch 2 遷移剝離的 NT kv 與欄位型別（不動時間值）"
```

---

### Task 3: Run the restamp, verify NT-readability, swap in

**Files:** Operates on `./catalog` (data only — gitignored).

**Implementation:**

1. Dry-run, record the expected counts.
2. Real run into `./catalog_restamped`:
   ```bash
   uv run python -m scripts.restamp_catalog_metadata --catalog-path ./catalog --out-path ./catalog_restamped
   ```
3. **Verify the new catalog is NT-readable and values are intact** before swapping:
   ```bash
   uv run python - <<'PY'
   from nautilus_trader.persistence.catalog import ParquetDataCatalog as C
   import datetime as d
   cat = C("catalog_restamped")
   from nautilus_trader.model.data import TradeTick, Bar
   t = cat.query(TradeTick); b = cat.query(Bar)
   print("ticks", len(t), "bars", len(b))
   print("first tick UTC", d.datetime.fromtimestamp(t[0].ts_event/1e9, d.timezone.utc))  # expect ~01:00–06:30
   PY
   ```
   Expected: `query()` returns rows for both types (no `MissingMetadata`); first tick decodes to true UTC (~01:00–06:30, NOT 09:00); tick/bar counts match the source (compare with `inspect_catalog.py` on the old catalog).
4. Swap with a backup (data is gitignored, so keep a safety copy):
   ```bash
   mv catalog catalog_pre_restamp_backup && mv catalog_restamped catalog
   ```
   Keep `catalog_pre_restamp_backup` until QA signs off (note it in the commit body); it can be deleted afterward.

**Verification:** `ParquetDataCatalog('catalog').query(TradeTick)` and `.query(Bar)` both succeed; counts equal the pre-restamp counts; timestamps unchanged (true UTC).

**Commit:** _(data is gitignored — no file commit; record the run + counts in Task 4's doc-correction commit body. Do NOT `git add` the catalog.)_

---

### Task 4: Mark the metadata-stripper + correct the mislabel in docs

**Files:**
- Modify: `scripts/migrate_ts_to_utc.py`
- Modify: `docs/BACKLOG.md`, `docs/AUDIT.md`, `docs/sessions/progress.json`

**Implementation:**

1. `scripts/migrate_ts_to_utc.py`: add a module-level warning docstring/comment that `polars.write_parquet` strips NT kv metadata and downcasts `fixed_size_binary`/`string` columns, so this one-shot is **superseded** — any future shift must go through `catalog.write_data()` (see `restamp_catalog_metadata.py`). Do NOT re-run it.
2. `docs/BACKLOG.md` BL-3: change classification from "既有問題/早於本批" to "Batch 2 遷移 `7961596` 引入的回歸（polars round-trip 剝離 NT kv + 降型）"; mark resolved by the restamp once Task 3 verifies.
3. `docs/AUDIT.md` / `docs/sessions/progress.json` `open_followups`: correct the "PRE-EXISTING (parquet never carried instrument_id kv)" wording to note it WAS carried, then stripped by the Batch 2 migration; restamped in BL-3.

**Verification:** `git diff` shows the corrected classification; `uv run ruff check scripts/` still clean.

**Commit:**
```bash
git add scripts/migrate_ts_to_utc.py docs/BACKLOG.md docs/AUDIT.md docs/sessions/progress.json
git commit -m "docs: BL-3 重新定性為 Batch 2 回歸並標記遷移腳本剝離 metadata 之陷阱

restamp 已將 catalog 還原為 NT-readable（query 可讀、時間值仍真 UTC）。
更正先前『pre-existing』誤判：instrument_id/price_precision/size_precision kv
原本存在，被 migrate_ts_to_utc 的 polars round-trip 剝離且欄位降型。"
```

---

**Sequencing:** Task 1 (gate) → 2 → 3 → 4. Task 3 must verify NT-readability before the swap; keep the backup until QA passes.
