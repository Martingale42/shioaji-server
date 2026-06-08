# Code Review — Batch 1 (WS-A: Gateway 合約欄位 + enum 序列化修正)

- **Date:** 2026-06-08
- **Scope:** 2 commits
  - `cf522cd` — `routes/contracts.py` serializers 改用 enum `.value`（新增 `_enum_value` helper），補 `unit`/`multiplier`/`currency`（stock）+ `underlying_code`（futures/options）；新增 `tests/test_contracts_serialization.py`
  - `0f813cf` — `models.py` 三個 response model 補對應欄位 + 更正 `OptionsContract.option_right` 描述
- **Plan:** `docs/plans/2026-06-08-ws-a-gateway-contract-fields.md`
- **Reviewer effort:** extra-high (recall mode)

## Verdict: APPROVED WITH NOTES

實作與計畫逐項對齊，序列化 bug（`str(enum)` → `.value`）已正確修復且測試確實斷言 `.value` 而非 `str(enum)`。三個 serializer 的 dict key 與對應 Pydantic `response_model` 欄位**完全吻合**（無缺漏必填、無多餘 key），經 Pydantic 實際 validate 通過。唯一需留意項為 `unit` 在 model 宣告為 `int`，而 Shioaji 上游型別為 `Union[StrictInt, float]`——觸發機率低但機制真實，列為 Minor。

---

## Findings

### Critical
無。

### Important
無。

### Minor

1. **`unit: int` 與 Shioaji `Union[StrictInt, float]` 不對齊，理論上可觸發 500**
   `[src/shioaji_server/models.py:58]`（StockContract）、`[:77]`（FuturesContract）、`[:99]`（OptionsContract）
   Shioaji `Contract.unit` 宣告為 `typing.Union[StrictInt, float] = 0`（`.venv/.../shioaji/contracts.py:58`）。response model 收斂為 `int`。實測 Pydantic v2 對「非整數 float」**不會靜默截斷而是直接 raise ValidationError**：
   ```
   unit=1000  -> model.unit=1000 (int)         # OK
   unit=0.5   -> RAISES ValidationError
   unit=100.7 -> RAISES ValidationError
   ```
   一旦某合約回傳小數 `unit`，`list_*` 端點會整段 `ResponseValidationError`（HTTP 500），非僅該筆失敗。TWSE/TPEX/TAIFEX 的 lot size 實務上皆為整數（1000 股 / 1 口），故觸發機率低；計畫第 109 行亦已預先承認此取捨（「default to int and widen only if a real contract shows otherwise」）。下游 WS-B adapter 以 `as f64` 取用，放寬為 `float` 不會破壞 adapter。**建議**（非阻擋）：若要徹底防呆，將三處 `unit` 放寬為 `int | float`；否則維持現狀但保留此風險記錄。

2. **`_enum_value` 對「任何具 `.value` 屬性的物件」皆會回傳 `.value`，非僅 enum**
   `[src/shioaji_server/routes/contracts.py:19]`
   `getattr(value, "value", str(value))` 是 duck-typing。目前僅 enum 欄位（exchange/day_trade/currency/option_right）走此路徑，`unit`/`multiplier` 用裸 `getattr(..., 0)` 不經此 helper，故無 int 被誤取 `.value` 的風險。屬可接受的設計，但 helper 名為 `_enum_value` 卻不檢查是否為 enum，語意上略寬——僅備註，無需修改。

3. **測試未覆蓋 `OptionRight.No.value == ""` 邊界**
   `[tests/test_contracts_serialization.py]`
   實測 `OptionRight.No.value` 為空字串。選擇權合約實務上必為 Call/Put，故價值低；提及供完整性，不建議補測。

---

## 計畫對齊檢查（WS-A）

