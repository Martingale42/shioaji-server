# Design — `shioaji-data` CLI(把 scripts 資料管線包成單一指令工具)

> 日期:2026-06-09 ·狀態:✅ 設計定案,待 writing-plans 開實作計畫
> 來源:`/scripts` cleanup(見 BACKLOG BL-7)後的後續——把 Paradigm B 下載/檢查管線從「逐支 `python -m scripts.X`」收斂成一個 `shioaji-data` CLI。

---

## 1. 動機與範圍

cleanup(BL-7)後,`scripts/` 頂層剩 Paradigm B 的下載/檢查管線(`bars`/`client`/`instruments`/`fetch_single`/`fetch_single_ticks`/`inspect_catalog`)。每次抓資料要分別跑 `uv run python -m scripts.fetch_single_ticks --code ...`,且只能逐檔(`fetch_single 到死`)。

**目標**:單一 `shioaji-data` CLI 統一日常下載/檢查,並支援**一次抓一串 ticker 的平行批次**。

### 定案決策(brainstorming 2026-06-09)

| 決策 | 選擇 | 理由 |
|---|---|---|
| 同 repo vs 新 repo | **同 repo** | CLI 依賴 gateway(本 repo)+ sinopac fork wheel(本 repo `[tool.uv.sources]` 釘死);新 repo 只會複製依賴閉包、零收益。定位=本機開發便利,consumer 只有作者本人 |
| 指令範圍 | **只包日常管線**(fetch-bars/fetch-ticks/instrument-def/inspect) | 一次性維護鏈(restamp/regen/verify)危險,維持獨立 `python -m scripts.maintenance.X`,不混進日常入口 |
| CLI 框架 | **argparse**(stdlib) | 零新依賴(本 repo 依賴精簡);現有 4 支腳本已用 argparse,遷移機械化 |
| 打包位置 | **提升進套件** `src/shioaji_server/data/` | `[project.scripts]` console script 只有當目標模組被安裝進套件才能解析;`scripts/` 是未安裝的鬆散 package。提升後 `shioaji-data` 隨 wheel 安裝,任何 cwd 可跨 |
| 單檔 vs 批次 | **共用指令、flag 區分**(`--code` XOR `--codes`/`--codes-file`) | 單一程式碼路徑,單檔=併發 1 的退化批次 |
| 批次併發 | **asyncio**(非 threading) | 工作 I/O-bound;client 已是 `httpx.AsyncClient`;共享配額閘門在單執行緒 asyncio 免鎖無 race |
| 配額×平行 | **共用配額閘門 + 有界併發** | 平行 ticks 會數倍燒每日 500MB 配額(resume_00631L 即單檔 tick 撞配額);集中節流 usage 查詢 + 單一 tripped flag |

### 非目標(YAGNI)

- 不做 typer/click(UX 雖好但加依賴,本機 CLI 不值)。
- 不把維護鏈包進 CLI。
- 不做 PyPI 發佈、不做 shell 補全。
- 不保留 `python -m scripts.fetch_single*` 向後相容(無外部呼叫者;resume cron 已刪)。

---

## 2. 架構(關注點分離:解析 ↔ 邏輯)

```
src/shioaji_server/data/          ← Paradigm B 提升進套件
├── __init__.py
├── cli.py            argparse 4 子指令 → console_scripts main()  (唯一 CLI 層)
├── bars.py           bar 引擎(原樣搬入,內部改相對 import)
├── client.py         ShioajiClient async httpx(原樣搬入)
├── instruments.py    load_instrument(原樣搬入)
├── fetch.py          ← fetch_single + fetch_single_ticks 合併重構:
│                       純 helper + fetch_*_one() + write_instrument_def_one()
│                       + QuotaGate + run_batch() orchestrator
└── inspect.py        ← inspect_catalog.py(剝 argparse,留 inspect_catalog())

scripts/maintenance/   ← 一次性維護鏈留原地(regen 內部 import 改指向套件)
```

**核心原則**:`cli.py` 只負責 argparse + 分發;所有實際邏輯是 `fetch.py`/`inspect.py`/`instruments.py` 的純函式。CLI 可獨立測試、邏輯可被別的 Python 程式 import 復用,不是 argparse 套 argparse。

