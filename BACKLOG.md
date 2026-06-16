# Backlog — shioaji-server + sinopac adapter

> 來源：2026-06-08 修復管線（見 `docs/sessions/2026-06-08-post-report.md`）審查/QA 浮現、但**不在本批範圍**的後續項。
> 跨 repo 項目統一在此追蹤（逐票標註 repo/branch）。完成後勾選並註記 commit。

## Status (the tracker — every item is a row)

Status: ✅ done · ⏳ open · ⏸️ deferred. The detailed entry for each item
follows below. Audit findings that produced these items live in
`docs/audits/2026-06-08-shioaji-server.md`.

| ID | Status | Item | Repo | Source |
|---|---|---|---|---|
| BL-1 | ✅ `2139ed2` (+NT `451ebf3`/`9df0581`) | P3 timed-out order true-adopt via custom_field token round-trip | shioaji-server + NT | `docs/plans/2026-06-08-bl1-p3-adopt.md` |
| BL-2 | ✅ `5ab7c8f` | `scripts/fetch_single.py` F401 dead-import cleanup | shioaji-server | `docs/plans/2026-06-08-bl2-bl4-cleanups.md` |
| BL-3 | ✅ | catalog metadata restamp (restore NT kv + dtypes after polars round-trip) | shioaji-server | `docs/plans/2026-06-08-bl3-catalog-metadata-restamp.md` |
| BL-4 | ✅ `f0317ffd21` | NT fork `pyproject.toml` `exclude-newer` TOML parse warning | NT fork | `docs/plans/2026-06-08-bl2-bl4-cleanups.md` |
| BL-5 | ⏳ open (blocked — live session) | instrument pipeline residual live verification (futures/options + WS-C equivalence + sinopac integration tests) | shioaji-server + NT | `docs/sessions/instruments/BL-5-handoff-checklist.md` |
| BL-6 | ✅ | Shioaji→NT instrument-definition pipeline (WS-A…D) | shioaji-server + NT | `docs/plans/2026-06-08-instrument-definitions-design.md` |
| BL-7 | ✅ `f21924c` | `scripts/` cleanup — retire Paradigm A bulk path, relocate maintenance chain | shioaji-server | commit `f21924c` |
| BL-8 | ⏸️ deferred 2026-06-16 | 0050 delisted constituents (2823/2888) 1-min bar backfill | shioaji-server | `scripts/resume_0050_fetch.sh` |

---

## BL-1 ·〔Medium〕P3 超時單「真 adopt」路徑 — ✅ 已修

- [x] **狀態**：已修（custom_field token 往返，跨 repo 三筆 commit）
- **Repo / branch**：`nautilus_trader` @ `sinopac-adapter-clean` + `shioaji-server` @ `main`
- **類型**：enhancement / 真實下單對帳
- **背景**：Batch 3 P3（commit `4f6bb70253`）已把 HTTP 超時從「一律 reject」改為「保留 `SUBMITTED` 待對帳」，消除了「回報 REJECTED 但 venue 持有活單」的隱性曝險。但超時單**沒有 `venue_order_id`**（`trade_id` 在逾時的回應裡），對帳 `generate_order_status_reports` 以合成 `ClientOrderId("SINOPAC-{trade_id}")` + `VenueOrderId(trade_id)` 重建報告 → NT 較可能將其當**外部單新建**，而非 adopt 原本地 `SUBMITTED` 單，可能殘留一張重影。此為**已知、已如實註解**的收斂限制，非乾淨 adopt。
- **修復方案**：以 `custom_field`（Shioaji `ConStrAsciiMax6`）做 deterministic blake2s token 往返：`_submit_order` 將 `client_order_id` 的 6-char base62 hash 寫入 `custom_field` → gateway 透傳 → WS 委託事件 / `list_trades` 回傳 token → adapter 反查 `_coid_token` 還原原始 `client_order_id` → NT `LiveExecutionEngine` adopt 原單。Deal 事件不帶 `custom_field`，靠先前 order-status 事件回填的映射解決。
- **驗收**：超時後才成交的單收斂到**同一張** NT order（無重影外部單）+ 8 條 BL-1 整合測試全綠。
- **跨 repo commits**：
  - `shioaji-server` @ `main`：`2139ed2` — gateway 下單透傳 custom_field 並於成交清單回傳
  - `nautilus_trader` @ `sinopac-adapter-clean`：`451ebf3` — Rust 透傳 custom_field（下單請求 + 委託/成交事件 + list_trades）
  - `nautilus_trader` @ `sinopac-adapter-clean`：`9df0581` — 超時單以 custom_field token 還原 client_order_id，對帳乾淨 adopt
