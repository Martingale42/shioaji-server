# 審計報告 — shioaji-server + sinopac adapter

日期：2026-06-08 · 方法：4+4 並行 subagent 偵察 + 人工核實關鍵發現
範圍：`shioaji-server`（gateway + scripts）、`nautilus_trader@sinopac-adapter-clean`（sinopac adapter Rust + Python）

> 嚴重性：🔴 Critical（資料錯誤/崩潰/資金）· 🟠 High · 🟡 Medium · ⚪ Low
> 狀態：✅ 已修 · ⬜ 待修 · 🔬 需先驗證

---

## 0. 最關鍵發現：時區三方不一致（人工核實）

`ts_event` 在三條路徑語義不同，**sinopac adapter 自己 HTTP 與 WS 就差 8 小時**：

| 路徑 | 程式 | ts 處理 | 結果 |
|------|------|---------|------|
| sinopac WS（即時行情） | `crates/.../websocket/parse.rs` `parse_taiwan_timestamp` → `taiwan_naive_to_unix_nanos`（`-8h`） | TW 字串轉真 UTC | ✅ 真 UTC |
| sinopac HTTP（歷史/快照/request_bars） | `crates/.../http/parse.rs:56,88,118` `UnixNanos::from(ts)` 直用 | 不轉換 | ❌ TW-as-UTC（偏 +8h） |
| 下載腳本（歷史 catalog） | `scripts/fetch_single_ticks.py:91,99` 直用 gateway ts | 不轉換 | ❌ TW-as-UTC（偏 +8h） |

**根因**：gateway 的 HTTP `/market/kbars`、`/market/ticks` 回傳 Shioaji 原生 `t.ts`（TW 本地當 UTC 編碼的納秒），而 WS 推送的是 `str(tick.datetime)` 文字（TW 本地），sinopac WS 解析時減了 8h、HTTP 沒減。

**實證**：catalog 0050 某交易日 first `ts_event`→UTC 顯示 09:02、last 顯示 13:30（台股 TW 盤中時段），證實 HTTP 路徑數值是 TW-as-UTC。

**根治點（單點）**：在 **gateway** 將 HTTP 的 ts 統一轉真 UTC（`ts_utc = ts_tw - 8h`）。如此 sinopac HTTP + 下載腳本自動對齊 WS 的真 UTC，三方一致。
- 代價：已下載的 catalog 需批量校正（`ts_event/ts_init -= 28_800_000_000_000`）或重下。
- ⚠️ 改 gateway 前需確認 sinopac WS 是否也經同一 gateway 的某欄位——避免改 HTTP 連帶影響 WS。

---

## 1. TradeId 不一致

| 來源 | 格式 | 唯一性 |
|------|------|--------|
| 下載腳本 | `{code}-{TW datetime 微秒字串}` `scripts/fetch_single_ticks.py:50` | ❌ 同微秒多筆重複（實證單日 36/1922） |
| sinopac HTTP | `{code}-{納秒整數}` `crates/.../http/parse.rs:87` | ✅ 納秒唯一 |
| sinopac 訂單成交 | `{trade_id}-{ordno}` `nautilus_trader/.../execution.py:354` | ⚠️ 原判「重複」**有誤**，見 §1 末更正（deal-level `ordno` 末 3 碼即成交序號，逐筆唯一） |

下載腳本應對齊 sinopac HTTP（改用納秒整數），順帶消除重複。

