# 設計:bars 冪等續傳 + gateway 真實探活

日期:2026-06-10
狀態:已審核通過
前置:`docs/plans/2026-06-09-shioaji-data-cli.md`(shioaji-data CLI 本體)

## 問題

QA 後發現兩顆設計雷,外加代碼盤點時挖出第三顆:

1. **bars 非冪等、假 complete**:`catalog.write_data` 純 append,重跑同檔重複寫入;
   `fetch_stock_bars` 連續 3 次錯誤 break 但只回傳 `int`,`fetch_bars_one`
   無條件回報 `complete`(`fetch.py`)。中途截斷不會在批次報告露出。
2. **隱形月份洞**:`bars.py` 迴圈裡錯誤 1~2 次後 `continue` 到**下一個月 chunk**
   ——整月被跳過、資料中段缺月,連 partial 都不算。inspect 的 >5 天 gap
   偵測是唯一安全網。
3. **Gateway 假活**:`cli.py` `_check_gateway` 只 GET `/api/health` 且不驗
   response(連 status code 都不看)。已知 gotcha:登入旗標謊報,後端 Solace
   session 靜默失效時 health 仍回正常,批次會空跑。

對照:ticks 側 `fetch_ticks_one` 已有 `stopped_early → partial + last_date`
機制;bars 是唯一沒跟上的。

## 決策(已逐項對齊)

| 分岔 | 選擇 |
|------|------|
| 續傳策略 | 自動續傳:retry-then-break + catalog-aware resume + partial 回報 |
| 盤中截尾 | `_effective_end`:台北 15:00 前一律 cap 到昨日,顯式 `--end` 也 cap |
| 探活 | 固定 2330 kbar 探針(近 14 日),僅 fetch 子命令,失敗 exit 1 |

## 設計

### 1. 連續前綴不變式(`bars.py`)

`fetch_stock_bars` 錯誤語義從「跳月留洞」改為「同 chunk 重試 3 次、仍敗即停」:

- 回傳型別 `int` → dataclass `BarsFetchOutcome(n_bars, last_bar_date, truncated)`
- 月 chunk 內層重試迴圈(3 attempts,不跨 chunk 累計);3 次皆敗 →
  `truncated=True` 立即 return,絕不 `continue` 跳到下一月
- 空月(上市前/停牌)不是錯誤,照常前進——「前綴連續」指不會有**因錯誤
  產生的**洞
- `last_bar_date` 取自實際寫入的最後一根 bar 的日期(非 chunk_end)

效果:catalog 中 `[start, last_bar_date]` 保證無錯誤洞 → 自動續傳的數學
前提成立。

### 2. Catalog-aware 自動續傳(`bars.py` + `fetch.py`)

新增 `last_bar_date_in_catalog(catalog, bar_type) -> date | None`:polars lazy
scan 直接掃 `catalog/data/bar/{bar_type}/` 的 `ts_event` max(同 `inspect.py`
讀法,不經 NT query 全載)。

`fetch_bars_one` 開跑順序重排:

```
1. resume 檢查: last = last_bar_date_in_catalog(...)
   if last: start = max(start, last + 1)
   if start > end: return complete(0 new)   # 秒過,零 API 呼叫、零副作用寫入
2. probe → 3. instrument def 寫入 → 4. fetch_stock_bars
5. truncated → status="partial" + last_date;否則 complete
```

第 1 步在 probe **之前**:已完成的檔連 probe 與 instrument def 重寫都省掉
(instrument def 重寫本身也是 append 重複,順手堵掉)。

### 3. 盤中截尾防護(`cli.py`,bars 與 ticks 共用)

盤中(09:00–13:30)跑 fetch,最後一天只有部分 bars/ticks,但 catalog
`last_date = today` → 下次自動續傳 `start = last+1` **永久跳過當天下午**,
且 inspect 日粒度 gap 偵測抓不到。

`_effective_end(end) -> date`:`end >= 台北今日` 且台北時間 < 15:00 → cap 到
昨日 + 印一行說明。顯式 `--end` 同樣被 cap,不留破口。15:00 留盤後結算緩衝
(13:30 收盤 + 證交所資料定稿)。

ticks 也吃同一個 cap——`fetch_ticks_one` 是同樣的日粒度 resume
(`last_date + 1`),盤中跑一樣產生永久截尾日。同一類問題一次出去。

### 4. Gateway 真實探活(`cli.py`)

`_check_gateway` 升級為兩段:

1. `GET /api/health` + `raise_for_status()`(補上 HTTP 層驗證)
2. `GET /api/market/kbars?code=2330&start=今-14日&end=今日` → `ts` 非空才放行
   (台積電必有資料,假陰性趨近零;成本僅數 KB 配額)

新增 `GatewayStaleError`,`main` 分流兩種 exit 1 訊息:「不可達(connect)」
vs「session 假活——health OK 但 2330 探針無資料,需重新登入」。僅三個 fetch
子命令執行,`inspect` 離線不探。

否決的替代方案:
- 探批次首檔:首檔可能本來就 no_data(下市/新上市)→ 假陰性誤殺整批
- 修 server 端 `/api/health` 加 Solace 探活:根治所有使用者,但動到 server
  + 重建 container,超出本次範圍(可列後續)

### 5. 報表與測試

- `format_batch_report`:bars partial 的 resume hint 簡化為「重跑同一條命令
  即續傳」(自動續傳後 `--start` 提示對 bars 已多餘;ticks 保留現有 hint)
- 測試(`test_data_cli.py`):
  1. 同 chunk 重試 3 次後 break、不跳月(truncated=True、後續 chunk 未被請求)
  2. resume 推進(start 被推到 last+1)/ 完成檔秒過(零 API 呼叫)
  3. `_effective_end` 各時段(注入 clock,不碰真實時間)
  4. 探針空 `ts` → exit 1(stale 訊息);health 非 2xx → exit 1
- README 同步補續傳語義

## 核心取捨

把冪等性從「操作紀律」搬到「資料不變式」:retry-then-break 讓 catalog 永遠
是連續前綴,於是「catalog 最後一根 bar」這個單一事實源就足以推導 resume 點
——不需要 sidecar 狀態檔、不需要 manifest,catalog 本身就是 checkpoint。
