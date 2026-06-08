# Batch 2 — 時區統一（§0 / S1）程式碼審查報告

- 日期：2026-06-08
- 審查範圍：commits `bf28a96`（Task 2）、`7961596`（Task 4）、`ef011d1`（Task 5）
- 分支：`main`
- 參考文件：`docs/plans/2026-06-08-timezone-unification.md`、`docs/AUDIT.md` §0 / §1（S1/S2）

## 裁決：APPROVED WITH NOTES

資料正確性紅線（−8h 方向、磁碟資料不被二次位移、檔案無遺失、WS 路徑未受污染）**全部通過**。
唯一阻擋 gate 「dry-run 報告 0 instrument」驗收條件的是一個 **報告層級的冪等缺陷**（dry-run 繞過 marker 守衛），
不影響磁碟資料安全，但會誤導操作者。降為 Important，建議修但不阻擋合併。

---

## 重新驗證的關鍵證據

### 1. −8h 方向正確（資料正確性紅線）— 通過
獨立重新解碼已遷移的 catalog 檔案：

```
catalog/data/trade_tick/0050.SINOPAC/
  2020-03-02T01-00-01-187000000Z_2020-03-02T06-30-00-000000000Z.parquet
  first ts_event = 1583110801187000000 -> 2020-03-02T01:00:01.187000+00:00
  last  ts_event                         -> 2020-03-02T06:30:00+00:00
  first ts_init                          -> 2020-03-02T01:00:01.187000+00:00
  rows = 2187
```

台股開盤 TW 09:00 被 SDK 當 UTC 存成 09:00 UTC，−8h 後正確落在 **真 UTC 01:00**。
方向正確，且資料**確非**原生真 UTC（原生真 UTC 的台股盤中不會是 09:00 UTC）。執行者的 gate 判定成立。

### 2. ts_init 與 ts_event 同步位移 — 通過且正確
- 遷移後首列 `ts_init` 與 `ts_event` 皆 = 01:00:01.187 UTC（非殘留 09:00）。
- 對這批歷史下載檔，`ts_init` 由下載腳本寫入時即等於同一筆 SDK `ts`（非真實 ingestion 牆鐘時間），
  故兩欄同幅 −8h 是正確的。資料佐證：首列 `ts_init == ts_event`。
- `migrate_ts_to_utc.py:38` `SHIFT_COLS = ("ts_event", "ts_init")`，`:77-79` 對兩欄同時 `- TW_UTC_OFFSET_NS`。

### 3. 檔名重鍵 — 通過
- `migrate_ts_to_utc.py:86` 以 NT 自身 `_timestamps_to_filename(new_start, new_end)` 重建檔名，
  `new_start`/`new_end` 取自**位移後** `ts_event` 的 min/max（`:83-85`），單一真相來源、無命名漂移。
- 觀察到的檔名 `2020-03-02T01-00-01-...Z_..._06-30-00-...Z.parquet` 與 NT 規格一致。
- 檔數守恆：`bar` + `trade_tick` 下 **3193** 檔，與 marker 紀錄 `files=3193` 一致；兩次測試重跑後仍為 3193，無遺失。

### 4. WS 路徑未受污染 — 通過
- `ws/manager.py` / `client.py` 四個 quote callback 全部沿用字串 `str(tick.datetime)` /
  `str(bidask.datetime)`（`client.py:338,348,363,373`）。
- `ws/` 目錄內 grep 不到任何 `28_800` / `_to_utc_ns` / `TW_UTC_OFFSET` 整數位移。
- HTTP 的 −8h 修正僅作用在 `routes/market_data.py` 的整數 `ts`，未碰 WS 字串路徑。三方對齊邏輯成立。

### 5. 測試品質 — 通過
- `test_timezone.py`：以 `datetime` 解碼鎖定方向（TW 09:00 → 真 UTC 01:00，`hour == 1` 守衛），
  非套套邏輯 `x == x - const`；錯號回歸會落到 17:00 被抓出。另含順序/間距保留、空 list no-op。
- `test_trade_id.py`：證明同微秒不同 ns（`base_ns` vs `base_ns + 123`）→ 不同 ID；
  並驗證後綴為純納秒整數、不同 code 不撞 ID。

---

## Findings

