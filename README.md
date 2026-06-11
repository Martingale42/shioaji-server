# Shioaji Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Shioaji SDK 的 REST/WebSocket 閘道器，作為 NautilusTrader Sinopac adapter 的後端服務。

將永豐金 Shioaji Python SDK 包裝為 HTTP API + WebSocket 即時行情推送，讓 NautilusTrader（Rust/PyO3）可透過標準網路協定連接台灣證券/期貨市場。

## Quick Start 快速開始

### 1. 前置需求

- [Docker](https://docs.docker.com/get-docker/)（推薦），或 Python 3.13+ 搭配 [uv](https://docs.astral.sh/uv/)
- 永豐金證券帳戶 + API Key（[申請指南](https://sinotrade.github.io/tutor/prepare/token)）
- CA 憑證檔案 `Sinopac.pfx`（[下載指南](https://sinotrade.github.io/tutor/prepare/token)）
- 已簽署 [API 交易條款](https://sinotrade.github.io/tutor/prepare/terms)

### 2. 設定環境變數

```bash
cd shioaji-server
cp .env.example .env
```

編輯 `.env`，填入你的憑證：

```env
SHIOAJI_API_KEY=your_api_key
SHIOAJI_SECRET_KEY=your_secret_key
CA_PATH=/path/to/Sinopac.pfx
CA_PERSON=your_ca_password
```

並將 `Sinopac.pfx` 放在 `shioaji-server/` 目錄下。

| 變數 | 說明 | 必填 |
|------|------|------|
| `SHIOAJI_API_KEY` | 永豐金 API Key | 是 |
| `SHIOAJI_SECRET_KEY` | 永豐金 Secret Key（建立時只顯示一次） | 是 |
| `CA_PATH` | CA 憑證 `.pfx` 檔案的絕對路徑 | 下單需要 |
| `CA_PERSON` | 憑證密碼（預設為身分證字號） | 下單需要 |

> **還沒有 API Key？** 請先完成：
> 1. [開立永豐金帳戶](https://sinotrade.github.io/tutor/prepare/open_account)
> 2. [簽署 API 交易條款](https://sinotrade.github.io/tutor/prepare/terms)
> 3. [申請 API Key](https://sinotrade.github.io/tutor/prepare/token)
> 4. 在 API 管理頁面下載 CA 憑證（`.pfx`），密碼預設為身分證字號

### 3. 啟動

#### Docker（推薦）

```bash
make up           # 模擬環境
make up-live      # 正式環境
```

首次執行會自動 build image。Server 啟動時自動從 `.env` 讀取憑證並登入。

```bash
make status       # 健康檢查
make logs         # 查看 container stdout
tail -f server.log  # 查看應用 log（mount 到 host）
make down         # 停止
make restart      # 重啟
```

#### 本地執行（不用 Docker）

```bash
cd shioaji-server
uv sync
uv run shioaji-server          # 模擬環境
uv run shioaji-server --live   # 正式環境
```

### 4. 確認啟動成功

不論用哪種方式，成功啟動會看到：

```
INFO:shioaji_server.app:[shioaji-server] Auto-login starting (simulation)...
INFO:shioaji_server.app:[shioaji-server] Login successful! 2 account(s):
INFO:shioaji_server.app:  - Account: 00XXXXXX
INFO:shioaji_server.app:  - Account: XXXXXXX
INFO:     Uvicorn running on http://0.0.0.0:8123 (Press CTRL+C to quit)
```

如果缺少 `.env` 或必要的環境變數，server 仍會啟動但不會自動登入，並顯示設定提示。此時可以手動登入（見 [docs/REFERENCE.md](docs/REFERENCE.md#認證)）。

### 5. API 文件

Server 啟動後，可在瀏覽器開啟自動產生的互動式 API 文件：

- **Swagger UI**：http://localhost:8123/docs
- **ReDoc**：http://localhost:8123/redoc

所有端點、參數說明、request/response schema 都在裡面，可以直接在頁面上測試 API。

### 6. 連接 NautilusTrader

Server 啟動並登入後，NT adapter 即可連接：

```python
from nautilus_trader.adapters.sinopac.config import SinopacDataClientConfig, SinopacExecClientConfig

# adapter 的 gateway_port 預設仍為 8000；gateway 現在監聽 8123，
# 因此需以 gateway_port=8123 覆寫，與 .env 的 SHIOAJI_SERVER_PORT 對齊。
# （gateway_host / gateway_port / gateway_ws_path 皆可覆寫）
data_config = SinopacDataClientConfig(gateway_port=8123)
exec_config = SinopacExecClientConfig(gateway_port=8123)
```

adapter（venue `SINOPAC`）的設定欄位與工廠見 `nautilus_trader/adapters/sinopac/`
（`config.py` / `factories.py`）。

---

## 下單與數量單位

> 完整 curl 範例（含整股／盤中零股／期貨、改單、刪單）見 [docs/REFERENCE.md](docs/REFERENCE.md#下單)。

閘道器在 Shioaji SDK 邊界統一處理「股數 ↔ 張數」換算，呼叫端送出的股票 `quantity` **一律以股數計**：

- **整股**（`order_lot=Common`，預設）：`quantity` 必須為 **1000 的倍數**（1 張 = 1000 股）；閘道器 ÷1000 換成張數送入 SDK。非 1000 倍數會直接回 **HTTP 422**（`Common-lot quantity must be a multiple of 1000 shares`）。
- **零股**（`order_lot=IntradayOdd` 盤中零股 09:00–13:30，或 `Odd` 盤後零股 13:40–14:30）：`quantity` 為股數 1–999，直接送入 SDK（不 ÷1000）。
- **期貨／選擇權**：`quantity` 為口數，不換算。

**委託拒絕多為非同步**：`POST /api/orders/place` 成功送出時回 `200` 且 `status` 為 `OrderStatus.PendingSubmit`，真正的拒絕（`OrderStatus.Failed`、`op_code != "00"`）稍後才透過 WebSocket `order_update` 浮現（由 NT exec client 處理）；只有 SDK 同步即回報失敗狀態時，閘道器才在當下回 **HTTP 422** 作為第二道防線。

**`GET /api/orders/trades` 回報成交**：`TradeInfo` 的 `quantity` 已換回股數，並新增 `filled_qty`（已成交股數，`status.deals` 加總）與 `avg_fill_price`（成交量加權均價，未成交為 `0.0`）。WS 廣播的成交數量同樣已正規化為股數。

---

## Make 命令一覽

| 命令 | 說明 |
|------|------|
| `make build` | 建置 Docker image |
| `make up` | 啟動 server（模擬環境，detached） |
| `make up-live` | 啟動 server（正式環境，detached） |
| `make down` | 停止並移除 container |
| `make restart` | 重啟 server |
| `make logs` | 查看 container stdout |
| `make status` | 健康檢查 |
| `make local` | 本地執行（不用 Docker） |
| `make local-live` | 本地正式環境（不用 Docker） |
| `make clean` | 移除 container 和 image |
| `make help` | 顯示所有命令 |

---

## 模擬 vs 正式環境

| | 模擬環境 | 正式環境 |
|---|---|---|
| Docker | `make up` | `make up-live` |
| 本地 | `uv run shioaji-server` | `uv run shioaji-server --live` |
| 需要憑證 | 下單需要 | 下單需要 |
| 需要簽署條款 | 需要 | 需要 |
| 使用真實資金 | 否 | **是** |

切換到正式環境前，請確認：

- [ ] 已簽署 [API 交易條款](https://sinotrade.github.io/tutor/prepare/terms)
- [ ] 已在模擬環境完成下單測試
- [ ] API Key 已啟用正式環境權限
- [ ] 已設定 IP 白名單（Docker 部署需注意 container 對外 IP）

---

## 環境變數一覽

除了 `.env` 中的憑證變數，還支援以下設定：

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `SHIOAJI_SERVER_HOST` | Server 監聽地址 | `0.0.0.0` |
| `SHIOAJI_SERVER_PORT` | Server 監聽埠（Makefile 會自動讀取並映射 `-p PORT:PORT`）。本專案 `.env` 設為 `8123`（避開 8000 與本機其他服務如 LLM server 衝突）；未設定時程式 fallback 為 `8000` | `8123`（`.env`），`8000`（未設定時的 fallback） |
| `SHIOAJI_SIMULATION` | 是否為模擬模式（被 `--live` 覆蓋） | `true` |
| `SHIOAJI_LOG_LEVEL` | Log 等級（debug/info/warning/error） | `info` |
| `SHIOAJI_LOG_FILE` | 輪替檔案 log 路徑（`RotatingFileHandler`，10 MB × 3 份；Docker 會 mount 到 host） | `server.log` |
| `SHIOAJI_ENV_FILE` | 指定 `.env` 檔案路徑 | 自動搜尋 cwd 與上一層目錄 |

---

## 文件目錄

| 文件 | 說明 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系統架構、元件職責、資料流 |
| [docs/REFERENCE.md](docs/REFERENCE.md) | API 端點速查、curl 範例、常用操作 |

---

## 資料下載 — `shioaji-data` CLI

`shioaji-data` 是把歷史資料下載/檢查管線收斂成單一指令的 CLI。它透過上述 gateway
（需先啟動並登入）抓取資料，寫入 NautilusTrader 的 `ParquetDataCatalog`，並支援
**一次平行抓一串 ticker**。

### 安裝

CLI 隨套件安裝；editable 安裝即可在任意 cwd 使用：

```bash
cd shioaji-server
uv pip install -e .
uv run shioaji-data --help
```

### 全域旗標

四個子指令共用：

| 旗標 | 說明 | 預設 |
|------|------|------|
| `--catalog` | `ParquetDataCatalog` 目錄路徑 | `./catalog` |
| `--gateway-url` | gateway base URL（CLI 預設仍為 `8000`；gateway 監聽 `8123` 時需明確指定 `--gateway-url http://localhost:8123`） | `http://localhost:8000` |

### 子指令

| 指令 | 說明 |
|------|------|
| `fetch-bars` | 下載 1 分鐘 K 棒（副產品寫 instrument def） |
| `fetch-ticks` | 逐日下載成交 tick，配額感知（共享 `QuotaGate`） |
| `instrument-def` | 只寫 NT instrument 定義 |
| `inspect` | equity 定義 + bar 體檢（筆數/日期範圍/gap 偵測） |

`fetch-bars` / `fetch-ticks` / `instrument-def` 三者以**互斥必填**的 ticker 選擇器擇一：
`--code 0050`（單檔）、`--codes 0050,00631L,2330`（逗號分隔）、或
`--codes-file tickers.txt`（每行一檔，`#` 註解與空行略過）。

### 範例

```bash
# 單檔 K 棒
uv run shioaji-data fetch-bars --code 0050 --start 2024-01-01

# 平行批次抓 tick（4 併發，共享每日配額閘門）
uv run shioaji-data fetch-ticks --codes 0050,00631L --concurrency 4

# 只寫 instrument 定義
uv run shioaji-data instrument-def --code 2330

# 檢查 catalog
uv run shioaji-data inspect
```

### 續傳與盤中保護

**fetch-bars 自動續傳（冪等）**：bars 的續傳點直接讀自 catalog 最後一根 bar 的隔日，
不需要手動算 `--start`、也不需要刪 bar 目錄。

- 重跑同一條 `fetch-bars` 指令即從 catalog 續抓；已完成的檔印 `up to date — skipping`
  秒過、**零 API 呼叫**。
- 中途因連續錯誤截斷時回報 `partial`；**直接重跑同一條指令**即從截斷點續傳。

`fetch-ticks` 不同：ticks 仍是 `--start`-driven 的續傳——部分完成時 CLI 印出**逐 ticker**
的 resume 提示（各檔 `last_date` 不同，合併 `--start` 會重抓已抓過的日子），重跑需帶
`--start <last_date + 1 天>`。

**盤中截尾防護**：台北時間 15:00 前，`--end`（含顯式指定）會被自動 cap 到昨日，並印一行
`note: capping --end …`。盤中當日 bars/ticks 不完整，若寫進 catalog 會讓日粒度續傳永久
跳過當天剩餘時段，且 `inspect` 的日級 gap 偵測看不到。15:00 = 收盤（13:30）＋ 證交所
資料定稿緩衝。

**啟動探活**：每個 fetch 子指令啟動會做兩段 pre-flight——`/api/health` ＋ 一筆真實 `2330`
kbar 探針（近 14 日）。只有真的拿得到行情才放行，因為 `/api/health` 只反映登入旗標，
後端 Solace session 靜默失效時仍回報健康。兩種 `exit 1` 的差異：

- **gateway not reachable**：container 沒起來或未登入（確認 container 已啟動且已登入）。
- **session stale or unhealthy**：health OK 但 2330 探針無資料（需重新登入 gateway）。

`inspect` 離線、不探活。

退出碼：`0` 全部完成、`2` 有 partial/failed、`1` gateway 不通或 session 假活。

> **一次性維護鏈不在此 CLI 內**：restamp / regen / verify 等危險的一次性操作維持
> 獨立入口 `uv run python -m scripts.maintenance.<x>`，刻意不混進日常下載指令。

---

## 故障排除

### 啟動時顯示 `Auto-login skipped`

`.env` 未找到或缺少 `SHIOAJI_API_KEY` / `SHIOAJI_SECRET_KEY`。確認：
- `.env` 在 `shioaji-server/` 目錄下（或用 `SHIOAJI_ENV_FILE` 指定路徑）
- 變數名稱正確

### Docker 啟動時 `Auto-login failed: ReadFile Error No such file or directory`

`.env` 中的 `CA_PATH` 是 host 路徑，container 內找不到。使用 `make up` 會自動處理路徑映射，不需要手動修改 `CA_PATH`。如果自行 `docker run`，需加上 `-e CA_PATH=/app/Sinopac.pfx`。

### `Please sign XXXX first`

帳戶未簽署 API 交易條款。前往永豐金網站完成簽署：
https://sinotrade.github.io/tutor/prepare/terms

### `Already connected`（409）

Server 已登入。先 logout 再重新登入：

```bash
curl -X POST http://localhost:8123/api/auth/logout
```

### `Not connected`（503）

Server 尚未登入。檢查 `.env` 設定或手動呼叫 `/api/auth/login`。

### `No futures/options account available`

未開通期貨帳戶，需另外向永豐金申請。

### 凌晨/長跑時 session 靜默失效（`/api/health` 顯示 `logged_in:true` 但 `session_alive:false`）

後端 Solace session 可能靜默停止回應（SDK 不發斷線事件、登入旗標仍謊報已連線）。Server 現在內含背景 keepalive watchdog，會主動探活並在連續失敗時自動重登，**無需手動重啟容器**。靈敏度可由 `ShioajiGatewaySession` 的 `keepalive_interval`（探活間隔，預設 `5.0` 秒）與 `keepalive_fail_threshold`（連續失敗門檻，預設 `2`）兩個旋鈕調整。

### Port 被占用

```bash
lsof -i :8123
kill <PID>
```

---

## Credits

- [Shioaji](https://github.com/Sinotrade/Shioaji) — 永豐金證券提供的 Python 交易 API，本專案的核心依賴
- [Shioaji Documentation](https://sinotrade.github.io/zh/) — Shioaji 官方文檔
- [Claude Code](https://claude.ai/claude-code) — 本專案由 Anthropic 的 Claude Code 協助開發

---

## License

[MIT](LICENSE)
