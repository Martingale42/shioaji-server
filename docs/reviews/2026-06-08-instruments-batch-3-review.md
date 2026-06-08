# Batch 3 (WS-C + WS-D) Code Review — backtest 同源 + catalog instrument 重生

- **Reviewer role:** Code Reviewer（instrument-definition fixes pipeline）
- **Scope:** `shioaji-server@main` commits `05cfd80`（Task 2 / WS-C）、`3c3b776`（Task 3 / WS-D）
- **Review date:** 2026-06-09
- **Plan refs:** `docs/plans/2026-06-08-ws-cd-backtest-and-regen.md`、`docs/plans/2026-06-08-instrument-definitions-design.md`（WS-C / WS-D）

## Verdict: **APPROVED WITH NOTES**

程式碼對「目標 adapter（sinopac）」是正確的，API 簽名逐項比對真實 provider/pyo3 source 全數吻合；資料紅線（只動 instrument 定義、不碰 bar/tick、先備份、後驗證、有問題不寫 marker）在程式與測試中都成立；真實 catalog 未被觸碰，既有 `catalog_pre_restamp_backup/` 完好。環境性的 deferral（sinopac adapter / gateway 不可用）由 plan line 44 明確授權，**不構成阻擋**。僅有數個 Minor（效能與 docstring 用詞），無 Critical/Important code issue。

---

## API-signature check（關鍵——mocked 測試掩蓋不了的部分）

mocked 測試把 `SinopacInstrumentProvider` 與 `nautilus_pyo3.sinopac.SinopacHttpClient` 整個 stub 掉，因此**無法**驗證真實簽名。我直接讀了 nautilus_trader source 樹（design 指定的 single source of truth）逐項比對：

| helper 用法 (`scripts/instruments.py`) | 真實 source | 結果 |
|---|---|---|
| `nautilus_pyo3.sinopac.SinopacHttpClient(base_url=gateway_url)` | `crates/adapters/sinopac/src/python/http.rs:51` → `#[pyo3(signature = (base_url=None))]` | ✅ 吻合（`base_url` keyword 存在且可單獨建構） |
| 子模組路徑 `nautilus_pyo3.sinopac` | `crates/adapters/sinopac/src/python/mod.rs:25` `Loaded as nautilus_pyo3.sinopac`；`crates/pyo3/src/lib.rs:288` `let n = "sinopac"` | ✅ 吻合 |
| `SinopacInstrumentProvider(client=client)` | `nautilus_trader/adapters/sinopac/providers.py:43-47` `__init__(self, client, config=None)`（client 為第一 positional，keyword 可用） | ✅ 吻合 |
| `await provider.load_async(instrument_id)` | `providers.py:136-153` `async def load_async(self, instrument_id, filters=None)` | ✅ 吻合 |
| `provider.find(instrument_id)` → `Instrument | None` | base `InstrumentProvider.find(self, instrument_id) -> Instrument | None`（繼承，已用 `uv run python` 確認簽名） | ✅ 吻合；helper 正確處理 `None`（raise `RuntimeError`） |

**結論：API-signature check 通過。** 一旦重建的 sinopac wheel 安裝進 venv，這條路徑不會因建構子 / 方法簽名而在 runtime 失敗。

---

## Findings

### Critical
（無）

### Important
（無）

### Minor

1. **Regen 對每個 instrument 都重抓整個合約宇宙（N× 冗餘 fetch）** — `[scripts/regen_catalog_instruments.py:190]`
   真實 `load_async` → `load_ids_async` → `_fetch_all_instruments`（`providers.py:64-81`）會同時打 `request_stock/futures/options_instruments` 抓**全部**合約後再 filter。regen loop 每個 id 都呼叫一次 `load_instrument`，等於 N 個 instrument 就把整個宇宙抓 N 次。目前 catalog 只有 2 檔，影響可忽略；但 deferred 的真實 regen 若 catalog 變大會明顯變慢。建議（非阻擋）：真實 regen 時改成一次 `provider.load_all_async()` / `load_ids_async([...all ids...])` 再對每個 id `find()`，把 N× 降為 1×。同樣的 N× 也存在於 `fetch_*` 腳本的每檔下載，但下載本身就是逐檔流程，影響較小。

2. **Fingerprint 的「byte-for-byte / byte-level」用詞過強** — `[scripts/regen_catalog_instruments.py:35, 75]`（docstring）、`[scripts/regen_catalog_instruments.py:208-232]`（實作）
   immutability 檢查是 `len(query(...))`（row count）+ 第一筆 `ts_event`。這**不會**偵測「count 與首筆 ts 不變、但中間/末筆被改」的情形，與 docstring 宣稱的「byte-level confidence / byte-for-byte identical」不符。實務上紅線靠的是「regen 只 unlink/write 非 `bar`/非 `trade_tick` 目錄、資料檔從不被開啟寫入」這個結構保證（見下方紅線確認），fingerprint 只是 sanity check。建議把 docstring 改為「row count + first ts_event sanity check」以免日後誤信其強度；若要真 byte-level，可加 last ts_event / 檔案 mtime 或 hash。

