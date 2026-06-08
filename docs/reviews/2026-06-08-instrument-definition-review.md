# Review — Shioaji → NautilusTrader Instrument Definition Writer

- 日期：2026-06-08
- 範圍：把 Shioaji 合約（Stock/Future/Option/Index）寫成 NT instrument 定義並存入 `ParquetDataCatalog` 的現有功能
- 方法：讀現有實作 + 兩路並行探查（NT instrument ground truth / Shioaji 合約欄位）+ 人工核實 Shioaji SDK 與 NT 驗證規則
- 結論：**現有實作只做 Equity、且所有欄位硬編碼、丟棄全部 Shioaji 合約資料 → 產出「能跑但語意錯誤」的 instrument；Future/Option/Index 完全沒有。屬資料正確性問題，需重做。**

> **⚠️ 更新（2026-06-08，adapter 深掘後）**：本報告 §1–§7 聚焦 shioaji-server 的 `make_equity` scripts。後續探查發現 **sinopac adapter 早已有完整三型別建構**（`nautilus_trader` `crates/adapters/sinopac/src/http/parse.rs`），且 **Equity tick 已正確依 `reference` 分階**（`common/tick_size.rs`）。故「Future/Option 完全沒有」**僅對 scripts 成立，對 adapter 不成立**。真正缺陷在 adapter：① `option_right` 比對錯誤（gateway 送 `"OptionRight.Call"`，parse 比 `"Call"` → 選擇權全 `bail!`）；② multiplier/lot 硬編碼（gateway 省略 Shioaji 權威 `multiplier`/`unit`）。已批准的解法（對齊 adapter 為單一來源、退役 scripts）見 `docs/plans/2026-06-08-instrument-definitions-design.md`。

---

## 1. 現狀（What exists）

| 位置 | 做什麼 | 問題 |
|------|--------|------|
| `scripts/fetch_single.py:32` `make_equity(code)` | 由 `code` 字串造 NT `Equity` | 全欄位硬編碼，不讀任何合約資料 |
| `scripts/fetch_historical.py:38` `contract_to_equity(contract)` | 由 gateway 合約 dict 造 `Equity` | **收了 contract dict 卻只用 `code`，其餘全丟** |
| `scripts/fetch_single_ticks.py:231` | `make_equity(code)` → `write_data` | 同上 |
| `scripts/restamp_catalog_metadata.py:218` | 重持久化時也重寫 instrument 定義 | 把同一份（錯的）Equity 再寫一次 |
| `src/shioaji_server/routes/contracts.py` | gateway `/api/contracts/*` 回合約 metadata | **省略 `unit`/`multiplier`/`currency`/`underlying_code`**，下游拿不到關鍵值 |

兩個 builder 的實際內容（完全相同的硬編碼）：

```python
Equity(
    instrument_id=InstrumentId(Symbol(code), VENUE),
    raw_symbol=Symbol(code),
    currency=TWD,                                 # 硬編碼
    price_precision=2,                            # 硬編碼
    price_increment=Price(0.01, precision=2),     # 硬編碼 — 對 ≥10 TWD 的股票是錯的
    lot_size=Quantity(1000, precision=0),         # 硬編碼 — Shioaji `unit` 有真值
    ts_event=0, ts_init=0,                        # 應為合約 update_date
)
```

**沒有任何 Future / Option / Index 的建構路徑。** 期貨/選擇權即使下載了行情，也沒有對應的 NT instrument 定義可寫入 catalog（NT 回測會找不到 instrument）。

---

## 2. NT instrument ground truth（已核實，file:line）

來源：`nautilus_trader` repo `crates/model/src/instruments/*` + 安裝版 `.pyx`。

**強制驗證規則（會讓建構失敗，必須遵守）**：
- `price_increment.precision == price_precision`（`equity.rs:119`、futures/option 同）—— 不等則 `Err`。
- `price_increment > 0`；futures/option 另需 `multiplier > 0`、`lot_size > 0`。

**各型別必填欄位**：

| 型別 | 必填 | 重要選填 |
|------|------|----------|
| `Equity` | instrument_id, raw_symbol, currency, price_precision, price_increment, ts_event, ts_init | isin, **lot_size**, min/max_quantity, **min/max_price**, margin_init/maint, maker/taker_fee, info |
| `FuturesContract` | + **asset_class, underlying, activation_ns, expiration_ns, multiplier, lot_size** | exchange, min/max_price, margins, fees, info（min_quantity 預設 1） |
| `OptionContract` | + **option_kind(Call/Put), strike_price**（+ futures 全部） | 同上 |

- `AssetClass`：`Equity=2`、`Index=5`（台股 → Equity；台指期/選 → Index）。
- `OptionKind`：`Call=1`、`Put=2`。
- `activation_ns`/`expiration_ns`/`ts_event`/`ts_init`：**UnixNanos（u64 奈秒）**。
- **NT 的 Equity/Futures/Option 不支援 per-instrument 階梯式 tick scheme**——每張只有單一 `price_increment`。`TickScheme`（階梯）存在，但未接到這些型別（只有 `BettingInstrument` 用）。→ 見 §5 的 tick 難點。

