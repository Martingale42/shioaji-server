# Backlog — shioaji-server + sinopac adapter

> 來源：2026-06-08 修復管線（見 `docs/sessions/2026-06-08-post-report.md`）審查/QA 浮現、但**不在本批範圍**的後續項。
> 跨 repo 項目統一在此追蹤（逐票標註 repo/branch）。完成後勾選並註記 commit。

---

## BL-1 ·〔Medium〕P3 超時單「真 adopt」路徑

- [ ] **狀態**：待辦
- **Repo / branch**：`nautilus_trader` @ `sinopac-adapter-clean`
- **類型**：enhancement / 真實下單對帳
- **背景**：Batch 3 P3（commit `4f6bb70253`）已把 HTTP 超時從「一律 reject」改為「保留 `SUBMITTED` 待對帳」，消除了「回報 REJECTED 但 venue 持有活單」的隱性曝險。但超時單**沒有 `venue_order_id`**（`trade_id` 在逾時的回應裡），對帳 `generate_order_status_reports` 以合成 `ClientOrderId("SINOPAC-{trade_id}")` + `VenueOrderId(trade_id)` 重建報告 → NT 較可能將其當**外部單新建**，而非 adopt 原本地 `SUBMITTED` 單，可能殘留一張重影。此為**已知、已如實註解**的收斂限制，非乾淨 adopt。
- **提案（擇一）**：
  1. `_submit_order` 送出前先以 `client_order_id` 暫存 venue 對應（樂觀映射），WS/對帳回來時據此 adopt；
  2. 對帳階段把合成 `SINOPAC-{trade_id}` 單映回原 `client_order_id`。
- **驗收**：超時後才成交的單收斂到**同一張** NT order（無重影外部單）+ 一條對帳收斂測試。
- **風險**：動 NT 對帳/外部單合併管線，需回歸現有 sinopac 整合測試。
- **參考**：`execution.py:443-467`、`:620-633`；`nautilus_trader/docs/reviews/2026-06-08-batch-3-review.md`（I2）；現況守衛測試 `test_p3_reconciliation_surfaces_timed_out_order_as_external`。

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

## BL-3 ·〔Low〕NT `ParquetDataCatalog.query()` 拋 `MissingMetadata('instrument_id')`

- [ ] **狀態**：待辦（調查）
- **Repo / branch**：`nautilus_trader` @ `sinopac-adapter-clean`（fork）
- **類型**：tech-debt / 資料相容性（早於 2026-06-08 批次）
- **背景**：NT Rust `ParquetDataCatalog.query()` 對本專案 catalog 拋 `MissingMetadata('instrument_id')`——parquet 檔**從未**帶 `instrument_id` kv metadata（只有 `ARROW:schema`）。**與 Batch 2 時區遷移無關**（遷移只改資料欄位 + 檔名，未動 parquet metadata）。專案自有 reader `scripts/inspect_catalog.py` 讀取正常（729,315 bars，日期完整）。
- **提案（擇一）**：寫入時補 `instrument_id` parquet kv metadata 使 NT Rust reader 可讀；或文件化「本 catalog 用 `inspect_catalog.py` / polars 讀取」的支援路徑。
- **驗收**：明確結論——要嘛 NT Rust reader 能讀，要嘛文件標明支援的 reader。
- **參考**：`docs/sessions/progress.json` `open_followups`；`docs/sessions/2026-06-08-post-report.md` §7.2。

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

> 另：AUDIT.md 其餘未修項（Rust R1–R5、Python P4–P7、scripts S3–S5、§5 Feature）仍在 `docs/AUDIT.md`，未納入本 backlog——待後續排程時再挑入。
