# Backlog Fixes — Design (BL-1 … BL-4)

- 日期：2026-06-08
- 來源：`docs/BACKLOG.md`（2026-06-08 修復管線審查/QA 浮現的後續項）
- 方法：brainstorming（探查 → 方案 → 使用者定調）後的已批准設計；下一步由 writing-plans 產出逐項 implementation plan，再交 orchestrator session 執行。
- 跨 repo：BL-2/BL-3 → `shioaji-server@main`；BL-4 → `nautilus_trader@sinopac-adapter-clean`；**BL-1 跨兩 repo**。

> **本設計修正一項先前誤判**：BL-3 原於 BACKLOG/AUDIT 標為「既有債、與 Batch 2 無關」。經實證（`catalog.write_data()` stamp `instrument_id`/`price_precision`/`size_precision`/`ARROW:schema` 四鍵；polars `write_parquet` round-trip 後僅剩 `ARROW:schema`），確認 **BL-3 是 Batch 2 遷移 `7961596` 引入的回歸**。已重新定性。

---

## 決策摘要（使用者已批准）

| 項 | 主題 | 方案 | Repo |
|----|------|------|------|
| BL-1 | P3 超時單真 adopt | 跨 repo `custom_field` token 往返（Hyperliquid 式）；token = `client_order_id` 確定性 6 字元雜湊 | shioaji-server + nautilus_trader |
| BL-2 | `fetch_single.py` 死 import | `ruff --fix` 移除 5× F401 | shioaji-server |
| BL-3 | 還原遷移剝離的 NT metadata | 經 NT `catalog.write_data()` 重新持久化已位移檔（值不再動）+ 修遷移腳本未來保留 kv | shioaji-server |
| BL-4 | pyproject 無效 `exclude-newer` | 移除 `[tool.uv] exclude-newer = "3 days"` 鍵 | nautilus_trader |

**建議批次順序**：BL-2 → BL-4（zero-risk 暖身）→ BL-3（資料紅線，dry-run gate）→ BL-1（跨 repo + Rust rebuild，最後）。

---

## BL-1 — P3 超時單真 adopt（跨 repo token 往返）

### 問題
HTTP `place_order` 超時 → 單留 `SUBMITTED`、無 `venue_order_id`、`_trade_id_to_client_order_id` 未填。後到的成交/委託事件（`_handle_deal_event` / `_handle_order_status_event`）與對帳（`generate_order_status_reports`）以 venue `trade_id` 找不回原始 `client_order_id` → 合成 `SINOPAC-{trade_id}` → **NT 以 `client_order_id` 為主鍵比對**，合成 id 不匹配 cache 內 SUBMITTED 單 → 建重影外部單。（NT 對帳主鍵實證：`nautilus_trader/live/execution_engine.py` `_cache.order(report.client_order_id)` 命中即 adopt、補 venue_order_id；未命中才 `_generate_order(is_external=True)`。）

### 方案：`custom_field` token 端到端往返
NT `client_order_id` 過長、Shioaji `custom_field` 僅 6 ASCII 字元（`ConStrAsciiMax6`）→ 不存完整 id，改存**確定性 6 字元雜湊 token**（restart-safe：對帳時對 cache 內每張候選單重算 hash6 比對，無需持久化 map）。

**Gateway（shioaji-server）**
- `PlaceOrderRequest` 增 `custom_field: str | None`（models）。
- `routes/orders.py:69,81` `api.Order(..., custom_field=req.custom_field or "")`。
- `on_order`（`client.py:378`）已 verbatim 轉發 Shioaji `msg`（含 `order.custom_field`）→ WS 事件免改；**確認** deal event 的 `msg` 確實帶 `custom_field`（Shioaji StockOrder/StockDeal 帶 `custom_field`）。
- `list_trades`/order-status REST 回應補 `custom_field` 欄位（reconciliation 用）。

**Adapter Rust（nautilus_trader）**
- HTTP place_order request model（`http/models.rs` + `http/client.rs:194`）增 `custom_field`。
- order/deal 事件結構（`websocket/messages.rs`）+ `TradeInfo`/list_trades 回應反序列化 `custom_field`（`#[serde(default)] Option<String>`，無 unwrap）。
- 透傳 `custom_field` 到 Python dict（order_parse / http parse）。Rebuild（`make build-debug`）。

**Adapter Python（nautilus_trader）**
- 新 helper `_client_order_id_token(client_order_id) -> str`：6 字元 base62/hex（如 `blake2s(coid, digest_size=…)` 取前 6）。
- `_submit_order`：`custom_field = token`；送出前存 `_token_to_client_order_id[token] = client_order_id`（session 內快取）。
- `_handle_deal_event` / `_handle_order_status_event`：`client_order_id` 解析優先序 → `_trade_id_to_client_order_id` → `custom_field` token 還原（先查快取，未命中則對 cache 內單重算 hash6 比對）→ 合成 fallback。
- `generate_order_status_reports`：同上優先序還原原始 `client_order_id`（取代直接合成 `SINOPAC-{trade_id}`）→ NT adopt。

