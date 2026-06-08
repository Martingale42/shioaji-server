# WS-C + WS-D: Backtest Same-Source + Catalog Regeneration Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Make the backtest download scripts build instrument definitions from the **same** sinopac-adapter Rust parse the live node uses (via `SinopacInstrumentProvider`), retiring the hardcoded `make_equity`/`contract_to_equity`; then regenerate the existing catalog's instrument definitions with the corrected builder.

**Architecture:** The download scripts construct the pyo3 `SinopacHttpClient(base_url=<gateway>)`, wrap it in `SinopacInstrumentProvider`, load the contract(s), and `catalog.write_data([instrument])`. A one-shot regeneration script does the same for every instrument already in the catalog, with a backup + verification (mirrors the BL-3 pattern). Data (bars/ticks) is untouched.

**Tech Stack:** Python, NautilusTrader (`SinopacInstrumentProvider`, `nautilus_pyo3.sinopac.SinopacHttpClient`, `ParquetDataCatalog`).

**Design reference:** `docs/plans/2026-06-08-instrument-definitions-design.md` (WS-C, WS-D). **Depends on WS-A + WS-B** (gateway sends new fields; adapter parse rebuilt). All work in `shioaji-server@main`.

**Project rules:** Python via `uv run`. Data correctness is a red line — regeneration touches only instrument definitions, NEVER bar/tick data; back up first. Commits WHY in Traditional Chinese, NO AI attribution, never `--no-verify`.

**Verified facts:**
- pyo3 `SinopacHttpClient(base_url=None)` is standalone-constructible (`crates/adapters/sinopac/src/python/http.rs:51`).
- `SinopacInstrumentProvider(client, config)` → `load_all_async()`/`load_ids_async([id])`/`load_async(id)`; instruments via `provider.find(id)` / `provider.list_all()` / `provider.get_all()` (`nautilus_trader/adapters/sinopac/providers.py`).
- Current hardcoded builders: `scripts/fetch_single.py:32`, `scripts/fetch_historical.py:38`, used at `fetch_single_ticks.py:231`.

---

### Task 1: GATE — verify the provider path works against the gateway

**Files:** Read-only probe (a scratch script, not committed).

**Implementation:**

Confirm the same-source path is viable before refactoring the scripts. With the gateway running + logged in (or document if creds unavailable):

```python
import asyncio
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.adapters.sinopac.providers import SinopacInstrumentProvider

async def main():
    client = nautilus_pyo3.sinopac.SinopacHttpClient(base_url="http://localhost:8000")
    provider = SinopacInstrumentProvider(client=client)
    await provider.load_ids_async([InstrumentId.from_str("2330.SINOPAC")])
    inst = provider.find(InstrumentId.from_str("2330.SINOPAC"))
    print(inst, inst.price_increment, inst.lot_size)
```

**Verification:** the provider returns a `2330.SINOPAC` `Equity` with a reference-derived tick (e.g. TSMC ~580 → tick 1.0) and `lot_size` from `unit`. Try one futures + one option code too (confirm options no longer fail).
**GATE:** If `SinopacHttpClient` can't be constructed standalone or the provider needs a full live node, STOP and report — fall back to calling `client.request_*_instruments()` + `instruments_from_pyo3()` directly (document the change). If the gateway can't run with creds in this environment, document that integration verification is deferred and proceed with unit-level wiring + a mocked provider test.

**Commit:** _(no code — findings only)_

---

### Task 2: Replace the hardcoded builders with the provider

**Files:**
- Modify: `scripts/fetch_single.py`, `scripts/fetch_single_ticks.py`, `scripts/fetch_historical.py`
- Create: `scripts/instruments.py` (thin shared helper)
- Test: `tests/test_instrument_provider_path.py`

**Implementation:**

