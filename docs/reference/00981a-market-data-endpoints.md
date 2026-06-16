# 00981A 市場資料端點參考(官方來源 spike 驗證)

Updated: 2026-06-16

本文件是 [`docs/plans/2026-06-16-00981a-constituents-catalog.md`](../plans/2026-06-16-00981a-constituents-catalog.md)
Task 1(SPIKE / DECISION GATE)的交付物。針對設計風險 R1(point-in-time 發行股數可重建)
與 R2(全市場歷史日收盤可取得 [2025-05-27 → 今天])做了實證,並用 `uv run --no-project --with httpx`
逐一驗證三類端點皆回傳真實資料。

---

## ⛳ 裁決:**GO**

三類端點家族全部以機器可讀方式取得真實資料;探針 (b) 玉山金(2884)的窗內配股事件可由
「現行股數 − 事件股數變動」往回重建出 pre-event 股數,並與獨立來源(揭露之現金股利總額)
**對齊在 0.11% 內**(rounding/庫藏股容差內)。R1 與 R2 皆成立。

| 風險 | 結論 |
|---|---|
| R1 point-in-time 股數可重建 | ✅ 成立(現行股數錨點 + MOPS 配股股數 + TWT49U 除權日) |
| R2 全市場歷史日收盤可得 | ✅ 成立(TWSE STOCK_DAY / TPEx tradingStock,皆回溯至 2025-05、前進至今日) |

---

## 家族 1 — 現行發行股數(錨點)

每市場一次 bulk GET,落地快取後逐碼查表。**只含普通股**(ETF/權證/特別股本就不在這兩個 dataset 內)。

### 1a. 上市 TWSE

- URL:`https://openapi.twse.com.tw/v1/opendata/t187ap03_L`
- Method:`GET`,`accept: application/json`,無參數
- 回傳:JSON array,1090 筆(其中 4 碼純數字普通股 = **1084**;6 碼 `91xxxx` 為外國 TDR/原股)
- 關鍵欄位(EXACT JSON keys):

  | 欄位 | 鍵名 | 範例(2330) |
  |---|---|---|
  | 公司代號 | `公司代號` | `2330` |
  | 公司簡稱 | `公司簡稱` | `台積電` |
  | **已發行普通股數** | `已發行普通股數或TDR原股發行股數` | `25932370067` |
  | 實收資本額(元) | `實收資本額` | `259323700670` |
  | 普通股每股面額 | `普通股每股面額` | `新台幣 10.0000元`(需 strip) |
  | 產業別 | `產業別` | `24`(33 種) |

  主鍵用 `已發行普通股數或TDR原股發行股數`;若該欄缺值可由 `實收資本額 / 10`(par=10)推導。
  此 dataset 無顯式「證券類別」欄,但它本身即上市公司普通股名錄,故普通股篩選靠「碼格式為 4 碼純數字」+ 排除 TDR。

### 1b. 上櫃 TPEx

- URL:`https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`
- Method:`GET`,`accept: application/json`,無參數(建議帶 `User-Agent`)
- 回傳:JSON array,889 筆(全為 4 碼純數字普通股)
- 關鍵欄位(EXACT JSON keys,英文):

  | 欄位 | 鍵名 | 範例(8069 元太) |
  |---|---|---|
  | 公司代號 | `SecuritiesCompanyCode` | `8069` |
  | 公司簡稱 | `CompanyAbbreviation` | `元太` |
  | **已發行普通股數** | `IssueShares` | `1152370359` |
  | 實收資本額(元) | `Paidin.Capital.NTDollars` | `11523703590` |
  | 普通股每股面額 | `ParValueOfCommonStock` | `新台幣 10.0000元` |
  | 產業別 | `SecuritiesIndustryCode` | `26` |

  探針驗證:8069 元太 = 1,152,370,359 股;5483 中美晶 = 641,221,651 股(皆對得上公開值)。

---

## 家族 2 — 歷史日收盤(逐碼逐月)

`openapi.twse.com.tw/v1` 只供前一日/月資料,故歷史收盤必須走官網的個股月成交 endpoint。
皆回溯至 2025-05、前進至 2026-06-16,coverage 充足。日期為**民國年**(ROC),需轉換。

### 2a. 上市 TWSE — STOCK_DAY

- URL:`https://www.twse.com.tw/exchangeReport/STOCK_DAY`
  (現行官網 RWD 路徑亦可:`https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY`)
