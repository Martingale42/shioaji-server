# Architecture 系統架構

## Overview 概覽

Shioaji Server 是一個 **協定轉換閘道器**，將永豐金 Shioaji Python SDK（同步、callback-based）轉換為標準 REST API + WebSocket 介面，讓 NautilusTrader 的 Rust/PyO3 adapter 可以透過網路協定操作台灣市場。

```
┌──────────────────────────────────────────────────────────────────┐
│                        NautilusTrader                            │
│  ┌─────────────────────┐    ┌──────────────────────────┐        │
│  │  ShioajiDataClient   │    │  ShioajiExecutionClient   │        │
│  │  (Rust/PyO3)         │    │  (Rust/PyO3)               │        │
│  └────────┬─────────────┘    └──────────┬────────────────┘        │
│           │ HTTP + WS                    │ HTTP + WS               │
└───────────┼──────────────────────────────┼────────────────────────┘
            │                              │
            ▼                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Shioaji Server (FastAPI)                    │
│                                                                  │
│  ┌──────────┐  ┌───────────┐  ┌────────┐  ┌─────────┐          │
│  │ /api/auth │  │/api/orders│  │/api/mkt│  │/api/acct│          │
│  └─────┬────┘  └─────┬─────┘  └───┬────┘  └────┬────┘          │
│        │             │             │             │               │
│        └─────────────┴─────────────┴─────────────┘               │
│                              │                                   │
│                     ┌────────▼────────┐    ┌──────────────────┐  │
│                     │  ShioajiClient   │    │ ConnectionManager│  │
│                     │  (SDK wrapper)   │◄───│ (WS fan-out)     │  │
│                     └────────┬────────┘    └──────────────────┘  │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │ Shioaji SDK (sync + callbacks)
                               ▼
                     ┌───────────────────┐
                     │   永豐金 Sinopac    │
                     │  (證券/期貨市場)     │
                     └───────────────────┘
```

---

## 元件職責

### `__main__.py` — 入口

- 載入 `.env` 環境變數
- 解析 CLI 參數（`--live`）
- 設定 log level
- 啟動 uvicorn

### `app.py` — FastAPI 應用

- 定義 `lifespan`：啟動時建立 `ShioajiClient` + 自動登入，關閉時 logout
- 掛載所有 route routers
- `/ws` WebSocket 端點：處理行情訂閱/取消訂閱
- `/api/health` 健康檢查

### `client.py` — ShioajiClient

SDK 的核心封裝層，解決兩個問題：

1. **同步轉非同步**：Shioaji SDK 全部是同步 blocking call，透過 `run_in_executor` 包成 async
2. **Callback 路由**：SDK 的行情/委託 callback 是在獨立 thread 觸發，透過 `register_callbacks` 將資料路由到 WebSocket manager

```python
@dataclass
class ShioajiClient:
    api: sj.Shioaji           # SDK 實例
    connected: bool            # 連線狀態
    simulation: bool           # 模擬/正式
    _lock: asyncio.Lock        # 防止並發登入/登出

    async def login(...)       # 非同步登入（executor）
    async def logout(...)      # 非同步登出（executor）
    async def run_sync(fn)     # 把任意同步 SDK call 包成 async
    def register_callbacks()   # 註冊 SDK callback → WS manager
```

### `ws/manager.py` — ConnectionManager

管理 WebSocket 連線和行情分發：

- **訂閱追蹤**：`subscriptions: dict[(code, quote_type), set[WebSocket]]`
- **引用計數**：第一個 client 訂閱時才向 SDK subscribe，最後一個取消時才 unsubscribe
- **跨 thread 分發**：SDK callback thread → `asyncio.run_coroutine_threadsafe` → async broadcast
- **委託推送**：`broadcast_order_update` 推送到所有已連線 client

### `models.py` — Pydantic Models

所有 request/response 的資料結構定義，分為：

- **Auth**：`LoginRequest`, `LoginResponse`, `StatusResponse`
- **Contracts**：`StockContract`, `FuturesContract`, `OptionsContract`
- **Market Data**：`SnapshotData`, `TicksResponse`, `KBarsResponse`
- **Orders**：`PlaceOrderRequest`, `UpdateOrderRequest`, `CancelOrderRequest`, `TradeInfo`
- **Account**：`Position`, `AccountBalance`, `MarginInfo`, `ProfitLoss`

### `errors.py` — 錯誤處理

將 `RuntimeError` 映射為 HTTP status codes：
- `Not connected` → 503
- `Already connected` → 409
- 其他 → 500

---

## Routes 路由

### `/api/auth` — 認證

| Method | Path | 說明 |
|--------|------|------|
| POST | `/api/auth/login` | 登入（支援 simulation 參數） |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/status` | 查詢連線狀態 |

### `/api/contracts` — 合約

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/contracts/stocks` | 所有股票合約 |
| GET | `/api/contracts/stocks/{code}` | 單一股票合約 |
| GET | `/api/contracts/futures` | 所有期貨合約 |
| GET | `/api/contracts/options` | 所有選擇權合約 |