1. `scripts/instruments.py`: one helper that all scripts call, e.g.:
   ```python
   async def load_instrument(gateway_url: str, instrument_id: InstrumentId) -> Instrument:
       client = nautilus_pyo3.sinopac.SinopacHttpClient(base_url=gateway_url)
       provider = SinopacInstrumentProvider(client=client)
       await provider.load_async(instrument_id)
       inst = provider.find(instrument_id)
       if inst is None:
           raise RuntimeError(f"Instrument {instrument_id} not found via provider")
       return inst
   ```
2. Delete `make_equity` (`fetch_single.py`) and `contract_to_equity` (`fetch_historical.py`); replace their call sites with `load_instrument(...)` (or a batch `load_all` for `fetch_historical`).
3. Keep the catalog write as `catalog.write_data([instrument])`.

**Tests:** `tests/test_instrument_provider_path.py` — mock the pyo3 client / provider to return a known `Equity` and assert `load_instrument` returns it and the script writes it via `write_data` (no hardcoded fields remain). Grep-assert `make_equity`/`contract_to_equity` are gone.

**Verification:**
Run: `uv run pytest tests/test_instrument_provider_path.py -v` → pass.
Run: `uv run ruff check scripts/ tests/` → clean (also clears any now-unused imports).
Integration (if gateway available): `uv run python -m scripts.fetch_single --code 2330 ...` writes a `2330.SINOPAC` Equity with correct tick/lot.

**Commit:**
```bash
git add scripts/instruments.py scripts/fetch_single.py scripts/fetch_single_ticks.py scripts/fetch_historical.py tests/test_instrument_provider_path.py
git commit -m "refactor: 下載腳本退役硬編碼 make_equity，改用 SinopacInstrumentProvider 同源

backtest 與 live node 自此共用同一份 Rust parse 的 instrument 定義（正確 tick/lot/
multiplier），消除腳本硬編碼 0.01 tick / 1000 lot 的劣化路徑。"
```

---

### Task 3 (WS-D): Regenerate existing catalog instrument definitions

**Files:**
- Create: `scripts/regen_catalog_instruments.py`

**Implementation:**

For every instrument already in `./catalog` (currently Equity only), rebuild its definition via the provider and overwrite the catalog's instrument definition — **data files (bar/trade_tick) are NOT touched**. Mirror BL-3's safety pattern: `--dry-run`, backup, verify, marker.

```python
# 1. enumerate existing instrument ids: catalog.instruments() (or scan data/<type>/<id>/)
# 2. for each: inst = await load_instrument(gateway_url, instrument_id)   # corrected builder
# 3. write into a fresh out-catalog (or overwrite the instrument parquet only) via catalog.write_data([inst])
# 4. backup ./catalog -> ./catalog_pre_instrument_regen_backup before swap
# 5. verify: catalog.instruments() returns corrected tick/lot/limit; data dirs unchanged (counts/first ts_event identical)
```

Idempotency marker `catalog/.instruments_regenerated`. Reuse `scripts/instruments.py`.

**Verification:**
Run: `uv run python -m scripts.regen_catalog_instruments --catalog-path ./catalog --gateway-url http://localhost:8000 --dry-run` → reports N instruments.
Then real run; verify a known instrument's `price_increment` now matches its `reference` tier (e.g. a >100 TWD stock no longer 0.01), `lot_size` from `unit`, and that `query(TradeTick)`/`query(Bar)` still return the SAME counts + first `ts_event` (data untouched).

**Commit:**
```bash
git add scripts/regen_catalog_instruments.py
git commit -m "feat: 一次性重生既存 catalog instrument 定義（修正 tick/lot），不動 bar/tick 資料

既存 Equity 定義原帶硬編碼 0.01 tick；以同源 provider 重生為依 reference 分階的正確
tick 與 unit lot。備份於 catalog_pre_instrument_regen_backup/，僅覆寫 instrument 定義。"
```

---

**Sequencing:** Task 1 (gate) → 2 → 3. Tasks depend on WS-A + WS-B being live (gateway fields + rebuilt adapter). Keep the WS-D backup until QA signs off.