| 計畫要求 | 狀態 |
|---|---|
| `option_right` 送 `.value`（C/P）非 `str(enum)` | ✅ `[contracts.py:64]`，`test_options_right_call_is_C`/`_put_is_P` 斷言 `== "C"/"P"` 且 `!= "OptionRight.*"` |
| `currency` 送 `.value`（TWD） | ✅ stock `[:35]` / futures `[:54]`；`test_stock_currency_is_value_not_repr` |
| `day_trade` 送 `.value`（Yes/No） | ✅ `[:34]`；`test_stock_day_trade_is_value` |
| `exchange` 改 `.value`（原 `str()` 會出 `"Exchange.TSE"`） | ✅ `[:28]`；`test_stock_exchange_is_usable_code`（實測舊式確實出 `"Exchange.TSE"`） |
| 新增 `unit`/`multiplier`/`currency`（stock） | ✅ `[:35-37]` |
| 新增 `unit`/`multiplier`/`currency`/`underlying_code`（futures/options） | ✅ `[:54-57]` |
| Pydantic model 同步補欄位 | ✅ `models.py:57-59, 76-79, 98-101` |
| `option_right` 描述更正 | ✅ `models.py:92` → "'C' (Call) or 'P' (Put)" |
| serializer dict key == response_model 欄位（無缺漏/多餘） | ✅ 三者皆精確吻合，Pydantic validate 通過 |
| 防呆 `getattr` 對選擇性欄位 | ✅ 新欄位走 getattr；base 保證欄位（exchange/code）裸取，正確 |

無 silent failure：`_enum_value(None)` 回 `default`（合理，對應上游缺值）；無 except 吞錯；無遮蔽必填欄位（缺漏必填會在 response_model validate 階段 fail-fast，非靜默）。無安全疑慮（純讀取序列化）。YAGNI 良好（僅讀更多欄位 + 送 `.value`，無多餘抽象）。

---

## Verification output（實跑）

```
$ uv run python -c "import shioaji_server.routes.contracts"
IMPORT OK

$ uv run pytest tests/ -v
... 46 passed, 1 warning in 1.11s
（含 9 個 test_contracts_serialization.py 全過；無回歸）

$ uv run ruff check src/ tests/
All checks passed!
```

serializer ↔ response_model 一致性（程式化檢查）：
```
Stock:   missing_required=None  extra_filtered=None  validates=OK
Futures: missing_required=None  extra_filtered=None  validates=OK
Options: missing_required=None  extra_filtered=None  validates=OK
```

enum 行為佐證（確認 bug 真實存在且已修）：
```
str(Currency.TWD)      = 'Currency.TWD'    # 舊 bug
Currency.TWD.value     = 'TWD'             # 修正後
str(OptionRight.Call)  = 'OptionRight.Call'
OptionRight.Call.value = 'C'
Exchange.TSE.value     = 'TSE'
```

`unit` 型別風險佐證：
```
StockContract(unit=1000)  -> 1000 (int)  OK
StockContract(unit=0.5)   -> ValidationError
StockContract(unit=100.7) -> ValidationError
```

---

## Summary

WS-A 實作正確、聚焦、與計畫逐項吻合。核心 bug（enum 以 `str()` 序列化導致 adapter 解析失敗、選擇權全滅）已修，且測試確實斷言 `.value`（含 `!= "OptionRight.Call"` 反向斷言），同時涵蓋 `exchange`/`day_trade`/`currency` 的 `.value` 切換。三個 serializer 的 dict key 與對應 Pydantic response_model 欄位精確吻合，無缺漏必填亦無多餘 key，Pydantic 實際 validate 通過——不會在 `response_model` 階段炸。46 tests 全過、ruff 全清、import OK。唯一 Minor 為 `unit: int` 與 Shioaji `Union[StrictInt, float]` 不對齊：實測 Pydantic v2 會對小數 `unit` raise（整段端點 500），但台股/期權 lot size 實務皆整數、觸發機率低，且計畫已預先承認此取捨——故為 APPROVED WITH NOTES 而非 CHANGES REQUESTED。可放行進入 WS-B。