- **參考**：`docs/plans/2026-06-08-bl1-p3-adopt.md`；原守衛測試 `test_p3_reconciliation_surfaces_timed_out_order_as_external` 已拆為 `test_p3_reconciliation_with_token_adopts_timed_out_order` + `test_p3_reconciliation_without_token_falls_back_to_synthetic`。

---

## BL-2 ·〔Low〕`scripts/fetch_single.py` 5× ruff F401 死 import — ✅ 已修

- [x] **狀態**：已修（`5ab7c8f` ruff --fix 移除 5 個 F401；後續 `05cfd80` WS-C 重構亦動此檔）
- **Repo / branch**：`shioaji-server` @ `main`
- **類型**：tech-debt / 清理（早於 2026-06-08 批次）
- **背景**：`uv run ruff check scripts/fetch_single.py` 報 5 個 F401 未使用 import。`src/ tests/` 範圍全綠，僅此 scripts 檔殘留。
- **驗收**：`uv run ruff check scripts/` → All checks passed（已確認）。
- **參考**：`docs/sessions/2026-06-08-post-report.md` §7.2。由 `docs/plans/2026-06-08-bl2-bl4-cleanups.md` Task 1 執行。

---

## BL-3 ·〔Low〕NT `ParquetDataCatalog.query()` 拋 `MissingMetadata('instrument_id')` — ✅ 已修

- [x] **狀態**：已修（`restamp_catalog_metadata.py` 還原 metadata + 欄位型別）
- **Repo / branch**：`shioaji-server` @ `main`
- **類型**：**Batch 2 遷移 `7961596` 引入的回歸**（polars round-trip 剝離 NT kv + 降型）
- **背景**：~~parquet 檔從未帶 `instrument_id` kv metadata~~ **更正**：`catalog.write_data()` 原本 stamp 四鍵 kv（`instrument_id`/`price_precision`/`size_precision`/`bar_type`）+ 正確欄位型別（`fixed_size_binary[16]`、`string`）。Batch 2 遷移 `migrate_ts_to_utc.py` 用 `polars.write_parquet()` 重寫檔案時，polars **剝離全部 NT kv** 並**降型**（`fixed_size_binary[16]→large_binary`、`string→large_string`）→ `ParquetDataCatalog.query()` 拋 `MissingMetadata("instrument_id")`。
- **修復**：`scripts/restamp_catalog_metadata.py` 讀已損壞的 parquet，`cast()` 回 canonical schema、從完好的 instrument 定義取 kv metadata 重寫、經 `ArrowSerializer.deserialize` 驗證後以 `catalog.write_data()` 重新持久化。**時間值不動**（已真 UTC）。
- **驗收**：`ParquetDataCatalog('catalog').query(TradeTick)` 與 `.query(Bar)` 均成功；10,609,509 ticks + 729,315 bars；first tick 01:00 UTC（真 UTC）。
- **參考**：`docs/plans/2026-06-08-bl3-catalog-metadata-restamp.md`；`docs/plans/2026-06-08-backlog-fixes-design.md`。

---

## BL-4 ·〔Low〕`nautilus_trader/pyproject.toml` `exclude-newer = "3 days"` uv TOML parse warning — ✅ 已修

- [x] **狀態**：已修（`f0317ffd21` 移除該鍵；`[tool.uv]` 現僅剩 `required-version = "==0.11.6"`）
- **Repo / branch**：`nautilus_trader` @ `sinopac-adapter-clean`（fork）
- **類型**：tech-debt / 工具設定噪音（早於 2026-06-08 批次）
- **背景**：`pyproject.toml` `exclude-newer = "3 days"` 觸發 uv settings discovery 的 TOML parse warning（`failed to parse "3 da" as year...`）。pytest/build 仍正常全綠，純噪音。
- **驗收**：`[tool.uv]` 不再含 `exclude-newer`（已確認 grep 無此鍵）。
- **參考**：`docs/sessions/2026-06-08-post-report.md` §7.2。由 `docs/plans/2026-06-08-bl2-bl4-cleanups.md` Task 2 執行。

