# Post Report — shioaji-server + sinopac adapter 修復管線

- 日期：2026-06-08
- 編排：Executor → Reviewer →（fix loop）→ 下一 batch；全部 batch 後 QA
- 範圍：兩 repo —— `shioaji-server@main`（Batch 1、2）、`nautilus_trader@sinopac-adapter-clean`（Batch 3）
- 最終結果：**3 batch 全數 APPROVED + 全鏈路 QA PASS**（122 個自動化測試全綠，修復範圍內 0 bug）

---

## 1. 總覽

| Batch | 主題 | 審查結果 | fix 循環 |
|-------|------|----------|----------|
| 1 | Gateway 韌性 (G5–G8) | APPROVED WITH NOTES | 0（2 Minor 一併收尾） |
| 2 | 時區統一 (§0/S1) | APPROVED WITH NOTES | 0（1 Important 即修） |
| 3 | Sinopac 執行 (P1–P3) | APPROVED（fix #1 後） | 1（C1 Critical + I2 Important） |
| QA | 雙 repo 全鏈路 | **PASS** | — |

測試統計：shioaji-server `pytest 24 passed` + `ruff` 淨（`src/ tests/`）；nautilus-sinopac `cargo 80 passed`（72 unit + 5 http + 3 ws）+ `pytest 18 passed`。

---

## 2. Batch 1 — Gateway 韌性 (G5–G8)　@`shioaji-server`

| Task | 缺陷 | 改動（file:line） | Commit |
|------|------|-------------------|--------|
| G5+G6 | WS handler 無連線檢查、非 disconnect 異常留殭屍/孤兒；壞 JSON 斷線 | `src/shioaji_server/app.py` `websocket_endpoint`：`try/except WebSocketDisconnect/finally` 永遠 `disconnect`+`_unsubscribe_orphaned`；迴圈開頭 `if not sj_client.connected` 守門；per-action `try/except`；`json.loads` 包 `JSONDecodeError` 回錯誤幀 | `9353ba3` |
| G7 | `update_status(api.stock_account)` 未判 None → 純期貨帳號 500 | `routes/orders.py` 加 `stock_account is not None` 對稱守衛；**揪出同類**：`routes/account.py` `list_positions`/`profit_loss` 兩處同 bug 一併修 | `ed508de` |
| G8 | `check_session` 探活無鎖 → 並發重複打 `usage()` 配額 | `client.py` 加 `_probe_lock` + 鎖內 double-check cache → single-flight | `acd7ce9` |
| 審查 Minor 收尾 | 內層 except 吞 `WebSocketDisconnect`（log 噪音+死 socket 重送）；G5/G6 無測試 | `app.py` 內層先 `except WebSocketDisconnect: raise`；新增 `tests/test_ws_resilience.py`（3 案例） | `3592867` |

審查報告：`docs/reviews/2026-06-08-batch-1-review.md`（`0bff64a`）。

---

## 3. Batch 2 — 時區統一 (§0/S1)　@`shioaji-server`

**根因**：gateway HTTP 直傳 Shioaji 原生 `t.ts`（TW 本地當 UTC 編碼，偏 +8h），而 WS 推 `str(tick.datetime)` 由 sinopac Rust 端正確減 8h → 三方不一致。根治點：gateway HTTP 單點減 8h。

**Task 1 驗證 gate（PASS）**：以既有 catalog 實證（非 live 登入）。`0050/2020-03-02` 首筆 `ts_event` 解碼 = `09:00:01 UTC`（台股開盤）→ 確認 TW-as-UTC，−8h 正確。WS 用獨立字串欄位 `str(tick.datetime)`，未受影響。

| Task | 改動（file:line） | Commit |
|------|-------------------|--------|
| 2 | `routes/market_data.py`：`TW_UTC_OFFSET_NS=28_800_000_000_000` + `_to_utc_ns()`，套至 `_fetch_ticks`/`_fetch_kbars`/`_fetch_snapshots`；`tests/test_timezone.py`（decode-based，方向鎖定） | `bf28a96` |
| 4 | `scripts/migrate_ts_to_utc.py`：catalog `ts_event`+`ts_init` −8h、重新命名、`.ts_utc_migrated` marker、`--dry-run`。**實跑 3193 檔**（11,338,824 列），雙跑安全 | `7961596` |
| 5 | `scripts/fetch_single_ticks.py` `_ns_to_trade_id` → `TradeId(f"{code}-{ts_ns}")`（納秒整數，對齊 sinopac HTTP，消同微秒重複）；`tests/test_trade_id.py` | `ef011d1` |
| 審查 Important 收尾 | migration `--dry-run` 短路 marker → 已遷移後誤報「3193 檔待 shift」（可能誘導 `--force` 雙位移）。移除 `and not dry_run`，dry-run 也尊重 marker | `950da36` |

審查報告：`docs/reviews/2026-06-08-batch-2-review.md`（`41facba`）。QA 獨立解碼已遷移 catalog 複驗：tick 首筆 → `01:00:01.187 UTC`、bar 首筆 → `01:01:00 UTC`（方向正確）。

---

## 4. Batch 3 — Sinopac 執行 (P1–P3)　@`nautilus_trader@sinopac-adapter-clean`（跨 repo）