3. **`from __future__` 後的 import 順序** — `[scripts/regen_catalog_instruments.py]` 與 `[scripts/instruments.py:36-39]`
   `_make_provider` 內的 function-local import 把 `nautilus_pyo3` 放在 `nautilus_trader.adapters.sinopac.providers` 之前，與 ruff isort 的 module 層排序慣例略有出入，但 ruff `check scripts/ tests/` 已 **All checks passed**（function-local import 不在 isort 管轄），故僅為觀感層級、非問題。lazy import 設計本身正確（見下）。

---

## 資料紅線確認（WS-D）

逐條對照 review procedure 第 4 點，全部成立：

- **先備份再動手**：`[regen:175-185]` `shutil.copytree(catalog_path, backup_path)` 在 mutation loop **之前**；`backup_path = catalog_path.parent / "catalog_pre_instrument_regen_backup"`，與無關的 `catalog_pre_restamp_backup/` 明確區隔。✅
- **拒絕覆蓋既有備份**：`[regen:166-174]` 若 backup dir 已存在 → 記 error、return、不動任何東西。✅（測試 `test_aborts_if_backup_dir_already_exists` 覆蓋）
- **只覆寫 instrument 定義、絕不碰 bar/trade_tick**：`[regen:_instrument_def_files]` 掃 `data/<type>/`，**明確 skip `{"bar", "trade_tick"}`**（line 含 `# data red line: never touch market data`）。已用真實 catalog 佈局驗證：`data/{bar,equity,trade_tick}/`，且 `bar` 子目錄是 `<id>-1-MINUTE-...`（連 `inst_dir = type_dir / id` 都不會命中），雙重保險。✅
- **unlink 只在 load 成功後**：`[regen:189-194]` `def_file.unlink()` 位於 `try` 內、`await load_instrument(...)` 成功之後；load 失敗則舊定義保留，不會半毀。✅
- **驗證 count + first ts_event 前後一致**：`[regen:208-232]` 重開一個 fresh `ParquetDataCatalog` 再 `_fingerprint`，逐 id 比對 `DataFingerprint`（trade_tick/bar 的 count + first_ts）。✅
- **任何資料變動 / error 都不寫 marker**：`[regen:234-249]` marker 僅在 `data_intact and not stats.errors` 時寫；否則記「marker NOT written, backup preserved for rollback」。✅
- **`--dry-run` 為唯讀**：`[regen:158-164]` dry-run 在 marker/backup 檢查**之前**就 early-return，只做 `catalog.instruments()` + `_fingerprint`，從不呼叫 `load_instrument`、不 copytree、不 unlink、不 write。✅

測試 `tests/test_regen_catalog_instruments.py` 確實逐條斷言紅線：`test_real_regen_fixes_def_and_preserves_data` 在 temp catalog seed legacy equity（0.01/1000）+ 5 ticks + 3 bars，mock provider 回 0.05/2000，regen 後斷言 (a) 定義變正確、(b) tick/bar **count + 首筆 ts_event 與 seed 完全相同**、(c) `stats.before == stats.after`；另有 dry-run no-op、idempotent marker、拒絕覆蓋備份共 4 案。✅

---

## 真實 catalog 未被觸碰確認（review procedure 第 5 點）

- `catalog/.instruments_regenerated` — **不存在**（dry-run 前後皆然）✅
- `catalog_pre_instrument_regen_backup/` — **不存在** ✅
- catalog instruments 仍為 legacy：`0050.SINOPAC: tick=0.01 lot=1000`、`00631L.SINOPAC: tick=0.01 lot=1000` ✅
- 既有 `catalog_pre_restamp_backup/` — **保留完好** ✅
- 唯一被改動的 tracked file 是 `docs/sessions/instruments/progress.json`（orchestrator 的，**未** staged，依指示不碰）

---

## Deferral 評估（review procedure 第 6 點）

- **GATE outcome 屬實**（我親自驗證，未只信 executor）：
  - `import nautilus_trader.adapters.sinopac` → `ModuleNotFoundError` ✅
  - 安裝的 wheel 為 `nautilus_trader 1.224.0`，只含**舊 `shioaji`-named** adapter：`nautilus_pyo3` 暴露 `ShioajiHttpClient` / `shioaji` 子模組、`nautilus_trader.adapters.shioaji` 存在，**無 `sinopac`**。✅
  - 無運行中 gateway / creds。
