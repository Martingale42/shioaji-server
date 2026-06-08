# Backlog — shioaji-server + sinopac adapter

> 來源：2026-06-08 修復管線（見 `docs/sessions/2026-06-08-post-report.md`）審查/QA 浮現、但**不在本批範圍**的後續項。
> 跨 repo 項目統一在此追蹤（逐票標註 repo/branch）。完成後勾選並註記 commit。

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

## BL-2 ·〔Low〕`scripts/fetch_single.py` 5× ruff F401 死 import

- [ ] **狀態**：待辦
- **Repo / branch**：`shioaji-server` @ `main`
- **類型**：tech-debt / 清理（早於 2026-06-08 批次）
- **背景**：`uv run ruff check scripts/fetch_single.py` 報 5 個 F401 未使用 import。`src/ tests/` 範圍全綠，僅此 scripts 檔殘留。
- **提案**：`uv run ruff check --fix scripts/fetch_single.py`（應可自動移除），人工確認無誤刪後 commit。
- **驗收**：`uv run ruff check scripts/` 乾淨。
- **參考**：`docs/sessions/2026-06-08-post-report.md` §7.2。

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

## BL-4 ·〔Low〕`nautilus_trader/pyproject.toml` `exclude-newer = "3 days"` uv TOML parse warning

- [ ] **狀態**：待辦
- **Repo / branch**：`nautilus_trader` @ `sinopac-adapter-clean`（fork）
- **類型**：tech-debt / 工具設定噪音（早於 2026-06-08 批次）
- **背景**：`pyproject.toml:121` `exclude-newer = "3 days"` 觸發 uv settings discovery 的 TOML parse warning（`failed to parse "3 da" as year...`）。pytest/build 仍正常全綠，純噪音。
- **提案**：改為 uv 接受的 `exclude-newer` 格式（RFC 3339 timestamp，如 `"2026-01-01T00:00:00Z"`），或移除該鍵（確認用途後）。
- **驗收**：`uv run ... pytest` 不再印該 warning。
- **參考**：`docs/sessions/2026-06-08-post-report.md` §7.2。

---

## BL-5 ·〔Medium〕期貨／選擇權 instrument 的 live 端到端驗證（instrument 定義管線 WS-B 延後項）

- [ ] **狀態**：待辦（blocked，須在 live-integration session 進行）
- **Repo / branch**：`shioaji-server` @ `main`（依賴 `nautilus_trader` @ `sinopac-adapter-clean` 的 sinopac wheel 已裝入 shioaji-server venv + gateway 已登入）
- **類型**：verification / live integration
- **背景**：2026-06-08 instrument 定義管線（WS-A…D）已修 WS-B 的 Rust parse——期貨／選擇權改用 Shioaji 權威 `multiplier`/`unit`/`currency`（硬編碼降為 fallback），並修 `option_right` 對齊 gateway 的 `"C"`/`"P"`（選擇權不再全 `bail!`）。**Rust 單元測試（cargo 85/85）已涵蓋 parse 邏輯**（777/99 非表內值證明走權威路徑、`0` 證明回退、舊拼法 `"Call"` 仍 bail）。但**端到端**（gateway 真實合約 → `SinopacInstrumentProvider` → 建出 instrument）此前因環境缺 sinopac wheel + gateway creds 而延後；GATE probe／首次 live regen 先只驗 `2330`（股票）。
- **驗收**：gateway 啟動後，經 provider 載入並比對——(1) 一個真實期貨代碼（如 `TXFG6` 之類近月）的 `multiplier` == Shioaji 合約值（非硬編碼 fallback），`lot_size` 來自 `unit`；(2) 一個真實 TXO 選擇權代碼建出 `OptionContract`（不 `bail!`），`option_right`/`strike_price`/`multiplier` 正確。記錄 commit／更新本票。
- **參考**：`docs/plans/2026-06-08-ws-b-adapter-instrument-parse.md`、`docs/plans/2026-06-08-ws-cd-backtest-and-regen.md`（Task 1 GATE）、`docs/qa/2026-06-08-instruments-full-qa.md`（Deferred 區）、`docs/sessions/instruments/progress.json`。

---

> 另：AUDIT.md 其餘未修項（Rust R1–R5、Python P4–P7、scripts S3–S5、§5 Feature）仍在 `docs/AUDIT.md`，未納入本 backlog——待後續排程時再挑入。