| Task | 缺陷 | 改動（file:line） | Commit |
|------|------|-------------------|--------|
| P2 | HTTP accepted 後遲到 WS "New" 失敗 → `ACCEPTED→REJECTED` 非法狀態轉移、狀態機崩 | `execution.py:263-272`：快取單若在 `ACCEPTED/PARTIALLY_FILLED/FILLED` → warning + return，不 reject；保留 mapping | `ed362e2d69` |
| P3 | HTTP 超時即 reject，但訂單可能已成交、mapping 未設 → 隱性曝險 | `execution.py:443-467`：`except (asyncio.TimeoutError, OSError)` → 不 reject、留 SUBMITTED 待對帳；真正 business 例外仍 reject | `4f6bb70253` |
| P1（原版） | partial fill TradeId 重複損毀帳本 | （依 AUDIT 原前提）改用 `seqno` —— **此版為回歸，見下** | `d47036a79c` |

### 4.1 P1 的關鍵介入：審查攔截「反向回歸」並根治

- **審查發現（Critical C1）**：P1 把鍵改成 `seqno`，但官方文件指出成交事件 `seqno` ＝ **委託 seqno（逐單相同）**；逐筆唯一的是 deal-level `ordno`（末 3 碼成交序號）與 `exchange_seq`。原 `ordno` 本就唯一，AUDIT 前提寫反。
- **編排者實證 ground truth**：不盲信 AUDIT 也不盲信 subagent —— 用官方 [order_deal_event](https://sinotrade.github.io/tutor/order_deal_event/)（WebSearch）+ repo Rust 結構（`messages.rs`：`ordno: String`、`exchange_seq: Option<String>`）雙重核實 → 確認 Reviewer 對、AUDIT 錯。
- **根治（fix #1）**：

| 項 | 改動 | Commit |
|----|------|--------|
| C1 | Rust `order_parse.rs set_deal_fields` 透傳 `exchange_seq`（`data.exchange_seq.as_deref()`，stock/futures 雙路徑）；`make build-debug` rebuild（29.5s）；Python `execution.py:337` 鍵改 `seq = event.get("exchange_seq") or ordno`（**永不 seqno**）；改正 Rust/Python 誤導註解；測試以官方語義重寫（同 trade_id+同 seqno、異 exchange_seq/ordno → 相異 TradeId） | `85ee21e2e8` |
| I2 | P3「乾淨 adopt」過度宣稱降級為如實描述（超時單無 venue_order_id、對帳以合成 `SINOPAC-{trade_id}` 浮現為外部單）；新增 `test_p3_reconciliation_surfaces_timed_out_order_as_external` 釘死實際收斂行為 | `b4ad1c1e17` |

- **回歸守衛經蓝军驗證**：把鍵臨時改回 `seqno` → 3 條 P1 測試如預期 FAIL（`duplicate TradeId corrupts ledger`），改回後全綠 —— 測試確實保護帳本正確性。

審查 + 修復驗證報告（在 nautilus_trader repo）：`docs/reviews/2026-06-08-batch-3-review.md`（初版 `ca498e83ff` + Fix Verification 區段 `b1986fad1e`）。

---

## 5. QA（全鏈路）

- 報告：`docs/qa/2026-06-08-full-qa.md`（`9e5a64c` on `main`）。
- 122 個自動化測試全綠；容器 `docker build` + `make restart` + `curl /api/health` → `{"status":"ok","connected":true,"logged_in":true,"session_alive":true}`（本機 `.env` 有憑證，連 G8 真探活都驗到）。
- 時區資料正確性 spot-check：獨立 polars 解碼已遷移 catalog → TW 09:00 開盤 tick 現為 **01:00 真 UTC**。

---

## 6. Commit 一覽

**shioaji-server@main**：`9353ba3` `ed508de` `acd7ce9` `3592867`（B1）、`bf28a96` `ef011d1` `7961596` `950da36`（B2）、審查/QA 報告 `0bff64a` `41facba` `9e5a64c`、本更正/報告（本 commit）。

**nautilus_trader@sinopac-adapter-clean**：`ed362e2d69`(P2) `4f6bb70253`(P3) `d47036a79c`(P1 原版) → `85ee21e2e8`(C1 根治) `b4ad1c1e17`(I2)、審查/驗證報告 `ca498e83ff` `b1986fad1e`。

> ⚠️ 工作樹剩餘 `M Cargo.lock`/`M uv.lock` 為既有 build/uv 環境噪音，全程未 stage、無跨 repo 污染。

---

## 7. 待辦（範圍外，建議另開 ticket）

1. **[Medium] I2 — P3 超時單真 adopt 路徑**：目前超時單留 SUBMITTED，收斂依賴 NT 外部單處理（已如實註解 + 測試釘死），非乾淨 adopt。若要在 `submit_order` 先以 client_order_id 暫存 venue 對應、或對帳時把合成單映回 client_order_id 以真正 adopt，需動 NT 對帳管線 —— 風險較高，獨立 ticket 評估。
2. **[Low] 既有技術債（早於本批）**：
   - `scripts/fetch_single.py` 5 個 ruff F401 未使用 import（`src/ tests/` 範圍全綠，此為 scripts 範圍）。
   - `nautilus_trader` `ParquetDataCatalog.query()` 對本 catalog 拋 `MissingMetadata('instrument_id')`（parquet 從未帶該 kv，與 Batch 2 遷移無關；專案自有 `scripts/inspect_catalog.py` 讀取正常）。
   - `nautilus_trader/pyproject.toml:121` `exclude-newer = "3 days"` 觸發 uv TOML parse warning（不影響 build/test）。
3. **AUDIT.md 其餘待修項**（本批未動，供後續排程）：Rust R1–R5（malformed 訊息 panic / Close 斷線等）、Python P4–P7、scripts S3–S5、§5 Feature 機會。