- **deferral 合法性**：plan line 44 明文授權——「If the gateway can't run with creds in this environment, document that integration verification is deferred and proceed with unit-level wiring + a mocked provider test.」故 integration 與真實 WS-D regen 延後、真實 catalog 不動，**符合 plan GATE**。
- **目標 adapter 正確**：design line 17「對齊 sinopac adapter 的 `SinopacInstrumentProvider`——live 與 backtest 共用同一份 Rust parse」。程式碼 import/呼叫的是 **sinopac**（單一來源），**而非**舊 `shioaji` wheel。方向正確。WS-B（adapter parse 修正）在 `nautilus_trader@sinopac-adapter-clean` 分支，重建 wheel 尚未裝入本 venv——這正是 deferral 的根因，合理。
- **lazy-import wiring 健全**（已實測）：
  - `scripts.instruments`、三個 `fetch_*`、`regen_catalog_instruments` 在**無 sinopac adapter** 的本 venv 全部 import 乾淨。
  - `load_instrument` 在**呼叫時**（非 import 時）才 raise `ModuleNotFoundError`，故不會污染其餘測試套件或無關腳本的 import。
  - 全測試套件 55 passed 即是旁證。

---

## Verification output（實跑、原文貼回）

### `uv run pytest tests/ -v`（節錄尾段）
```
tests/test_instrument_provider_path.py::test_load_instrument_returns_provider_instrument PASSED
tests/test_instrument_provider_path.py::test_load_instrument_raises_when_not_found PASSED
tests/test_instrument_provider_path.py::test_make_provider_wires_client_into_provider PASSED
tests/test_instrument_provider_path.py::test_script_path_writes_via_write_data PASSED
tests/test_instrument_provider_path.py::test_legacy_hardcoded_builders_are_gone PASSED
tests/test_regen_catalog_instruments.py::test_dry_run_writes_nothing PASSED
tests/test_regen_catalog_instruments.py::test_real_regen_fixes_def_and_preserves_data PASSED
tests/test_regen_catalog_instruments.py::test_idempotent_marker_aborts_second_run PASSED
tests/test_regen_catalog_instruments.py::test_aborts_if_backup_dir_already_exists PASSED
======================== 55 passed, 1 warning in 1.19s =========================
```

### `uv run ruff check scripts/ tests/`
```
All checks passed!
```

### `uv run python -m scripts.regen_catalog_instruments --catalog-path ./catalog --gateway-url http://localhost:8000 --dry-run`
```
INFO DRY-RUN catalog=/home/cy/Code/MT5/shioaji-server/catalog gateway=http://localhost:8000
INFO Found 2 instruments: ['0050.SINOPAC', '00631L.SINOPAC']
INFO   0050.SINOPAC BEFORE: ticks=8068863 (first_ts=1583110801187000000) bars=398643 (first_ts=1583110860000000000) | def tick=0.01 lot=1000
INFO   00631L.SINOPAC BEFORE: ticks=2540646 (first_ts=1583110800586000000) bars=330672 (first_ts=1583110860000000000) | def tick=0.01 lot=1000
INFO DRY-RUN: would regenerate 2 instrument definitions (no backup, no write)
INFO DRY-RUN complete: instruments=2 regenerated=0 errors=0
```
跑完再次確認：**無** `catalog/.instruments_regenerated`、**無** `catalog_pre_instrument_regen_backup/`、`catalog_pre_restamp_backup/` 仍在。dry-run 確為唯讀。

### 退役建構子確認
`grep make_equity|contract_to_equity` 全 repo（排除 `.venv`）：僅出現在 `scripts/instruments.py` docstring 與 `tests/test_instrument_provider_path.py` 的 grep-assert 字串中（刻意以字串斷言其不存在）。`fetch_single.py` / `fetch_historical.py` / `fetch_single_ticks.py` 三處呼叫點皆改走 `from scripts.instruments import load_instrument`。✅

---

## 真實 regen 上線前的 hand-off 前置條件

deferred 的真實 WS-D regen 要能跑，依賴以下交接：

1. **裝入重建的 sinopac wheel**：WS-B（`nautilus_trader@sinopac-adapter-clean`，含 option_right "C"/"P"、真實 multiplier、`unit` lot、`nautilus_trader.adapters.sinopac` + `nautilus_pyo3.sinopac`）build 並 `uv pip install` 進本 venv，取代現有只含舊 `shioaji` adapter 的 1.224.0 wheel。
2. **gateway 起來且已登入**（WS-A 的合約欄位也需在線），有可用 creds。
3. 先跑 GATE 探測（plan Task 1 的 scratch script）確認 `2330.SINOPAC` 回傳 reference-tier tick（如 ~580 → tick 1.0）、`lot_size` 來自 `unit`，且 futures/options 不再 `bail!`。
4. 真實 regen 前再跑一次 `--dry-run` 對帳；正式跑後保留 `catalog_pre_instrument_regen_backup/` 直到 QA 簽核（plan line 125）。
5. （Minor 1 建議）正式 regen 前把逐 id 的 `load_instrument` 改為單次 `load_all_async()`/`load_ids_async([...])` 以避免 N× 全宇宙重抓。