> **⚠️ 更正（2026-06-08，Batch 3 審查後）**：上表「sinopac 訂單成交 `{trade_id}-{ordno}` → ❌ partial fill 重複」之判定**有誤**。依官方文件 [order_deal_event](https://sinotrade.github.io/tutor/order_deal_event/)，成交事件的 `ordno` ＝「5 碼委託 ordno ＋ 末 3 碼成交序號（001/002/003…）」，**本來就逐筆唯一**，原 `{trade_id}-{ordno}` 並不重複。真正逐單相同（不可作鍵）的是 `seqno`（「the seqno in the order is the same as seqno in the deals」）。逐筆唯一鍵應為 `exchange_seq` 或 deal-level `ordno`。詳見 §4.2 P1 更正與 `docs/reviews/2026-06-08-batch-3-review.md`。

---

## 2. shioaji-server — gateway core

| # | 嚴重 | 發現 | 位置 | 修復方向 | 狀態 |
|---|------|------|------|----------|------|
| G1 | 🔴 | `_relogin` 未持 `_lock`，與 `login()` 並發各自重建 api → 洩漏 Solace 連線（限 5/人） | `client.py` | `_relogin` 加 `async with _lock` | ✅ 1a7fa07 |
| G2 | 🟠 | `connected=False` 設在 `_reconnect_lock` 外，有視窗期請求打向死 session | `client.py` | 移入鎖內 | ✅ 1a7fa07 |
| G3 | 🟠 | `_resubscribe` 時合約表未就緒 → 靜默失敗、重連後行情停 | `client.py` | 重試等就緒 + ERROR 記錄 | ✅ 1a7fa07 |
| G4 | 🟡 | 退避 `while True` 無終止/告警，憑證失效永遠空轉 | `client.py` | 失敗計數 → CRITICAL 告警 | ✅ 1a7fa07 |
| G5 | 🟠 | WebSocket handler 無 `require_connected`，非 disconnect 異常不清理 → 殭屍連線 + 孤兒訂閱 | `app.py:152-192` | 迴圈開頭檢查連線 + `finally: disconnect` | ⬜ |
| G6 | 🟡 | WS `json.loads` 未捕獲，一條壞 JSON 永久斷線 | `app.py:155` | try/except JSONDecodeError | ⬜ |
| G7 | 🟡 | `orders.py` `update_status(api.stock_account)` 未判 None → 純期貨帳號 500 | `routes/orders.py:39` | 加 `is not None` 對稱保護 | ⬜ |
| G8 | 🟡 | `check_session` 探活無鎖，並發請求重複打 usage 浪費帳務配額 | `client.py` | probe 加 in-flight 鎖 | ⬜ |

---

## 3. shioaji-server — scripts（資料管線）

| # | 嚴重 | 發現 | 位置 | 修復方向 | 狀態 |
|---|------|------|------|----------|------|
| S1 | 🔴 | ts_event TW-as-UTC 偏 8h（見 §0） | `fetch_single_ticks.py:91,99`、gateway `routes/market_data.py:45` | gateway 統一減 8h（根治） | 🔬 |
| S2 | 🟠 | 同微秒重複 TradeId（見 §1） | `fetch_single_ticks.py:50` | 改納秒整數對齊 sinopac | ⬜ |
| S3 | 🟠 | `fix_trade_ids.py` 實跑 TypeError（`existing_data_behavior` 非 pyarrow 參數） | `fix_trade_ids.py:44` | 移除該 kwarg | ⬜ |
| S4 | 🟡 | quota 耗盡兩處 resume 提示不一致（`day` vs `last_date+1`） | `fetch_single_ticks.py:182,241` | 統一用最後成功日+1 | ⬜ |
| S5 | ⚪ | `bid_price/ask_price` 靜默丟棄（TradeTick 無此欄位） | `fetch_single_ticks.py:86` | 如需 spread 改存 QuoteTick | ⬜ |

---

## 4. sinopac adapter（nautilus_trader@sinopac-adapter-clean）

### 4.1 Rust crate — `crates/adapters/sinopac/src/`

| # | 嚴重 | 發現 | 位置 | 修復方向 |
|---|------|------|------|----------|
| R1 | 🔴 | BidAsk `bid_volume/ask_volume` 空陣列無檢查 → `[0]` panic、行情 task 崩潰 | `websocket/parse.rs:83-87` | empty check 加 volume 陣列 |
| R2 | 🟠 | 平行陣列無長度驗證 → index panic；`parse_kbars_response` 回 `Vec<Bar>` 非 Result，panic 不可捕 | `http/parse.rs:72-94,107-124` | 開頭驗證所有陣列等長，否則 bail |
| R3 | 🟠 | Close frame 無條件 `return None` 殺死 handler → 靜默斷線 | `websocket/handler.rs:160-163` | 改 warn + 觸發 reconnect |
| R4 | 🟡 | connect 前 subscribe 送進已丟棄 channel → 靜默丟失、reconnect 不重播 | `websocket/client.rs:93` | subscribe 先檢查 `is_connected` |
| R5 | 🟡 | `next_message()` 並發 `take()` receiver 被搶 → 提前回 None | `websocket/client.rs:280-289` | 並發保護或文件化單消費者 |

> HTTP client 無 retry（設計選擇，gateway 統一處理）；`SnapshotData.ts` 註解寫 ms 實為 ns（文件錯誤）。

### 4.2 Python — `nautilus_trader/adapters/sinopac/`

| # | 嚴重 | 發現 | 位置 | 修復方向 |
|---|------|------|------|----------|
| P1 | 🔴 | partial fill TradeId 唯一性 ⚠️**原前提有誤，見下方更正** | `execution.py:354` + `order_parse.rs set_deal_fields` | ~~用全域唯一 `seqno`~~ → 改用 `exchange_seq`／deal-level `ordno`；**`seqno` 逐單不可作鍵** |
| P2 | 🟠 | HTTP accepted 後 WS "New" 失敗 → ACCEPTED→REJECTED 非法狀態轉移、狀態機崩 | `execution.py:259-265` | 末態訂單不再 reject，檢查現狀 |
| P3 | 🟠 | HTTP 超時即 reject 但訂單可能已成交、mapping 未設 → 隱性曝險 | `execution.py:424-431` | 超時保留 pending/accepted，待 WS/對帳補全 |
| P4 | 🟡 | `instrument=None` 未防守 → 期貨單以 STOCK 市場送出 | `execution.py:397` | None 則 reject |
| P5 | 🟡 | 帳戶 `locked=0` 硬編碼 → 可用資金高估 | `execution.py:193` | 接 `/account/margin` |
| P6 | 🟡 | 對帳 `filled_qty` 恆 0（Rust `TradeInfo` 無此欄位）→ 重連重複送單 | `execution.py:592` | Rust model 補 filled_qty |
| P7 | ⚪ | `lru_cache` 快取 mutable handler list、子訂單共用 command_id | `factories.py:116`、`execution.py:522` | 移除快取 / 各自 UUID |

> **⚠️ P1 更正（2026-06-08，Batch 3 審查後）**：原「修復方向：用全域唯一 `seqno`」是**錯的**——對成交事件而言 `seqno` ＝ 委託 seqno，**同一張單所有 partial fill 相同**，作 TradeId 鍵反而製造重複、損毀帳本。真正逐筆唯一的是 `exchange_seq`（交易所成交序號）與 deal-level `ordno`（末 3 碼為成交序號）。
> 第一次實作（commit `d47036a79c`）依原前提改用 `seqno` → 屬**回歸**，已於審查攔截並修正：鍵改為 `event.get("exchange_seq") or ordno`（Rust 透傳 `exchange_seq`），commit `85ee21e2e8`。依據：官方 [order_deal_event](https://sinotrade.github.io/tutor/order_deal_event/)；詳見 `nautilus_trader` repo `docs/reviews/2026-06-08-batch-3-review.md`（C1）。**教訓：成交回報欄位唯一性以官方文件／實倉 multi-fill 回報為準，勿憑靜態分析臆測。**

---

## 5. Feature 機會（價值/成本）

### shioaji-server / gateway
- **高/S** WS 推送 `session_down`/`session_recovered` + `GET /api/ws/subscriptions`：NT adapter 重連後狀態可感知
- **高/S** Docker HEALTHCHECK 接 `/api/health`：自愈失敗的「假活」容器可被 `docker ps` 看出
- **高/S** session 事件計數器併入 `/api/health`：自愈黑盒變可觀測
- **中/S** 錯誤回應結構化（統一 `.error` vs `.detail`）
- **中/S** `fetch_historical --auto-resume`（讀 catalog 最後日自動續）

### sinopac adapter
- **高/S** 重連後 Python 訂閱集合 ground-truth 重送（防 Rust/Python 訂閱狀態分歧靜默斷流）
- **高/M** `request_bars` 跨月分頁（現在可能靜默截斷只回最後一月）
- **高/M** `generate_fill_reports` 補全（接 `/account/pnl`，重啟後對帳不再從零）
- **中/S** 零股/盤中零股 order_lot 透傳；margin 帳務（locked）

---

## 6. 建議優先級

1. **🔴 已止血**：G1-G4 session 自愈回歸（✅ commit 1a7fa07）
2. **🔴 資金/帳本**：P1（成交重複 TradeId）、P3（超時隱性曝險）、P2（狀態機崩）— sinopac 真實下單路徑
3. **🔴 崩潰**：R1、R2（malformed 訊息 panic）
4. **🔴 資料正確性**：S1/§0 時區（先驗證 §0 的 WS 是否同源，再決定 gateway 統一減 8h + 批量校正 catalog）
5. **🟠 韌性**：G5（ws 殭屍）、R3（Close 斷線）、S2/§1 TradeId 對齊
6. **Feature**：先做 3 個 S（WS 狀態推送、HEALTHCHECK、訂閱 ground-truth 重送）

> 註：sinopac adapter 的 bug（§4）位於 `nautilus_trader` repo，需在該 repo 修復。本 gateway repo 可先做 §0 的時區統一（根治 S1 + sinopac HTTP）。