---

## BL-5 ·〔Medium〕instrument 管線剩餘 live 驗證（期貨/選擇權 + WS-C 等價 + sinopac 整合測試）

- [ ] **狀態**：待辦（blocked，須在 live-integration session 進行）。**BL-6 收口後，本票持有 instrument 管線全部殘留 live 驗證**；股票 `2330` GATE + WS-D 真實 regen 已於 BL-6 完成，剩下都是 live 端到端比對。
- **Repo / branch**：`shioaji-server` @ `main`（依賴 `nautilus_trader` @ `sinopac-adapter-clean` 的 sinopac wheel 已裝入 shioaji-server venv + gateway 已登入）
- **類型**：verification / live integration
- **背景**：2026-06-08 instrument 定義管線（WS-A…D）已修 WS-B 的 Rust parse——期貨／選擇權改用 Shioaji 權威 `multiplier`/`unit`/`currency`（硬編碼降為 fallback），並修 `option_right` 對齊 gateway 的 `"C"`/`"P"`（選擇權不再全 `bail!`）。**Rust 單元測試（cargo 91/91，含 ETF 階）已涵蓋 parse 邏輯**。但**真實合約端到端**（gateway → `SinopacInstrumentProvider` → 建出 instrument）的期貨/選擇權、以及 WS-C 等價與 sinopac py 整合測試仍待 live session。
- **驗收**：gateway 啟動後——(1) 一個真實期貨代碼（如 `TXFG6` 之類近月）的 `multiplier` == Shioaji 合約值（非硬編碼 fallback），`lot_size` 來自 `unit`；(2) 一個真實 TXO 選擇權代碼建出 `OptionContract`（不 `bail!`），`option_right`/`strike_price`/`multiplier` 正確；(3) **WS-C 等價**：同一 `InstrumentId` 經 backtest 腳本（provider）與 live node 載入，id/tick/lot/multiplier 完全一致；(4) **sinopac Python 整合測試** `tests/integration_tests/adapters/sinopac/` 全綠（需 uv 0.11.6，見 [[gotcha-nautilus-uv-version-pin]]）。每項記錄 commit／更新本票。
- **▶ 照跑 checklist**：`docs/sessions/instruments/BL-5-handoff-checklist.md`（前置已就緒，只差起 gateway → 4 步 probe → 簽核）。
- **參考**：`docs/plans/2026-06-08-ws-b-adapter-instrument-parse.md`、`docs/plans/2026-06-08-ws-cd-backtest-and-regen.md`（Task 1 GATE）、`docs/qa/2026-06-08-instruments-full-qa.md`（Deferred 區，3 項 hand-off precondition）、`docs/sessions/instruments/progress.json`。

---

## BL-6 ·〔Tracking〕Shioaji→NT instrument 定義管線 — ✅ 已完成