- Method:`GET`
- 參數:`response=json`、`date=YYYYMMDD`(該月任一日,通常用月初 `YYYYMM01`)、`stockNo=XXXX`
- 回傳:`{"stat":"OK", "fields":[...], "data":[[...],...]}`,一個月一個 request
- `fields`:`['日期','成交股數','成交金額','開盤價','最高價','最低價','收盤價','漲跌價差','成交筆數','註記']`
- **收盤價 = `data[i][6]`**;日期 = `data[i][0]`(格式 `114/05/02` = 2025-05-02)
- 探針驗證(2330):`date=20250501` → 20 筆(114/05/02 收 950.00 … 114/05/29 收 967.00);
  `date=20260601` → 12 筆(115/06/01 … 115/06/16 收 2400.00)。
- 注意:無資料月份 `stat != "OK"`(回 `很抱歉...`),需檢查 `stat` 後再解析。

### 2b. 上櫃 TPEx — 個股日成交資訊(tradingStock)

- URL:`https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock`
- Method:`GET`
- 參數:`code=XXXX`、`date=YYYY/MM/01`(西元年、斜線、月初)、`id=`(空)、`response=json`
- 回傳:`{"tables":[{"title":..., "date":..., "fields":[...], "data":[[...],...]}]}`
- `fields`:`['日 期','成交張數','成交仟元','開盤','最高','最低','收盤','漲跌','筆數']`
- **收盤 = `tables[0]["data"][i][6]`**;日期 = `[i][0]`(`114/05/02`,民國年)
- 探針驗證(8069 元太):`date=2025/05/01` → 20 筆(114/05/02 收 232.50);
  `date=2026/06/01` → 12 筆(115/06/16 收 194.50)。
- 注意:舊版 `web/stock/aftertrading/.../st43_result.php` **已失效**(302),勿用。
- 量綱:TPEx 成交量單位為「張」(= 1000 股);本管線只取收盤價做市值排名,量綱不影響。

---

## 家族 3 — 股本變動事件(MOPS,用於重建 point-in-time 股數)

窗內(2025-05-27 → 今天)各股的**股數變動**事件由兩個官方來源組合定位:
**除權息日**(effective_date)來自 TWSE TWT49U;**配股股數變動**(shares_delta)來自 MOPS 股利分派彙總表。

### 3a. 事件日 + 篩選(TWSE 除權除息計算結果表 TWT49U)

- URL:`https://www.twse.com.tw/rwd/zh/exRight/TWT49U`
- Method:`GET`
- 參數:`startDate=YYYYMMDD`、`endDate=YYYYMMDD`、`response=json`(支援整段日期區間)
- 回傳:`{"stat":"OK","fields":[...],"data":[[...]]}`
- 關鍵欄位(by index):`[0]=資料日期(除權息日,民國年)`、`[1]=股票代號`、`[2]=名稱`、
  `[3]=除權息前收盤價`、`[4]=參考價`、`[5]=權值+息值`、**`[6]=權/息`**、`[11]=詳細資料(code,YYYYMMDD)`
- **`權/息` 欄區分事件性質**:含「權」= 配股(股數會變)、純「息」= 現金股利(股數不變)。
  本管線只需處理含「權」(權 / 權息)的事件。
- 探針驗證:窗內 [2025-05-27, 2026-06-16] 共 1558 筆;其中 4 碼普通股且含「權」者 = **130 檔**。
- 用途:① 取得每個配股事件的**除權日**(= shares_delta 的 effective_date);② 篩出哪些碼有股數變動。

### 3b. 配股股數變動量(MOPS 股利分派情形彙總表 t05st09sub)

- URL:`https://mopsov.twse.com.tw/server-java/t05st09sub`
- Method:**`POST`**(`Content-Type: application/x-www-form-urlencoded`)
- 參數:`TYPEK`(`sii` 上市 / `otc` 上櫃 / `rotc` 興櫃)、`YEAR`(民國年,股利**所屬**年度,
  例如 2025 年中除權息 → `YEAR=113`)、`step=1`、`firstin=1`
- 回傳:**big5 編碼 HTML 全市場彙總表**(約 2 MB),需 `.decode('big5')` 後解析 `<table>`
- 每列關鍵欄位(2884 玉山金 113 年度,by cell index):

  | cell | 內容 | 值 |
  |---|---|---|
  | 0 | 代號 - 簡稱 | `2884 - 玉山金` |
  | 11 | 現金股利(元/股) | `1.20000000` |
  | **14** | **盈餘轉增資配股(元/股)** | `0.10000000` |
  | **16** | **股東配股總股數(股)= shares_delta** | `160,200,000` |