### Important
- **[scripts/migrate_ts_to_utc.py:130] dry-run 繞過冪等守衛，報告層誤導。**
  守衛條件 `if marker.exists() and not force and not dry_run:` 在 `--dry-run` 時被短路，
  導致**已遷移**的 catalog 再跑 dry-run 仍輸出 `files_shifted=3193`，且把已是真 UTC 的
  `2026-05-12T01:02` 列「建議」二次位移到 `2026-05-11T17:02`（等效 −16h）。

  - **gate 影響**：直接違反驗收條件「dry-run 報告 0 instrument（marker 存在）」。實測輸出
    `DRY-RUN complete: instruments=4 files_scanned=3193 files_shifted=3193`，**非 0**。
  - **資料安全**：磁碟資料**未受損**。真正的（非 dry-run）二次執行被正確攔截
    （`MIGRATE complete: instruments=0 files_shifted=0`，marker 未被覆寫，守衛在任何 write 之前 return），
    重跑後 0050 首筆仍為 01:00:01.187 UTC、檔數仍 3193。故僅為報告誤導，非資料損毀。
  - **建議修法**：dry-run 也應檢查 marker——marker 存在時輸出「已遷移、0 待處理」，
    或提供 `--force` 才在 dry-run 顯示重複位移預覽。最小改動：將 `and not dry_run` 移出守衛條件，
    在 dry-run 路徑下印 no-op 訊息後 return。

### Minor
- **[scripts/migrate_ts_to_utc.py:144-149] marker 寫入未受 marker 既存保護。**
  目前因守衛在 marker 存在時提前 return，不會走到寫入；但若未來移除/重構守衛，
  marker 內的 `files=/rows=` 統計會被後續空跑覆寫為 0。屬防禦性建議，非當前缺陷。
- **[orchestrator gate 描述] WS 參考路徑名不符實。** gate 提到 `ws/client.py`，
  實際 WS callback 在 `src/shioaji_server/client.py`（非 `ws/` 內）。不影響結論——已在該檔確認四處 `str(...datetime)` 未受位移。

### 預先存在（不阻擋本批）
- `scripts/fetch_single.py:18,19` 5 個 ruff `F401` 死 import（`Venue`、`Currency` 等）。
  該檔最後修改於 `c7e47ed`，**不在** Batch 2 任一 commit，屬範圍外。本批 5 個變更檔 ruff 全清。

---

## 驗證輸出

### pytest（24 passed）
```
tests/test_timezone.py::test_offset_constant_is_8_hours PASSED
tests/test_timezone.py::test_tw_0900_as_utc_decodes_to_true_utc_0100 PASSED
tests/test_timezone.py::test_shift_magnitude_and_order_preserved PASSED
tests/test_timezone.py::test_empty_list_is_noop PASSED
tests/test_trade_id.py::test_same_microsecond_different_ns_distinct_ids PASSED
tests/test_trade_id.py::test_id_is_nanosecond_integer_suffix PASSED
tests/test_trade_id.py::test_distinct_codes_distinct_ids PASSED
======================== 24 passed, 1 warning in 1.16s =========================
```

### ruff
```
# 變更檔（5 個）：All checks passed!
# 全量掃描：Found 5 errors —— 全在 scripts/fetch_single.py（預先存在死 import，範圍外）
```

### 遷移冪等 dry-run（marker 存在）
```
DRY-RUN complete: instruments=4 files_scanned=3193 files_shifted=3193 files_renamed=0 rows_shifted=11338824
```
→ **非 0**，即上述 Important finding。對照：真正二次執行
```
WARNING Marker ... exists — catalog already migrated. Re-run is a no-op
MIGRATE complete: instruments=0 files_shifted=0 files_renamed=0 rows_shifted=0
```
→ 真實路徑正確攔截，磁碟資料與檔數（3193）守恆。

---

## 結論
三項任務的核心邏輯（HTTP −8h、catalog 遷移方向與檔名、TradeId 納秒整數）正確且有方向鎖定測試覆蓋，
磁碟資料安全無虞。**APPROVED WITH NOTES**：合併前建議修正 dry-run 冪等報告缺陷（Important），
以滿足「dry-run 報告 0」之驗收語意；該缺陷不影響已遷移資料的正確性。