- [x] **狀態**：✅ 已完成。管線核心交付齊全——WS-A…D 程式碼（3 batches APPROVED WITH NOTES + QA PASS WITH ISSUES）、sinopac wheel 建置鏈打通並裝入 venv、`2330` GATE probe 過、**WS-D 真實 catalog regen 完成且資料紅線守住**，並於 live regen 揪出+修掉 ETF tick 階 bug（見下）。**殘留的純 live 端到端驗證（期貨/選擇權、WS-C backtest==live 等價、sinopac py 整合測試）已併入 [[BL-5]] 追蹤。**
- **Repo / branch**：`shioaji-server` @ `main` + `nautilus_trader` @ `sinopac-adapter-clean`
- **類型**：feature（instrument 定義對齊 adapter 單一來源）+ deferred live verification
- **背景**：對齊 sinopac adapter 的 Rust parse 為 live + backtest 的**單一** instrument 定義來源。WS-A gateway 補 `unit`/`multiplier`/`currency`/`underlying_code` + 修 enum `.value` 序列化；WS-B adapter parse 用 Shioaji 權威 `multiplier`/`unit`/`currency`（硬編碼降 fallback）+ 修 `option_right` `"C"`/`"P"`（選擇權不再全 `bail!`）；WS-C 退役 `make_equity` 改用 `SinopacInstrumentProvider`；WS-D 重生既存 catalog instrument 定義。
- **已完成 commits**：
  - B1（WS-A，`shioaji-server`@`main`）：`cf522cd`、`0f813cf`；review `475fce9`
  - B2（WS-B，`nautilus_trader`@`sinopac-adapter-clean`）：`016b17b3`、`ed739d9e`；review `7f3ed62a`
  - B3（WS-C+D，`shioaji-server`@`main`）：`05cfd80`、`3c3b776`；review `2071cc6`
  - QA：`ef874e7`（PASS WITH ISSUES：55 pytest + 85 cargo pass、ruff clean）
- **live-integration 進度**（2026-06-09 session）：
  1. ✅ **wheel 建置鏈打通** — fork wheel `v1.226.1-sinopac`（WS-B）→ 發現 ETF bug → `v1.226.2-sinopac`（ETF 修正）已建並 pin 進 shioaji-server venv（`uv.lock` gitignored；pin commit `0926529`→`9f2b77f`）。見 [[nautilus-fork-wheel-release]]。
  2. ✅ **WS-D 真實 catalog regen 完成** — `0050.SINOPAC` tick `0.01→0.05`、`00631L.SINOPAC` 維持 `0.01`；bar/tick 筆數 + first ts_event 前後一致（紅線守住）；備份 `catalog_pre_instrument_regen_backup/`（345M，保留至 signoff）、marker `catalog/.instruments_regenerated` 已寫。
  3. ✅ **2330 GATE probe 過** — provider 建出 `2330.SINOPAC` Equity，tick 5.0 / lot 1000 / TWD。
  4. ➡️ **殘留 live 驗證移交 [[BL-5]]**：期貨/選擇權端到端、WS-C backtest==live 同 instrument 等價、sinopac Python 整合測試 `tests/integration_tests/adapters/sinopac/`（uv 版本門檻見 [[gotcha-nautilus-uv-version-pin]]）。
- **🐛 live regen 發現並修掉的 bug（ETF tick 階）**：adapter `parse_stock_to_equity` 對所有 equity 套 `twse_stock_tick_size`，但 ETF（TWSE `category "00"`）tick 階不同（`<50→0.01`、`≥50→0.05`）→ 0050 算成 0.50、00631L 算成 0.05 皆錯。修法：新增 `twse_etf_tick_size` + `category=="00"` 分流 + 6 條測試（cargo 91 全綠）。commit `nautilus_trader`@`sinopac-adapter-clean` `b053a6a`，wheel `v1.226.2-sinopac`。**preview-before-mutate 擋下了壞 tick 寫入 production**。
- **關聯**：殘留 live 驗證統一由 **[[BL-5]]** 持有（已擴充涵蓋期貨/選擇權 + WS-C 等價 + sinopac 整合測試）；下次 live session 收。
- **參考**：`docs/plans/2026-06-08-instrument-definitions-design.md`、三份 `docs/plans/2026-06-08-ws-*.md`、`docs/reviews/2026-06-08-instrument-definition-review.md`、`docs/qa/2026-06-08-instruments-full-qa.md`、`docs/sessions/instruments/`（orchestrator/progress）。

---

## BL-7 ·〔Low〕`scripts/` cleanup — 退役 Paradigm A bulk 下載路徑 + 維護鏈歸位 — ✅ 已完成