---

## 3. 指令面

```
shioaji-data [--catalog ./catalog] [--gateway-url http://localhost:8000] <cmd>

  fetch-bars      (--code 0050 | --codes 0050,00631L,2330 | --codes-file tickers.txt)
                  [--start 2020-03-02] [--end <today>] [--concurrency 4]
  fetch-ticks     (--code ... | --codes ... | --codes-file ...)
                  [--start 2020-03-02] [--end <today>] [--min-remaining-mb 50] [--concurrency 4]
  instrument-def  (--code ... | --codes ... | --codes-file ...)   # 只寫 instrument def
  inspect                                                          # equity 定義 + bar gap/筆數體檢
```

- `--catalog` / `--gateway-url`:全域旗標(argparse parent parser,4 子指令共用,預設同現狀)。
- `--code` **XOR** `--codes`/`--codes-file`:argparse mutually-exclusive group,擇一必填。給 `--codes` → 走平行。
- `fetch-bars`/`fetch-ticks` 行為與現有腳本一致(quota-aware、`--start` resume、副產品寫 instrument def 全保留)。
- `instrument-def`:新拆出的小指令(現在只能當 fetch 副產品取得),`load_instrument` + `write_data`。

---

## 4. 批次 + 平行設計

### 4.1 平行模型(asyncio,非 thread/loop)

- 一個共用 `ShioajiClient`(內部 `Semaphore(10)` 限制對 gateway 的請求併發)。
- 外層 ticker 級 `asyncio.Semaphore(--concurrency)`(預設 4)→ `asyncio.gather(*workers, return_exceptions=True)`,逐 ticker 錯誤隔離。
- 預設 4 併發 × 逐日請求 ≈ 最多 4 個併發 gateway 請求,遠低於 Sinopac 50 req/5s 與 client 10 上限——rate-limit 安全,真正瓶頸是配額。

**為何 asyncio 而非 threading**:工作 I/O-bound(wall-clock 幾乎全在等網路);client 已是 async(threads 要嘛每 thread 一個 event loop、要嘛重寫成同步,逆紋);GIL 下 threads 對 I/O 雖會重疊但開銷更大;**關鍵**——共享配額閘門是跨 worker 可變狀態,單執行緒 asyncio 的 coroutine 只在 `await` 點交棒、狀態變更原子、免鎖無 race,threads 則每次存取要 `threading.Lock` 且有 check-then-use race。

### 4.2 共用配額閘門 `QuotaGate`

- 集中 + 節流 `/api/account/usage` 查詢(4 個 worker 不各自狂打 usage,快取數秒共用一份)+ 單一 `tripped` flag。
- `remaining_mb < --min-remaining-mb` → 觸發 tripped → worker 停派新日/新 ticker、在途收尾、各自記錄最後完成日。
- 把「配額」從 per-worker 狀態升維成 batch 級共享資源——平行化真正的難點,不是 `gather` 本身。

### 4.3 收尾報告 + resume

- 每 ticker 回傳 `TickerResult(code, status, last_date, n_written, error)`;`status ∈ {complete, partial, no_data, failed}`。
- CLI 印彙總表 + **逐 ticker** resume 提示:
  ```
  ✓ 0050  ✓ 00631L  ⚠ 2330 → resume: shioaji-data fetch-ticks --code 2330 --start 2024-03-03
  ```
- **逐 ticker** 而非單一合併指令是刻意的:各檔 `last_date` 不同,合併 `--start` 會對抓得較遠的檔重抓 → catalog 寫入重疊。逐檔 resume 才正確。

### 4.4 `fetch.py` 內部結構

- 純 helper(原樣搬自 `fetch_single_ticks`):`_tick_type_to_aggressor`、`_ns_to_trade_id`、`ticks_to_trade_ticks`、`trading_days`。
- `QuotaGate`(新):節流 usage 查詢 + tripped flag。
- `fetch_bars_one(client, code, start, end, catalog, gateway_url) -> TickerResult`(自 `fetch_single.main` 抽,去 argparse)。
- `fetch_ticks_one(client, code, start, end, catalog, gate) -> TickerResult`(自 `fetch_single_ticks` 抽,用共享 `QuotaGate` 取代 per-call `check_quota`)。
- `write_instrument_def_one(code, catalog, gateway_url) -> TickerResult`。
- `run_batch(codes, concurrency, per_ticker_coro) -> list[TickerResult]`:asyncio.gather + Semaphore orchestrator,4 個 fetch 指令共用。
- `TickerResult` dataclass。

