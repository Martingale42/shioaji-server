# Instrument Definitions — Design (Shioaji → NautilusTrader)

- 日期：2026-06-08
- 來源：`docs/reviews/2026-06-08-instrument-definition-review.md`（初版聚焦 scripts）+ 後續 adapter 深掘（本設計修正初版的不準確處）
- 方法：brainstorming（探查 adapter + NT/Shioaji ground truth → 使用者定調 5 項決策）後的已批准設計；下一步由 writing-plans 產出逐 WS implementation plan，再交 orchestrator 執行。

> **修正初版 review 的不準確處**：初版說「沒有 Future/Option 建構路徑」。實際上 **sinopac adapter 早已有完整三型別建構**（`crates/adapters/sinopac/src/http/parse.rs`），且 **Equity tick 已正確依 `reference` 分階**（`common/tick_size.rs`）。真正問題在 adapter 的 multiplier/lot 硬編碼與 option_right 解析錯誤，加上 gateway 省略權威欄位。scripts 的 `make_equity` 是另一條劣化路徑，本案一併退役。

---

## 使用者已批准的 5 項決策

1. **範圍**：全修（Equity / Future / Option；Index 視需要）。
2. **tick 策略**：A — 依 `reference` 取當前階（**adapter 已實作正確**）。
3. **資料源**：gateway 補回 `unit`/`multiplier`/`currency`/`underlying_code`。
4. **既存 catalog**：一併重生已寫入的 instrument 定義。
5. **架構**：對齊 sinopac adapter 的 `SinopacInstrumentProvider`——live 與 backtest 共用**同一份** Rust parse。

---

## Ground truth（已 file:line 核實）

**NT 驗證規則**：`price_increment.precision == price_precision`（否則建構 `Err`）；futures/option 另需 `multiplier>0`、`lot_size>0`。OptionContract **不**校驗 `strike_price` 精度（`option_contract.rs:147` 僅校驗 price_increment）。

**Shioaji `Contract`（`.venv/.../shioaji/contracts.py:46-107`）權威欄位**：`unit`（lot）、`multiplier:int`（契約乘數）、`currency`、`reference`、`limit_up/down`、`delivery_date`、`strike_price`、`option_right`（enum，value "C"/"P"）、`underlying_code`/`underlying_kind`、`security_type`。

**adapter 現況 scorecard（`http/parse.rs`）**：

| 項 | 狀態 | 位置 |
|----|------|------|
| 三型別建構 | ✅ | `parse_stock_to_equity`/`parse_futures_to_contract`/`parse_options_to_contract` |
| Equity tick 依 reference（TWSE tiers） | ✅ 正確 | `common/tick_size.rs:29` |
| max/min_price ← limit_up/down | ✅ | parse.rs:145,200,268 |
| 到期 ← delivery_date | ✅ | parse.rs:192,260 |
| **option_right 解析** | 🔴 壞 | parse.rs:252 比對 `"Call"/"Put"`，但 gateway 送 `str(OptionRight.Call)` = **`"OptionRight.Call"`** → 全部 `bail!`，選擇權建不出 |
| multiplier | 🟠 硬編碼 | `common/instrument.rs:34,49`（keyed on `category`，值脆弱，未知 fallback 2000/4000） |
| lot_size | 🟠 硬編碼 | `STOCK_LOT_SIZE=1000`/`CONTRACT_LOT_SIZE=1` |
| currency | 🟡 硬編碼 TWD | parse.rs:139,184,244 |
| underlying | 🟡 設成 root symbol 非 `underlying_code` | parse.rs:208,276 |
| info（name/category/day_trade） | 🟡 未保存 | — |
| `extract_root_symbol` | ⚪ 死碼（parse 用 `category`，未呼叫此 helper） | `common/instrument.rs:69` |

**gateway 缺口（`routes/contracts.py`）**：stock/futures/options dict 皆**省略** `unit`/`multiplier`/`currency`/`underlying_code`；`option_right` 用 `str(enum)` → 送出 `"OptionRight.Call"`（錯誤格式）。這是 adapter 硬編碼與 option bug 的根因。

---

## 設計：4 條工作流（WS）

### WS-A · Gateway 修正 + 補欄位　@`shioaji-server@main`
- `routes/contracts.py`：
  - **修 `option_right`**：改送 `contract.option_right.value`（"C"/"P"）——並讓 WS-B 對齊此格式（見下）。
  - **補欄位**：stock/futures/options dict 加 `unit`、`multiplier`、`currency`（`str(contract.currency.value)`），futures/options 加 `underlying_code`。