**Catalog 儲存**：`write_data([instrument])` 把每張寫成 `data/<type>/<instrument_id>/` 下的 parquet，帶 kv metadata（`instrument_id`、`price_precision`、class 等），`catalog.instruments()` 反序列化回完整欄位（含 `info` 以 msgspec 二進位）。

---

## 3. Shioaji 合約 ground truth（已核實，`.venv/.../shioaji/contracts.py:46-107`）

`Contract` 基底（`Stock`/`Future`/`Option`/`Index` 皆繼承）帶有：

| Shioaji 欄位 | 型別 | 對 NT 的用途 |
|--------------|------|--------------|
| `code` / `symbol` | str | instrument_id / raw_symbol |
| `currency` | Currency=TWD | `currency`（不必硬編碼） |
| `unit` | int/float | **lot_size**（股票 1000、期/選 1）—— 真值，不必硬編碼 |
| `multiplier` | int | **futures/option 的 multiplier（契約乘數）**—— 真值（TXF=200…），**gateway 目前沒回傳** |
| `reference` | float | 昨收/參考價 → **推導 tick 階梯** 的輸入 |
| `limit_up` / `limit_down` | float | `max_price` / `min_price` |
| `delivery_date` | str "YYYY-MM-DD" | `expiration_ns`（解析 → 奈秒） |
| `delivery_month` | str "YYYYMM" | info |
| `strike_price` | int/float | option `strike_price` |
| `option_right` | enum "C"/"P"/"" | option `option_kind`（Call/Put） |
| `underlying_code` / `underlying_kind` | str（"I"/"S"…） | futures/option `underlying`、asset_class 推導 |
| `security_type` | enum IND/STK/FUT/OPT | 決定建哪種 NT 型別 |
| `category` / `name` / `day_trade` / `margin_*` | — | `info` dict（保存原始 metadata） |
| `update_date` | str | `ts_event`/`ts_init` |

**關鍵**：`multiplier` 與 `unit` 是 Shioaji **權威值**，不需要維護一張硬編碼乘數表——只要 gateway 把它們吐出來即可。唯一 Shioaji **沒有**的是 **tick size**（須依 TWSE/TAIFEX 規則推導）。

---

## 4. Gap 分析 — 現有 Equity 逐欄位

| NT 欄位 | 現值 | 正確來源 | 嚴重度 |
|---------|------|----------|--------|
| `price_increment` | `0.01` 固定 | **依 `reference` 推 TWSE 階梯**（<10→0.01, 10–50→0.05, 50–100→0.1, 100–500→0.5, 500–1000→1, ≥1000→5）¹ | 🔴 語意錯誤（見 §6） |
| `lot_size` | `1000` 固定 | Shioaji `unit` | 🟠 多數股票剛好對，但 ETF/特殊單位會錯 |
| `currency` | `TWD` 固定 | Shioaji `currency` | 🟡 台股皆 TWD，低風險 |
| `price_precision` | `2` 固定 | 依商品（多數 2；高價股仍 2） | 🟡 多數對 |
| `min_price`/`max_price` | 未設 | Shioaji `limit_down`/`limit_up` | 🟠 漏掉漲跌停資訊 |
| `isin` | 未設 | （Shioaji 無）→ 可留空 | ⚪ |
| `ts_event`/`ts_init` | `0` | Shioaji `update_date` → 奈秒 | 🟡 |
| `info`（name/category/day_trade/margin…) | 未設 | Shioaji 對應欄位 | 🟡 失去可追溯 metadata |
| Future/Option/Index | **完全沒有** | §3 對應 | 🔴 缺整類資產 |

¹ TWSE 階梯為公開周知值，落地前以官方 TWSE 文件再核對一次。

---

## 5. 完整 solution 的形狀（架構建議）

**核心理念**：單一 source-of-truth 的 instrument builder，吃 gateway 的**完整**合約資料，依 `security_type` 分派到正確 NT 型別。消除三份腳本各自硬編碼。

### 5.1 先補資料源（gateway）
`routes/contracts.py` 增補回傳：`unit`、`multiplier`、`currency`、`underlying_code`、`strike_price`/`option_right`（option）、`delivery_date`（已有）。否則 builder 拿不到 multiplier/lot_size 的權威值。

### 5.2 一個 builder 模組（取代散落的 `make_equity`/`contract_to_equity`）
`scripts/instruments.py`（或對齊 adapter 的 `InstrumentProvider` 樣式）：

