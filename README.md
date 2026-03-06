# Shioaji Server

Shioaji SDK 的 REST/WebSocket 閘道器，作為 NautilusTrader Shioaji adapter 的後端服務。

將永豐金 Shioaji Python SDK 包裝為 HTTP API + WebSocket 即時行情推送，讓 NautilusTrader（Rust/PyO3）可透過標準網路協定連接台灣證券/期貨市場。

## Quick Start 快速開始

### 1. 前置需求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (推薦) 或 pip
- 永豐金證券帳戶 + API Key（[申請指南](https://sinotrade.github.io/tutor/prepare/token)）
- CA 憑證檔案 `Sinopac.pfx`（[下載指南](https://sinotrade.github.io/tutor/prepare/token)）
- 已簽署 [API 交易條款](https://sinotrade.github.io/tutor/prepare/terms)

### 2. 安裝

```bash
cd shioaji-server
uv sync
```

### 3. 設定環境變數

在 `shioaji-server/` 目錄下建立 `.env`：

```env
SHIOAJI_API_KEY=your_api_key
SHIOAJI_SECRET_KEY=your_secret_key
CA_PATH=/absolute/path/to/Sinopac.pfx
CA_PERSON=your_ca_password
```

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

### 4. 啟動（一個命令完成啟動 + 登入）

```bash
cd shioaji-server

# 模擬環境（預設）
uv run shioaji-server

# 正式環境
uv run shioaji-server --live

# 背景執行
uv run shioaji-server &> server.log &
```

Server 啟動時會自動從 `.env` 讀取憑證並登入，你會看到：

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:shioaji_server.app:[shioaji-server] Auto-login starting (simulation)...
INFO:shioaji_server.app:[shioaji-server] Login successful! 2 account(s):
INFO:shioaji_server.app:  - Account: 00XXXXXX
INFO:shioaji_server.app:  - StockAccount: XXXXXXX
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

如果缺少 `.env` 或必要的環境變數，server 仍會啟動但不會自動登入，並顯示設定提示。此時你可以手動登入（見 [docs/REFERENCE.md](docs/REFERENCE.md)）。

### 5. 連接 NautilusTrader

Server 啟動並登入後，NT adapter 即可連接：

```python
from nautilus_trader.adapters.shioaji.config import ShioajiDataClientConfig, ShioajiExecClientConfig

# 預設連接 localhost:8000
data_config = ShioajiDataClientConfig()
exec_config = ShioajiExecClientConfig()
```

完整範例見 `nautilus_trader/examples/live/shioaji/`。

---

## 模擬 vs 正式環境

| | 模擬環境 | 正式環境 |
|---|---|---|
| 啟動命令 | `uv run shioaji-server` | `uv run shioaji-server --live` |
| 需要憑證 | 下單需要 | 下單需要 |
| 需要簽署條款 | 需要 | 需要 |
| 使用真實資金 | 否 | **是** |

切換到正式環境前，請確認：

- [ ] 已簽署 [API 交易條款](https://sinotrade.github.io/tutor/prepare/terms)
- [ ] 已在模擬環境完成下單測試
- [ ] API Key 已啟用正式環境權限
- [ ] 已設定 IP 白名單

---

## 環境變數一覽

除了 `.env` 中的憑證變數，還支援以下設定：

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `SHIOAJI_SERVER_HOST` | Server 監聽地址 | `0.0.0.0` |
| `SHIOAJI_SERVER_PORT` | Server 監聽埠 | `8000` |
| `SHIOAJI_SIMULATION` | 是否為模擬模式（被 `--live` 覆蓋） | `true` |
| `SHIOAJI_LOG_LEVEL` | Log 等級（debug/info/warning/error） | `info` |
| `SHIOAJI_ENV_FILE` | 指定 `.env` 檔案路徑 | 自動搜尋 cwd 和上層目錄 |

---

## 文件目錄

| 文件 | 說明 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系統架構、元件職責、資料流 |
| [docs/REFERENCE.md](docs/REFERENCE.md) | API 端點速查、curl 範例、常用操作 |

---

## 故障排除

### 啟動時顯示 `Auto-login skipped`

`.env` 未找到或缺少 `SHIOAJI_API_KEY` / `SHIOAJI_SECRET_KEY`。確認：
- `.env` 在 `shioaji-server/` 目錄下（或用 `SHIOAJI_ENV_FILE` 指定路徑）
- 變數名稱正確

### `Please sign XXXX first`

帳戶未簽署 API 交易條款。前往永豐金網站完成簽署：
https://sinotrade.github.io/tutor/prepare/terms

### `Already connected`（409）

Server 已登入。先 logout 再重新登入：

```bash
curl -X POST http://localhost:8000/api/auth/logout
```

### `Not connected`（503）

Server 尚未登入。檢查 `.env` 設定或手動呼叫 `/api/auth/login`。

### `No futures/options account available`

未開通期貨帳戶，需另外向永豐金申請。

### Port 8000 被占用

```bash
# 找到佔用的程序
lsof -i :8000
# 終止
kill <PID>
```