### `/api/orders` — 委託

| Method | Path | 說明 |
|--------|------|------|
| POST | `/api/orders/place` | 下單 |
| PUT | `/api/orders/update` | 改單（改價/減量） |
| DELETE | `/api/orders/cancel` | 刪單 |
| GET | `/api/orders/trades` | 查詢所有委託 |

### `/api/market` — 行情查詢

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/market/snapshots` | 即時快照（多檔） |
| GET | `/api/market/ticks` | 歷史逐筆成交 |
| GET | `/api/market/kbars` | 歷史 K 線 |

### `/api/account` — 帳務

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/account/positions` | 持倉查詢 |
| GET | `/api/account/balance` | 帳戶餘額 |
| GET | `/api/account/margin` | 保證金（期貨） |
| GET | `/api/account/pnl` | 損益查詢 |

### `/ws` — WebSocket 即時行情

Client 發送 JSON 訊息訂閱/取消：

```json
{"action": "subscribe", "contract_code": "2330", "quote_type": "tick"}
{"action": "unsubscribe", "contract_code": "2330", "quote_type": "tick"}
```

Server 推送行情資料：

```json
{"type": "tick", "code": "2330", "data": {"close": 580.0, "volume": 1, ...}}
{"type": "bidask", "code": "2330", "data": {"bid_price": [...], ...}}
{"type": "order_update", "event": "...", "data": {...}}
```

---

## 資料流

### 下單流程

```
NT ExecClient                Shioaji Server              Sinopac
     │                            │                        │
     │  POST /api/orders/place    │                        │
     │ ─────────────────────────► │                        │
     │                            │  api.place_order()     │
     │                            │ ─────────────────────► │
     │                            │ ◄───────────────────── │
     │  {"trade_id", "status"}    │                        │
     │ ◄───────────────────────── │                        │
     │                            │                        │
     │                            │  order callback        │
     │  WS: order_update          │ ◄───────────────────── │
     │ ◄─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │                        │
```

### 即時行情流程

```
NT DataClient                Shioaji Server              Sinopac
     │                            │                        │
     │  WS: subscribe 2330/tick   │                        │
     │ ─────────────────────────► │                        │
     │                            │  api.quote.subscribe() │
     │                            │ ─────────────────────► │
     │  WS: {"type":"subscribed"} │                        │
     │ ◄───────────────────────── │                        │
     │                            │                        │
     │                            │  tick callback (thread) │
     │                            │ ◄───────────────────── │
     │                            │  run_coroutine_        │
     │  WS: {"type":"tick",...}   │  threadsafe → broadcast│
     │ ◄─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │                        │
```

---

## 設計決策

### 為什麼需要 Gateway？

Shioaji SDK 是純 Python + C extension，有幾個限制：
1. **同步 API** — 所有 call 都是 blocking，不適合 NautilusTrader 的 async 事件迴圈
2. **Callback thread** — 行情回呼在 SDK 內部 thread 觸發，需要跨 thread 橋接
3. **Python-only** — NautilusTrader 核心是 Rust，無法直接呼叫 Shioaji SDK

Gateway 模式將 SDK 隔離在獨立 process，透過 HTTP/WS 提供語言無關的介面。

### 為什麼用 FastAPI？

- 原生 async 支援，搭配 `run_in_executor` 處理同步 SDK
- 自動產生 OpenAPI docs（`/docs`）方便開發除錯
- Pydantic model 提供 request/response 驗證
- WebSocket 內建支援

### 單一 SDK 實例

整個 server 共用一個 `ShioajiClient` 實例（存在 `app.state.sj`）。Shioaji SDK 本身不支援多實例（受限於連線數和 callback 註冊），單一實例也符合一個 server 對應一個交易帳戶的設計。

---

## 檔案結構

```
shioaji-server/
├── .env                    # 環境變數（gitignored）
├── .gitignore
├── pyproject.toml          # 專案設定、依賴
├── server.log              # 執行日誌（gitignored）
├── Sinopac.pfx             # CA 憑證（gitignored）
├── README.md               # 快速開始指南
├── docs/
│   ├── ARCHITECTURE.md     # 本文件
│   └── REFERENCE.md        # API 速查手冊
└── src/
    └── shioaji_server/
        ├── __init__.py
        ├── __main__.py     # 入口：載入 .env、啟動 uvicorn
        ├── app.py          # FastAPI app、lifespan、auto-login、WS 端點
        ├── client.py       # ShioajiClient：SDK 封裝、async bridge
        ├── errors.py       # RuntimeError → HTTP status 映射
        ├── models.py       # Pydantic request/response models
        ├── routes/
        │   ├── auth.py     # 登入/登出/狀態
        │   ├── contracts.py# 合約查詢
        │   ├── market_data.py # 行情查詢（snapshot/ticks/kbars）
        │   ├── orders.py   # 下單/改單/刪單/查詢
        │   └── account.py  # 持倉/餘額/保證金/損益
        └── ws/
            └── manager.py  # WebSocket 連線管理、行情分發
```
