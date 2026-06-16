# 設計:00981A 成分股(top-300 superset)point-in-time 1-min bars + instrument 下載

日期:2026-06-16
狀態:已核准(設計),待實作
分支:`feat/00981a-universe`(worktree `.claude/worktrees/00981a-universe`)

## 目標

仿照 0050 成分股下載手法(見 `docs/plans/2026-06-11-0050-constituents-catalog-design.md`),把
**00981A(統一台股增長主動式 ETF,統一投信,2025-05-27 掛牌)** 的成分宇宙 1-min bars 與
instrument definition 下載進同一個 `catalog/`。口徑為 **point-in-time、survivorship-bias-correct**。

00981A 是**主動式 ETF**:經理人從市值前 300 大企業中**裁量選股**、無指數方法學、持股**每日揭露**。
因此無法像 0050 從指數方法學重建成員。改以 **point-in-time top-300 市值母體**當宇宙——
保證涵蓋 00981A 從前 300 大裁量選出的所有實際持股,且規則化、可重建。

## 與 0050 的關鍵差異

| | 0050 | 00981A |
|---|---|---|
| ETF 類型 | 被動(追 FTSE TWSE Taiwan 50) | 主動(裁量選股) |
| 成員來源 | 指數方法學 + 維基變動表(預先 curate) | **無方法學** → top-300 市值母體 superset |
| 宇宙建構 | 手動 curate 成員表 | **程式化重建**:逐日全市場市值排名 → top-300 → 聯集 |
| 宇宙規模 | 81 碼 | 估 ~330–380 碼 |
| 市值資料 | 不需(成員已知) | TWSE/TPEx OpenAPI + MOPS 發行股數 × 收盤 |

## 決策(來自 brainstorming)

| 分岔 | 選擇 |
|------|------|
| 成分來源 | top-300 市值母體(00981A 裁量持股的超集) |
| 口徑 | point-in-time 聯集,survivorship-correct |
| 市值資料 | TWSE + TPEx 官方 OpenAPI + MOPS 發行股數 × 收盤價 |
| 交易所範圍 | 上市(TWSE)+ 上櫃(TPEx) |
| 成員窗(算聯集的期間) | 00981A 生命週期 [2025-05-27 → 今天] |
| Rebalance 頻率 | **日頻**(逐交易日排名,最完整聯集) |
| 發行股數 | **真歷史股數**(非近似;重建法見 §A) |
| Bar 回補窗 | 2020-03-02 → 今天(對齊 0050,給足 lookback) |

## 架構(兩半:一半全新、一半沿用)

### A. 全新:top-300 宇宙建構(`scripts/build_00981a_universe.py`)

主動 ETF 特有的段落,0050 沒有。流程:

1. **錨點股數**:TWSE/TPEx 官方 OpenAPI 抓全市場**現行發行股數**(已發行股份總數)。
2. **股本變動事件**:MOPS 抓 [2025-05-27, today] 窗內各股**增減資/股票股利/減資/可轉債轉換**
   等事件(日期 + 股數變動)。
3. **重建日頻股數序列**:由現行股數往回套用窗內各變動事件 → 每股一條日頻
   `shares_outstanding(t)`。窗僅約 13 個月、多數股窗內零變動 → 大多為常數,只有少數有事件的
   股需調整。**這是「真歷史股數」可行的關鍵:短窗讓重建變得 tractable**。
4. **日頻收盤**:TWSE/TPEx 官方 OpenAPI 抓窗內全市場個股日收盤(個股日成交資訊);快取落地,
   不吃 Shioaji 配額。
5. **日頻市值排名**:每交易日 `市值(t) = 收盤(t) × shares(t)`,排序取 **top-300**。
6. **聯集 + 區間成員表**:窗內約 270 交易日的 top-300 取聯集 → 去重碼表;每碼在 top-300 的
   進出日 → 區間成員表(日頻精度)。