- `build_instrument(contract: dict) -> Instrument`，依 `security_type` 分派：
  - **STK → `Equity`**：`currency`=contract.currency；`lot_size`=`unit`；`price_increment`=tick(reference)；`min/max_price`=limit_down/up；`info`=保存 name/category/day_trade/margin。
  - **FUT → `FuturesContract`**：`asset_class`=由 underlying_kind 推（"I"→Index）；`multiplier`=contract.multiplier；`lot_size`=`unit`(=1)；`underlying`=underlying_code；`activation_ns`/`expiration_ns`=解析 delivery_date（TW 收盤時刻 → UTC 奈秒）；`price_increment`=TAIFEX tick。
  - **OPT → `OptionContract`**：+ `option_kind`(C/P→Call/Put)、`strike_price`、同 futures 其餘。
  - **IND → `Index`**（如要存指數）：price_increment=1、size 相關依 NT Index 簽名。
- tick 推導集中在 `tw_tick_size(reference: float) -> Price`（TWSE 階梯）與 TAIFEX 商品 tick（多為整數點）。**唯一需要 domain 規則的地方**。
- `price_increment.precision` 必須等於 `price_precision`（建構前自我驗證，避免 NT raise）。

### 5.3 寫入
沿用 `catalog.write_data([instrument])`（已正確 stamp kv，BL-3 已證）。instrument 必須在對應 bar/tick 之前寫入。

---

## 6. 為什麼現狀是「能跑但錯」（風險）

1. **tick 錯 → `make_price` 產生不存在的價位**：NT 用 `price_increment`/`make_price` 把值對齊到 tick。對一檔 600 TWD（實際 tick=1.0）的股票，現值 0.01 會讓 NT 接受 600.55 這種**市場上不存在**的價位；回測撮合、滑價、限價單對齊全部失真。不會 crash，但結果靜默錯誤——比 crash 更危險。
2. **�makers/限價資訊缺失**：無 `min/max_price`（漲跌停）→ 任何依賴漲跌停的邏輯失效。
3. **缺 Future/Option instrument**：期/選回測或 live 對帳找不到 instrument 定義。
4. **既存 catalog 已被污染**：已寫入（並經 BL-3 restamp 重寫）的 Equity 都帶錯 tick；若已有人用此 catalog 回測，結論需重評。
5. **DRY 違反**：三份腳本各自硬編碼，未來改一處漏兩處。

---

## 7. NT 限制造成的最難設計點：TWSE 階梯 vs NT 單一 tick

NT 的 Equity 只能存**單一** `price_increment`，但 TWSE tick 隨價格分階。三種落地策略（需你定調，見 §8）：

| 策略 | 作法 | 優 | 缺 |
|------|------|----|----|
| A. 依 reference 取當前階 | 用昨收落在的那一階當單一 tick | 對「接近參考價」的價格正確 | 價格大幅移動跨階後不再精確；instrument 是某日快照 |
| B. 永遠用最細階 0.01 | 統一 0.01 | 永不拒絕合法價 | `make_price` 不強制真 tick；撮合/對齊偏鬆 |
| C. 自訂 tick scheme（venue 級） | 實作 TWSE `TieredTickScheme` 並掛到 venue | 完全正確 | 工程量大、NT 未原生接到 Equity，需確認可行性 |

> 建議起手 **A**（依 reference 取階，資料正確性最高且簡單），把 reference/階對應記進 `info` 以便追溯；若回測對 tick 精度要求極高再評估 C。

---

## 8. 待你定調的設計決策（下一步討論用）

1. **範圍**：只修 Equity（補正確 tick/lot/漲跌停/metadata），還是一併補 Future/Option（甚至 Index）？
2. **tick 策略**：§7 的 A / B / C？
3. **資料源**：是否同意先在 gateway 補回 `unit`/`multiplier`/`currency`/`underlying_code`（BL-1 已開過 gateway 透傳的先例）？
4. **既存 catalog**：要不要一併重生已寫入的 Equity 定義（修正 tick），還是只管未來下載？
5. **落點**：builder 放 `scripts/instruments.py`，或對齊 sinopac adapter 的 `InstrumentProvider`（讓 live 與回測共用單一定義來源）？

---

## 附錄：file:line 索引

- 現有 builder：`scripts/fetch_single.py:32`、`scripts/fetch_historical.py:38`、`scripts/fetch_single_ticks.py:231`、`scripts/restamp_catalog_metadata.py:218`
- gateway 合約：`src/shioaji_server/routes/contracts.py:8-44`；models `src/shioaji_server/models.py:44-91`
- Shioaji 合約定義：`.venv/.../shioaji/contracts.py:46-107`（`unit`/`multiplier`/`strike_price`/`option_right`/`underlying_code`/`reference`…）
- NT instrument（repo）：`crates/model/src/instruments/{equity,futures_contract,option_contract}.rs`；enums `crates/model/src/enums.rs`（AssetClass、OptionKind）
- inspect_catalog 既有「required fields」檢查：`scripts/inspect_catalog.py:33-53`

> 注：本報告引用的 NT 欄位/驗證規則皆 file:line 核實；TWSE 階梯與 TAIFEX 乘數屬周知 domain 值——`multiplier`/`unit` 走 Shioaji 權威欄位即可免硬編碼，tick 階梯落地前再對官方文件核對一次。
