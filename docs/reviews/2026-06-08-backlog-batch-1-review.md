# Backlog Batch 1 — BL-2 + BL-4 Mechanical Cleanups 程式碼審查報告

**審查日期：** 2026-06-08
**審查範圍：**
- BL-2: `shioaji-server@main` commit `5ab7c8f`（`scripts/fetch_single.py` 移除 5 個 F401 未使用 import）
- BL-4: `nautilus_trader@sinopac-adapter-clean` commit `f0317ffd21`（移除 `pyproject.toml` 無效 `exclude-newer`）

**對照文件：** `docs/plans/2026-06-08-bl2-bl4-cleanups.md`

## 裁決：APPROVED WITH NOTES

兩個 commit 皆依計畫完成，正確性無疑。有一個 Important 觀察（BL-4 遺漏同 repo 第二個 `pyproject.toml` 的相同問題）與一個 Minor 觀察（import 排序不符 isort 規範）。

---

## 驗證輸出

### BL-2 — `uv run ruff check scripts/`
```
All checks passed!
```

### BL-4 — `grep 'exclude-newer' pyproject.toml`
```
(empty — line removed)
```

`[tool.uv]` section 保留 `required-version = "==0.11.6"`，下方 `[tool.isort]` 未受影響。✓

---

## 逐項評估

### BL-2 — 移除 `scripts/fetch_single.py` 5 個未使用 import

**正確性 vs 計畫：達成。**
- 移除 `BarSpecification`、`BarAggregation`、`PriceType`、`Venue`、`Currency` 五個符號。
- `grep` 確認五個符號在 `fetch_single.py` 中無任何使用。✓
- 保留的 `BarType`、`InstrumentId`、`Symbol`、`Price`、`Quantity`、`Equity`、`ParquetDataCatalog` 全有使用位點。✓
- `scripts/fetch_single_ticks.py` 透過 `from scripts.fetch_single import make_equity` 依賴此檔；`make_equity` 未使用任何被移除符號。✓ 無 cross-file 破壞。
- Commit message 與計畫完全一致。✓

**無邏輯修改**：diff 僅觸及 import 行 16-19，函式體未改動。✓

### BL-4 — 移除 `nautilus_trader/pyproject.toml` 無效 `exclude-newer`

**正確性 vs 計畫：達成。**
- 移除 root `pyproject.toml:121` 的 `exclude-newer = "3 days"`。✓
- `[tool.uv]` section 結構保持完整（`required-version` 不動）。✓
- Commit message 與計畫完全一致。✓

---

## 問題清單

### Critical
無。

### Important

1. **[`nautilus_trader/python/pyproject.toml:63`] BL-4 遺漏同 repo 第二個 `exclude-newer = "3 days"`。**
   `python/pyproject.toml` 的 `[tool.uv]` section 有完全相同的無效行：
   ```toml
   [tool.uv]
   required-version = "==0.11.6"
   exclude-newer = "3 days"
   ```
   實測 `uv run --directory python` 仍會印出 `Failed to parse` 警告。
   原始計畫（`docs/BACKLOG.md` BL-4）僅指定 `pyproject.toml:121`，未掃描同 repo 其他 pyproject；
   但 BL-4 的意圖是「消除 uv parse warning」，此處是同類遺漏。
   **建議**：補一個 commit 移除 `python/pyproject.toml:63` 的 `exclude-newer` 行。

### Minor

1. **[`scripts/fetch_single.py:19`] import 排序不符 isort（I001）— 預存問題。**
   `from nautilus_trader.model.instruments import Equity`（line 19）應排在
   `from nautilus_trader.model.objects import ...`（line 18）之前（alphabetical）。
   此為 BL-2 之前即存在的問題，且 ruff 的 I 規則未在本 repo 啟用，故 `ruff check` 仍全綠。
   BL-2 有機會順手修正但未做。不阻擋。

---

## 結論

BL-2（dead imports）與 BL-4（invalid uv key）皆正確完成計畫目標，zero-risk 暖身批次驗證通過。
唯一值得追蹤的是 BL-4 同 repo 下 `python/pyproject.toml` 的同類遺漏（Important #1），
建議在後續 commit 補清。

**裁決：APPROVED WITH NOTES**