- `models.py`：對應 `StockContract`/`FuturesContract`/`OptionsContract` response model 加新欄位。
- **驗收**：`/api/contracts/*` 回傳含 `unit`/`multiplier`/`currency`/`underlying_code`；`option_right` 為 "C"/"P"。

### WS-B · Adapter parse 修正　@`nautilus_trader@sinopac-adapter-clean`
- `http/models.rs`：`StockContract`/`FuturesContract`/`OptionsContract` 加 `unit`、`multiplier`、`currency`、`underlying_code`（`#[serde(default)]`，無 unwrap on external data）。
- `http/parse.rs`：
  - **multiplier**：`Quantity::new(contract.multiplier as f64, 0)` 用 Shioaji 權威值；`multiplier==0` 時才 fallback 到 `futures_multiplier`/`options_multiplier`（保留為防呆，不再是主路徑）。
  - **lot_size**：用 `contract.unit`（fallback STOCK/CONTRACT_LOT_SIZE）。
  - **option_right**：對齊 WS-A 的 "C"/"P"（`"C"=>Call, "P"=>Put`），未知值 `bail!` 並在訊息附原值。
  - **underlying**：futures/options 用 `contract.underlying_code`（非 root symbol）。
  - **currency**：用 `contract.currency`（fallback TWD）。
  - **info**：保存 name/category/day_trade 等到 `info`（可選，YAGNI 視價值）。
  - `extract_root_symbol` 死碼：移除或實際接上（與 `category` 路徑二擇一，消除不一致）。
- Rebuild（`make build-debug`）。
- **驗收**：`cargo test -p nautilus-sinopac --features python` 綠（新增：option_right "C"/"P"、真實 multiplier、unit）；options 不再 `bail!`。

### WS-C · Backtest 同源　@`shioaji-server@main`
- 退役 `scripts/fetch_single.py:make_equity`、`scripts/fetch_historical.py:contract_to_equity`、`fetch_single_ticks.py` 的 `make_equity` 用法。
- 改用 `SinopacInstrumentProvider`：腳本建 pyo3 `nautilus_pyo3.sinopac.SinopacHttpClient`（指向 gateway base URL）→ `provider.load_all_async()`（或 `load_ids_async([id])`）→ 取 `provider.find(instrument_id)` → `catalog.write_data([instrument])`。
- **前置 gate**（放進 plan Task 1）：確認 pyo3 `SinopacHttpClient` 能在腳本中獨立指向 gateway 建構（不需完整 live node）。若不可行，退而用 pyo3 `request_*_instruments` 直呼。
- **驗收**：腳本寫入的 instrument 與 adapter live 載入定義一致（同一 Rust parse）；單檔/批量下載皆正確。

### WS-D · 重生既存 catalog　@`shioaji-server@main`
- 用 WS-C 的同源 provider 重新產生既存 catalog 內所有 instrument 定義（目前只有 Equity），覆寫 `data/.../instrument` 定義（沿用 BL-3 的 `write_data` + 備份模式，**只動 instrument 定義，不動 bar/tick 資料**）。
- **驗收**：`catalog.instruments()` 回正確 tick（依各檔 reference 分階）/lot/limit；備份 `catalog_pre_instrument_regen_backup/` 保留待 QA。

---

## 相依與風險

- **順序**：A → B → C → D。B 的整合測試需 A 上線的 gateway（單元測試用 fixture 即可）。C/D 需 B rebuild 後。
- **跨 repo**：A/C/D 在 `shioaji-server@main`；B 在 `nautilus_trader@sinopac-adapter-clean`。不可混 commit。
- **資料正確性紅線**：tick/multiplier/lot 直接影響回測撮合與部位估值——以真實合約值驗證，Rust 不對外部資料 unwrap/expect。
- **既存 catalog**：WS-D 重生前備份；只動 instrument 定義。

---

## 規則（沿用）

Python 一律 `uv run`；nautilus_trader Python 測試 `uv run --active --no-sync pytest …`（bare 會失敗，勿 re-sync venv）；Rust 改後 `make build-debug` 再測；commit body 中文 OK、無 AI attribution、不 `--no-verify`；Rust 無 unwrap/expect on external data。