---

## 5. 遷移機制

### 5.1 搬移(全在 repo 內,Makefile/README 無引用)

| 動作 | 細節 |
|---|---|
| 純搬移(git mv) | `bars.py` `client.py` `instruments.py` → `data/`;內部互引改相對 import(`from .client import ShioajiClient`) |
| 搬移+剝 argparse | `inspect_catalog.py` → `data/inspect.py`(留 `inspect_catalog()`,`main()` 移進 cli) |
| 合併+重構(真工作) | `fetch_single.py` + `fetch_single_ticks.py` → `data/fetch.py`(見 §4.4) |
| 新建 | `data/__init__.py`、`data/cli.py` |
| pyproject | 加 `[project.scripts] shioaji-data = "shioaji_server.data.cli:main"`;`data/` 子套件隨現有 `src/shioaji_server` 自動進 wheel,無需改 build 設定 |

### 5.2 import 連動(3 處非測試,否則 import error)

- `scripts/maintenance/regen_catalog_instruments.py:39`(+docstring L7)→ `from shioaji_server.data.instruments import load_instrument`(維護鏈改吃已安裝套件,比鬆散 sibling 更穩)。
- `tests/test_trade_id.py:11` → `from shioaji_server.data.fetch import _ns_to_trade_id`。
- `tests/test_instrument_provider_path.py`(L89/207/209)→ import 路徑改 `shioaji_server.data.instruments`,grep 守衛 `test_legacy_hardcoded_builders_are_gone` 改讀 `src/shioaji_server/data/fetch.py`。

pytest `pythonpath=["."]` 保留(`scripts.maintenance.*` 測試仍靠它);`shioaji_server.data.*` 走 src-layout 已安裝套件——兩個 import 根並存。

---

## 6. 錯誤處理

- **Gateway 不通**:`cli.main()` 包 try,pre-flight 打 `/api/health`;`httpx.ConnectError` → 印 `gateway not reachable at {url} — container up & logged in? (curl {url}/api/health)` + exit 1。
- **逐 ticker 失敗**:`gather(return_exceptions=True)` → 該檔 `TickerResult(status=failed)`,batch 續跑,彙總列出。
- **配額 tripped**:非錯誤——優雅停 + 逐檔 resume 提示。
- **No-data**(probe false)→ `status=no_data`,非失敗。
- **exit code**:全完成 `0`;有 failed/incomplete `2`(cron/script 可偵測部分完成);gateway 不通 `1`。

---

## 7. 測試

| 測試 | 動作 |
|---|---|
| `test_trade_id.py` | 改 import 路徑 |
| `test_instrument_provider_path.py` | 改 import + grep 守衛指向 `data/fetch.py` |
| `test_restamp_metadata.py` / `test_regen_catalog_instruments.py` | 不動(維護鏈原地;regen 內部 import 改了但測試仍綠) |
| 新 `test_data_cli.py` | parser 有 4 子指令;`--code` XOR `--codes` 互斥(同給 → SystemExit);`--codes-file` 解析;dispatch 路由(monkeypatch async driver)。全離線 |
| 新 `test_batch_fetch.py` | `run_batch` 尊重 concurrency 上界 + 逐 ticker 隔離;`QuotaGate` tripped 後 `ok()` 恆 False + usage 查詢節流(視窗內只查一次)。用 fake client |

驗收:`uv run ruff check` 全綠;`uv run pytest` 全綠(含新 CLI/batch 測試);`uv run shioaji-data inspect` 在任意 cwd 可跑。

---

## 8. 後續(本設計外)

- 期貨/選擇權 instrument-def(現 instruments.py 已走 same-source provider,理論上支援,待 live 驗證——併 BL-5)。
- 若量測到 `catalog.write_data()` 阻塞 event loop 成瓶頸,改 `await asyncio.to_thread(catalog.write_data, ...)`(單點 hybrid,非整體改 threading)。