### 測試
- session 內：mock `place_order` 超時（單留 SUBMITTED）→ 後到帶 token 的成交事件 → adopt **同一張** NT order（無重影外部單），venue_order_id 補上。
- restart：清空 in-memory map → 對帳以 hash6 對 cache 單重建關聯 → adopt。
- token 還原優先序：mapping 命中時不重算；hash 碰撞僅在 active 候選間、可忽略。
- 既有 P1/P2/P3 測試不回歸。

### 風險
跨 repo + Rust rebuild；須先確認 Shioaji deal event 回傳 `custom_field`（若否，退而用 order-status 事件 / list_trades 的 custom_field 還原）；hash6 碰撞（active 單少、僅候選間比對，可接受）。

---

## BL-3 — 還原遷移剝離的 NT metadata（NT API 重新持久化）

### 問題（Batch 2 回歸）
`scripts/migrate_ts_to_utc.py:69,103` `pl.read_parquet` → `shifted.write_parquet()`。polars 不保留 NT 的 file-kv → 剝離 `instrument_id`/`price_precision`/`size_precision`（僅剩 `ARROW:schema`）→ `ParquetDataCatalog.query()` 拋 `MissingMetadata("instrument_id")`（`crates/serialization/src/arrow/mod.rs:68` `KEY_INSTRUMENT_ID`；各 `arrow/*.rs` decoder 強制）。**時間值已正確位移（QA 證 01:00 UTC），不可再減 8h。**

### 方案：重新持久化（值不動，只還原 metadata）
新腳本 `scripts/restamp_catalog_metadata.py`：
- 對每個 bar/trade_tick 的已位移 parquet：polars 讀欄位 → 由 catalog 內**完整無損的 instrument 定義**（equity defs 當初被遷移排除、metadata 完好）取得 `price_precision`/`size_precision` → 重建 NT `TradeTick`/`Bar` 物件 → `catalog.write_data()` 重寫（NT 重生四鍵 kv + 正確 schema + 以 ts 範圍重新命名）→ 刪舊 metadata-less 檔。
- `--dry-run`（報數、不寫）；新 marker `catalog/.metadata_restamped`（**不沿用 `.ts_utc_migrated`**）；guard 確保不重複、不碰時間值。
- **並修 `migrate_ts_to_utc.py`**：未來改走 NT API 或保留 kv（避免再犯）。

### 測試
- restamp 後 `ParquetDataCatalog.query()` 能成功讀回 bar 與 trade_tick（不再 `MissingMetadata`）。
- 抽樣 `ts_event` 仍 = 01:00 UTC（證明未二次位移）；四鍵 kv 齊全；row 數不變。
- 雙跑 idempotent（marker 擋）。

### 風險 / 附帶
3193 檔一次性較慢（可接受，一次性）；reconstruct 需正確 precision（從 instrument def 取，可靠）。附帶更正 `BACKLOG.md` BL-3 條目、`AUDIT.md`/`progress.json` 之「pre-existing」措辭為「Batch 2 回歸」。

---

## BL-2 — `scripts/fetch_single.py` 5× ruff F401（機械性）

`uv run ruff check --fix scripts/fetch_single.py` 移除 `BarSpecification`/`BarAggregation`/`PriceType`/`Venue`/`Currency` 五個未使用 import（已 `--diff` 預覽乾淨）。驗收：`uv run ruff check scripts/` 乾淨。

---

## BL-4 — `nautilus_trader` pyproject 無效 `exclude-newer`（機械性）

移除 `pyproject.toml:121` `[tool.uv]` 下的 `exclude-newer = "3 days"`（uv 不支援相對語法；fork 追 upstream，相對 cutoff 無意義）。驗收：`uv run ... pytest` 不再印 `failed to parse "3 da" as year` TOML parse warning；`required-version` 等其餘 `[tool.uv]` 設定保留。

---

## 跨 repo / 編排注意

- BL-1 的 gateway 部分在 `shioaji-server`、adapter 部分在 `nautilus_trader@sinopac-adapter-clean` —— **不可混 commit**；建議「gateway 先、Rust rebuild、Python 後」三段。
- BL-2/BL-3 commit 於 `shioaji-server@main`；BL-4 commit 於 `nautilus_trader@sinopac-adapter-clean`。
- 規則沿用：Python 一律 `uv run`；nautilus_trader Python 測試用 `uv run --active --no-sync pytest …`（bare 會失敗）；Rust 改動後 `make build-debug` 再測；commit body 中文 OK、無 AI attribution、不 `--no-verify`；Rust 不對外部資料 unwrap/expect。