- 取得方式(機器可讀):每市場 × 每 ROC 年度 一次 bulk POST → 解析 HTML 表格 → 逐碼查 cell[16]。
- 取得**股數變動量** `shares_delta = cell[16]`;搭配 3a 的除權日得到 `(code, effective_date, shares_delta)`。
- 備註:新版 MOPS SPA(`mops.twse.com.tw/mops/#/web/t05st09_new`)前端最終就是打這支
  `mopsov.twse.com.tw/server-java/t05st09sub`(從 JS bundle `assets/t05st09_new.js` 確認);
  `mops.twse.com.tw/mops/api/*` 的 JSON API 對「股利分派/股本形成」回 302,不可直接用。
  另:`t187ap45_L`(OpenAPI 股利分派)只反映**最新一期**宣告值(玉山金現只剩 114 年度、配股為 0),
  **非 point-in-time 歷史**,不可作為窗內事件來源——必須用 t05st09sub 的指定 YEAR。

---

## 探針 (b) 重建驗證 — 玉山金 2884(R1 證明)

| 項目 | 值 | 來源 |
|---|---|---|
| 窗內事件 | 除權息 ex-date **2025-07-22**,類型「權息」 | TWT49U |
| 配股股數變動(shares_delta) | **+160,200,000 股** | MOPS t05st09sub cell[16],YEAR=113 |
| 現行已發行普通股數(錨點) | 16,174,000,000 股 | t187ap03_L |
| **重建 pre-event 股數** | 16,174,000,000 − 160,200,000 = **16,013,800,000** | 往回滾算 |
| 獨立對照值(現金股利反推基數) | 19,194,960,000 元 ÷ 1.2 元/股 = **15,995,800,000** | t05st09sub 揭露之現金股利總額 ÷ 每股現金股利 |
| 差異 | 18,000,000 股 = **0.11%** | — |

**對齊?是。** 0.11% 殘差來自:現金股利基數是**配息基準日已發行且具配息資格股數**(排除庫藏股),
而重建用的是發行股數錨點減配股增量;對 160 億股規模的金控,0.11% 屬 rounding/庫藏股容差內。
另一交叉驗證:由配股率 0.1元/10元par = 0.01 反推 pre-event 基數 = 160,200,000 / 0.01 = 16,020,000,000,
與重建值 16,013,800,000 差 0.04%,亦在容差內。窗內僅此**一個**事件(TWT49U 驗證),重建單調、無多事件糾纏。

---

## 全市場請求量 + 禮貌 rate-limit 估算

- 普通股檔數:TWSE 4 碼普通股 = **1084**、TPEx = **889** → 合計 **1973** 檔。
- 成員窗 [2025-05-27 → 2026-06-16] 跨 **14** 個曆月。
- **家族 2 日收盤是主要請求量**(逐碼逐月):1973 × 14 ≈ **27,622** 個 GET。

  | rate-limit | 總耗時 |
  |---|---|
  | 0.50 s/req | ≈ 3.8 小時 |
  | **0.75 s/req(建議)** | ≈ 5.8 小時 |
  | 1.00 s/req | ≈ 7.7 小時 |

  探針實測:TWSE STOCK_DAY 以 0.6s 間距連發 5 檔,5/5 成功、無被擋。
  建議 `RATE_LIMIT_SECONDS = 0.75`(plan 預設 0.6 偏積極,可調)。**強制本地快取**
  (`cache/twse_tpex/close/{code}/{YYYYMM}.parquet`)以支援續傳、不重抓。
- **家族 1 現行股數**:2 個 bulk GET(TWSE + TPEx),整批一次,可數日刷新一次。
- **家族 3 事件**:TWT49U 1 個區間 GET;MOPS t05st09sub 每市場 × 每 ROC 年度(113、114)
  各 1 個 POST ≈ 4 個 bulk POST。量極小。
- 全部為**公開資料,不吃 Shioaji 配額**。

---

## 給 Task 2 實作者的提醒

1. 日期一律民國年 ↔ 西元年轉換(`ROC_year + 1911`);`STOCK_DAY`/`tradingStock` 都回民國年。
2. 解析前先檢查 TWSE `stat == "OK"`、TPEx `tables[0].data` 非空;無資料月份需略過不報錯。
3. MOPS t05st09sub 是 **big5 HTML**,須 `resp.content.decode('big5')` 再用 table parser。
4. TPEx 收盤量綱為「張」,但本管線只取收盤價,量綱不影響市值排名。
5. 普通股篩選:碼為 4 碼純數字 + 排除 TDR(`91xxxx`);ETF/權證/特別股不在這兩個 t187ap03 dataset。
6. shares_delta 的 effective_date 用 TWT49U 的除權日(`資料日期`),非董事會日、非股東會日。