輸出進 `universe/`(進 git 版控):
- `universe/00981a_top300_constituents.txt` — 聯集去重碼表(餵 `fetch-bars --codes-file`)。
- `universe/membership_00981a_top300.csv` — 區間成員表
  (`code,name,effective_from,effective_to,confidence,note`,沿用 0050 schema;日頻精度)。
  供回測做 survivorship-correct 過濾。

### B. 沿用:bar 下載(「跟 0050 同手法」,零新下載程式)

7. 聯集餵冪等下載指令(catalog-aware resume,同 0050):
   ```bash
   uv run shioaji-data fetch-bars \
       --codes-file universe/00981a_top300_constituents.txt \
       --catalog ./catalog --start 2020-03-02 --concurrency 4
   ```
8. `uv run shioaji-data inspect --catalog ./catalog` 覆蓋率驗收。
9. `scripts/resume_00981a_fetch.sh`(clone 自 `resume_0050_fetch.sh`)做配額限制下的跨日續傳 cron。

## 資料流

```
TWSE/TPEx OpenAPI(日收盤 + 現行股數) ┐
                                      ├─→ 重建日頻市值 → 逐日 top-300 → 聯集 + 成員表(universe/)
MOPS(股本變動史)                    ┘                                         │
                                                                              ▼
                                                  fetch-bars(Shioaji gateway)→ catalog/ → inspect
```

## 執行限制:每日配額(同 0050)

Shioaji 每日 500MB 流量配額(`/api/account/usage` `limit_mb=500`)。~330–380 碼 × 6 年
1-min bars ≈ 約 7GB → 本質約 15 天工作(總位元組固定,與下載節奏無關)。續傳策略同 0050:
配額重置後重跑同一條冪等指令;開跑前 `curl /api/account/usage` 確認 `remaining_mb > 0`。
**注意:此下載會與實盤交易的每日配額競爭較久(約 15 天),需配合排程。**

## 可行性風險與緩解

- **R1 歷史股數可得性**:真歷史日頻股數依賴 MOPS 股本變動史完整。短窗讓此可行(多數股零變動),
  但需驗證 MOPS 增減資 dataset 覆蓋窗內所有事件。**第一步先做 spike**:抽幾檔有已知除權/增資的
  股(如台積電配股、金融股現增),驗證「現行股數 − 窗內事件」重建出的歷史股數對得上獨立來源。
- **R2 全市場日收盤抓取量**:TWSE/TPEx OpenAPI 逐日全市場約 270 天 × 約 1800 股,需**快取**
  (不重抓)、禮貌 rate-limit、可續傳。公開資料、不吃 Shioaji 配額。
- **R3 上市/上櫃代碼對齊與篩選**:TWSE 與 TPEx 兩套 dataset 需統一代碼格式;**只留普通股**
  (排除 ETF、權證、特別股、存託憑證、受益證券)。
- **R4 superset 完整性驗收**:用 00981A **今日實際揭露持股**對照——確認每一檔實際持股都在
  我們的 top-300 聯集內;若有漏,檢查 rebalance/篩選邏輯或放寬 top-N。

## 驗收

1. `build_00981a_universe.py` 產出聯集碼表 + 成員表;**R4 對照**:00981A 現行揭露持股 ⊆ 聯集。
2. spike 驗證重建股數正確(R1)。
3. `fetch-bars` 後 `inspect`:逐碼 first≈2020-03(或上市日)、last≈昨日;`no_data` 對照配額
   狀態排除假性缺口;真下市股(Shioaji 無歷史 kbar)明列於缺口報告。
4. 不破壞現有 0050/00631L catalog;regression 套件全綠。

## 範圍(YAGNI)

不碰 ticks、不做除權息調整、不動現有 0050/00631L 資料、不抓 00981A 實際持股(用 top-300
superset 取代)、宇宙建構只做市值排名(不做其他因子或加權)。