- [x] **狀態**：✅ 已完成（refactor commit `f21924c`）。
- **Repo / branch**：`shioaji-server` @ `main`
- **類型**：tech-debt / 清理
- **背景**：`scripts/` 同時養兩套世界觀——**Paradigm A（全市場 / bulk）**：`fetch_historical`（流動性分層掃描）+ `filters.py` + `load_catalog.py` + `metadata.parquet`；**Paradigm B（指定標的 / curated）**：`fetch_single` + `fetch_single_ticks` + `instruments` + `client` + `inspect_catalog`。catalog（`0050`/`00631L`）100% 由 Paradigm B 逐檔 curate 建成，Paradigm A 從未在此 catalog 跑過——`load_catalog` 實測對現有 catalog 直接死（缺 `metadata.parquet`）。
- **處置**：
  - **退役 Paradigm A**：共用 1-min bar 引擎（`VENUE`/`BAR_SPEC`/`kbars_to_bars`/`month_ranges`/`probe_kbar_availability`/`fetch_stock_bars`）抽進 `scripts/bars.py`，`fetch_single`/`fetch_single_ticks` 改 import；刪 `fetch_historical.py`/`filters.py`/`load_catalog.py`。
  - **刪 superseded 一次性碼**：`migrate_ts_to_utc.py`（剝 NT kv 的回歸根因，標「勿再跑」）、`fix_trade_ids.py`（TradeId 格式被換兩次，且 AUDIT S3 實跑 TypeError）、`resume_00631L_ticks.sh`（00631L tick 已補至 2026-06-05、crontab 自清）。
  - **維護鏈歸位**：`restamp_catalog_metadata.py`/`regen_catalog_instruments.py`/`verify_catalog_restamp.py` 移入 `scripts/maintenance/`（一次性已執行、markers 在 catalog；保留作複用），同步更新 usage docstring + 2 個測試 import。
- **驗收**：`scripts/` 15→11 檔（兩層）；`uv run ruff check scripts/ tests/` 全綠；`uv run pytest` 56 passed。
- **連動 AUDIT**：S3（`fix_trade_ids.py` TypeError）因檔案移除而失效、§5「`fetch_historical --auto-resume`」因腳本退役而 obsolete——均已於 `docs/audits/2026-06-08-shioaji-server.md` 註記。
- **未處置（刻意保留）**：`catalog_pre_*_backup/`（558M，regen/restamp 紅線回滾依據）保留至 [[BL-5]] live signoff 後再回收。

---

## BL-8 ·〔Low〕0050 宇宙下市成分股（2823 中壽 / 2888 新光金）歷史 1-min K 回補 — ⏸️ Deferred

- [ ] **狀態**：Deferred（使用者決定暫緩，2026-06-16）
- **Repo / branch**：`shioaji-server` @ `main`
- **類型**：data / 歷史回補
- **背景**：0050 point-in-time 成分宇宙（81 碼）下載現況 `79 complete · 0 partial · 2 no_data · 0 failed`。兩個 no_data 是已下市成分 **2823 中壽**、**2888 新光金**——`resume_0050_fetch.sh` 的 recent-month 探測抓不到下市代碼，catalog 內無任何 parquet（`find catalog -path '*2823*'` 為空）。日常 resume 腳本刻意跳過它們。
- **處置（待辦）**：對 2823 / 2888 以明確歷史視窗（`shioaji-data fetch-bars --start 2020-03-02 --end <各自下市日>`）單獨回補。⚠️ 風險：Shioaji 是否仍供應已下市代碼的歷史 K 線**未證實**——可能仍抓不到，屆時如實註記「venue 不提供下市歷史」後關票。
- **配額**：單檔約 5–6 年分鐘 K，估幾十 MB，遠低於每日 500MB 上限（查詢當日剩 462 MB）。
- **驗收**：2823 / 2888 在 catalog 內有 bars（涵蓋至各自下市日），或確認 venue 不提供、如實註記後關票。
- **參考**：`scripts/resume_0050_fetch.sh`（註解已標 delisted 需 historical-window fetch）；最新 inspect 見 `~/.shioaji-server/logs/0050_fetch/run-20260616-140001.log`（`79 complete, 0 partial, 2 no_data, 0 failed (81)`）。

---

> 另：稽核報告 `docs/audits/2026-06-08-shioaji-server.md`（索引見 `AUDIT.md`）其餘未修項——gateway G5–G8、scripts S2/S4/S5、sinopac Rust R1–R5、Python P4–P7、§5 Feature——刻意未納入本 backlog，待後續排程時再挑入。
