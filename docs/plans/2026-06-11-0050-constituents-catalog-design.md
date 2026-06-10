# 設計:0050 成分股 point-in-time bars + instrument 下載

日期:2026-06-11
狀態:已核准,執行中(受每日配額限制,跨日續傳)

## 目標

仿照現有 0050.SINOPAC / 00631L.SINOPAC 的做法,把 **0050(FTSE TWSE Taiwan
50 Index)成分股**的 1-min bars 與 instrument definition 下載進同一個
`catalog/`。口徑為 **point-in-time**:涵蓋 2020-03-02 至今曾經是成分股的所有
股票(survivorship-bias-correct),並保留一份帶生效日的成分變動表。

## 決策

| 分岔 | 選擇 |
|------|------|
| 成分來源 | 方案 A:研究 FTSE 季度調整,使用者核對(非使用者提供權威檔) |
| 口徑 | point-in-time,聯集所有曾入選股票 + 區間成分表 |
| 日期範圍 | 對齊 0050:2020-03-02 → 今天 |
| 2026Q2 待生效股(創意3443、臻鼎-KY4958) | 納入下載(使用者拍板) |

## 資料來源與信心

- 主來源:中文維基「臺灣50指數」完整歷年變動表。
- 生效日交叉核對:FTSE Russell index notices(2025-06 / 2025-09 / 2026-03
  三筆官方確認)+ 元大/MoneyDJ/中央社/經濟日報新聞。
- 已知缺陷(已於 `membership_0050.csv` 標記):
  - **F1**:早期(尤其 8046 南電)剔除日記錄缺漏 → 區間 `effective_to`
    部分標 `low` 信心。對下載 universe 無影響(代碼仍納入)。
  - **F2**:2023Q4 維基未列 → 可能無變動。
  - **F3**:2026Q2(生效 2026-06-22)晚於今天,3443/4958 嚴格不在窗內,
    但依使用者決定納入下載;成分表以 `pending` 標記。
  - **F4**:2892 第一金在 FTSE factsheet 但維基 roster 缺 → 納入(多抓無害)。
  - **F6**:鴻勁代碼確認為 **7769**(非第一輪誤植的 6661)。

## 交付物(`universe/`,進 git 版控)

1. `universe/membership_0050.csv` — point-in-time 區間成分表(每段成員資格
   一列:`code,name,effective_from,effective_to,confidence,note`;`effective_to`
   空=現行;`2020-03-02` 起=窗起即為成員,真實納入日早於窗)。供回測做
   survivorship-correct 過濾。
2. `universe/0050_constituents.txt` — 81 碼去重聯集,餵 `--codes-file`。

## 機制(沿用現有 shioaji-data CLI,零新程式)

下載端為單一冪等指令:

```bash
uv run shioaji-data fetch-bars \
    --codes-file universe/0050_constituents.txt \
    --catalog ./catalog --start 2020-03-02 --concurrency 4
```

`fetch_bars_one` 對每檔:catalog-aware resume(讀最後一根 bar,已完成者零
API 呼叫跳過)→ probe → 寫 instrument def → 月塊抓 1-min bars(retry-then-break
保持連續前綴)。`inspect` 子指令做覆蓋率驗收。

## 執行限制:每日流量配額(關鍵)

實跑發現本帳號 Shioaji **每日流量配額 = 500MB**(`/api/account/usage`
`limit_mb=500`)。25 檔 × 6 年 1-min bars 即耗盡 500MB,之後 probe 拿到空
資料 → 假性 `no_data`。**79 檔(有資料者)× 6 年 ≈ 約 1.5GB → 本質是約 3 天
的工作**,與下載節奏無關(總位元組固定)。

**續傳策略**:每日配額重置後,重跑上面同一條指令即可。冪等性保證:
- 已完成的代碼:讀 catalog 最後一根 bar,`resume_start > end` → 零 API 跳過。
- 配額中斷而尾段留空的代碼:resume 以「最後實際 bar + 1 天」接續(空尾不是
  錯誤,但 resume 依實際 bar 推進,自動補尾),無需手動處理。
- 假性 `no_data` 的代碼:下次配額足夠時 probe 成功,正常下載。
- 開跑前以 `curl /api/account/usage` 確認 `remaining_mb > 0`。

## 驗收

`uv run shioaji-data inspect --catalog ./catalog` → 每檔 first/last/bars/gap;
逐檔確認 first≈2020-03(或上市日)、last≈昨日;`no_data` 清單對照配額狀態
排除假性缺口。真正下市股(Shioaji 無歷史 kbar)若有,明列於缺口報告。

## 範圍(YAGNI)

不碰 ticks、不做除權息調整、不動現有 0050/00631L。
