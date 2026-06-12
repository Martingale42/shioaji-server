# API Reference 速查手冊

所有 API 端點的 curl 範例和常用操作速查。

Server 預設地址：`http://localhost:8123`（埠號可由 `.env` 的 `SHIOAJI_SERVER_PORT` 設定；本文件範例皆以 `8123` 為例）。

> **互動式 API 文件**：啟動 server 後開啟 [Swagger UI](http://localhost:8123/docs) 或 [ReDoc](http://localhost:8123/redoc)，可直接在瀏覽器瀏覽所有端點、參數說明並測試 API。

---

## 啟動與停止

### Docker（推薦）

```bash
make build        # 建置 image（首次 make up 會自動觸發）
make up           # 啟動（模擬環境，detached，自動登入）
make up-live      # 啟動（正式環境，detached）
make down         # 停止並移除 container
make restart      # 重啟

make logs         # 查看 container stdout
tail -f ~/.shioaji-server/logs/server.log  # 查看應用 log（mount 到 host）
make status       # 健康檢查
make clean        # 移除 container 和 image
make help         # 顯示所有命令
```

### 本地執行

```bash
cd shioaji-server
uv run shioaji-server          # 模擬環境（自動登入）
uv run shioaji-server --live   # 正式環境

# 背景執行
uv run shioaji-server &> ~/.shioaji-server/logs/server.log &

# 停止
kill $(lsof -ti :8123)
```

---

## 健康檢查

```bash
curl http://localhost:8123/api/health
# {"status":"ok","connected":true}

# 或
make status
```

---

## 認證

### 手動登入（自動登入失敗時使用）

```bash
curl -X POST http://localhost:8123/api/auth/login \
  -H "Content-Type: application/json" \
  --data-binary @- <<'EOF'
{
  "api_key": "YOUR_API_KEY",
  "secret_key": "YOUR_SECRET_KEY",
  "ca_path": "/path/to/Sinopac.pfx",
  "ca_passwd": "YOUR_CA_PASSWORD",
  "simulation": true
}
EOF
```

> Docker 環境中 `ca_path` 應為 `/app/Sinopac.pfx`。

### 登出

```bash
curl -X POST http://localhost:8123/api/auth/logout
```

### 查詢連線狀態

```bash
curl http://localhost:8123/api/auth/status
# {"connected":true,"simulation":true}
```

---

## 合約查詢

### 查詢單一股票

```bash
curl http://localhost:8123/api/contracts/stocks/2330
# {"code":"2330","name":"台積電","reference":580.0,"limit_up":638.0,...}
```

### 列出所有股票

```bash
curl http://localhost:8123/api/contracts/stocks
```

### 列出所有期貨

```bash
curl http://localhost:8123/api/contracts/futures
```

### 列出所有選擇權

```bash
curl http://localhost:8123/api/contracts/options
```

---

## 下單

### 數量單位約定（Wire-unit contract）

> **股票 `quantity` 一律以「股數」為單位**，閘道器在 Shioaji SDK 邊界自行換算成 SDK 期望的單位，呼叫端不需要知道整股／零股的內部換算。

| 市場 / `order_lot` | `quantity` 單位 | 合法範圍 | 邊界換算 |
|---|---|---|---|
| 股票 `Common`（整股，預設） | 股數 | **必須為 1000 的倍數**（1 張 = 1000 股） | 閘道器 ÷1000 換成張數送入 SDK |
| 股票 `IntradayOdd`（盤中零股，09:00–13:30） | 股數 | 1–999 | 直接送入 SDK（不 ÷1000） |
| 股票 `Odd`（盤後零股，13:40–14:30） | 股數 | 1–999 | 直接送入 SDK（不 ÷1000） |
| 期貨 / 選擇權 | 口數（contracts） | ≥ 1 | 不換算 |

整股 `quantity` 非 1000 倍數時，閘道器在送進 SDK 前直接回 **HTTP 422**：
`Common-lot quantity must be a multiple of 1000 shares, was <n>`。

### 股票買入（整股，1 張 = 1000 股）

```bash
curl -X POST http://localhost:8123/api/orders/place \
  -H "Content-Type: application/json" \
  -d '{
    "code": "2330",
    "action": "Buy",
    "price": 580.0,
    "quantity": 1000,
    "price_type": "LMT",
    "order_type": "ROD",
    "market": "stock"
  }'
```

### 股票賣出（整股）

```bash
curl -X POST http://localhost:8123/api/orders/place \
  -H "Content-Type: application/json" \
  -d '{
    "code": "2330",
    "action": "Sell",
    "price": 590.0,
    "quantity": 1000,
    "price_type": "LMT",
    "order_type": "ROD",
    "market": "stock"
  }'
```

### 股票盤中零股買入（`order_lot=IntradayOdd`，`quantity` 為股數 1–999）

```bash
curl -X POST http://localhost:8123/api/orders/place \
  -H "Content-Type: application/json" \
  -d '{
    "code": "2330",
    "action": "Buy",
    "price": 580.0,
    "quantity": 100,
    "price_type": "LMT",
    "order_type": "ROD",
    "order_lot": "IntradayOdd",
    "market": "stock"
  }'
```

### 期貨買入

```bash
curl -X POST http://localhost:8123/api/orders/place \
  -H "Content-Type: application/json" \
  -d '{
    "code": "TXFR1",
    "action": "Buy",
    "price": 22000,
    "quantity": 1,
    "price_type": "LMT",
    "order_type": "ROD",
    "market": "futures"
  }'
```

### 下單參數速查

| 參數 | 選項 | 說明 |
|------|------|------|
| `action` | `Buy`, `Sell` | 買賣方向 |
| `price_type` | `LMT`, `MKT`, `MKP` | 限價/市價/範圍市價 |
| `order_type` | `ROD`, `IOC`, `FOK` | 當日有效/立即成交否則取消/全部成交否則取消 |
| `order_cond` | `Cash`, `MarginTrading`, `ShortSelling` | 現股/融資/融券（僅股票） |
| `order_lot` | `Common`, `Odd`, `IntradayOdd`, `Fixing` | 整股/盤後零股/盤中零股/定盤（僅股票，預設 `Common`） |
| `quantity` | 整數 | 股票為**股數**、期貨/選擇權為口數（見上方〈數量單位約定〉） |
| `market` | `stock`, `futures`, `options` | 市場類型 |

> 注意：市價單（`MKT`/`MKP`）必須搭配 `IOC` 或 `FOK`，不能用 `ROD`。

> **委託拒絕（venue rejection）多為非同步**：`POST /api/orders/place` 成功送出時回 `200` 且 `status` 為 `OrderStatus.PendingSubmit`；真正被交易所拒絕（`OrderStatus.Failed`、`op_code != "00"`）通常**稍後**才透過 WebSocket 的 `order_update` 事件浮現（由 NT exec client 處理）。
> 只有當 SDK **同步**就回報失敗狀態（`Failed`/`Inactive`）時，閘道器才會在當下回 **HTTP 422**（`Order rejected by venue: ...`）作為第二道防線。

### 改單

```bash
# 改價
curl -X PUT http://localhost:8123/api/orders/update \
  -H "Content-Type: application/json" \
  -d '{"trade_id": "0001E0", "price": 585.0}'

# 減量（只能減少，不能增加）
curl -X PUT http://localhost:8123/api/orders/update \
  -H "Content-Type: application/json" \
  -d '{"trade_id": "0001E0", "quantity": 1}'
```

### 刪單

```bash
curl -X DELETE http://localhost:8123/api/orders/cancel \
  -H "Content-Type: application/json" \
  -d '{"trade_id": "0001E0"}'
```

### 查詢所有委託

```bash
curl http://localhost:8123/api/orders/trades
# [
#   {
#     "trade_id": "0001E0",
#     "code": "2330",
#     "action": "Buy",
#     "price": 580.0,
#     "quantity": 1000,
#     "status": "Filled",
#     "order_type": "ROD",
#     "price_type": "LMT",
#     "custom_field": "",
#     "filled_qty": 1000,
#     "avg_fill_price": 580.0
#   }
# ]
```

回傳每筆委託的 `TradeInfo`。數量欄位皆與下單時相同單位（股票為**股數**、期貨/選擇權為口數）：

| 欄位 | 說明 |
|------|------|
| `quantity` | 委託數量。整股委託已從 SDK 的張數 ×1000 還原為股數；零股/期貨/選擇權維持原值（factor 1） |
| `filled_qty` | 已成交數量（`status.deals` 各筆成交量加總，同樣換算為股數），尚未成交時為 `0` |
| `avg_fill_price` | 成交均價（以成交量加權），尚未成交時為 `0.0` |

---

## 行情查詢

### 即時快照（多檔）

```bash
curl "http://localhost:8123/api/market/snapshots?codes=2330,2317&market=stock"
```

### 歷史逐筆成交

```bash
curl "http://localhost:8123/api/market/ticks?code=2330&date=2026-03-06&market=stock"
```

### 歷史 K 線

```bash
curl "http://localhost:8123/api/market/kbars?code=2330&start=2026-03-01&end=2026-03-06&market=stock"
```

---

## 帳務查詢

### 持倉

```bash
# 股票持倉
curl "http://localhost:8123/api/account/positions?market=stock"

# 期貨持倉
curl "http://localhost:8123/api/account/positions?market=futures"
```

### 帳戶餘額

```bash
curl http://localhost:8123/api/account/balance
```

### 保證金（期貨）

```bash
curl http://localhost:8123/api/account/margin
```

### 損益

```bash
curl http://localhost:8123/api/account/pnl
```

---

## WebSocket 即時行情

### 連線

```bash
# 使用 websocat 工具
websocat ws://localhost:8123/ws
```

### 訂閱

```json
{"action": "subscribe", "contract_code": "2330", "quote_type": "tick"}
{"action": "subscribe", "contract_code": "2330", "quote_type": "bidask"}
```

### 取消訂閱

```json
{"action": "unsubscribe", "contract_code": "2330", "quote_type": "tick"}
```

### 接收格式

Tick 資料：
```json
{
  "type": "tick",
  "code": "2330",
  "data": {
    "close": 580.0,
    "volume": 1,
    "total_volume": 12345,
    "tick_type": 1,
    "bid_side_total_vol": 5000,
    "ask_side_total_vol": 4800,
    "avg_price": 579.5,
    "open": 578.0,
    "high": 582.0,
    "low": 577.0,
    "amount": 580000.0,
    "pct_chg": 0.35,
    "timestamp": "2026-03-06 09:00:01"
  }
}
```

五檔報價：
```json
{
  "type": "bidask",
  "code": "2330",
  "data": {
    "bid_price": [579.0, 578.0, 577.0, 576.0, 575.0],
    "bid_volume": [10, 25, 30, 15, 20],
    "ask_price": [580.0, 581.0, 582.0, 583.0, 584.0],
    "ask_volume": [8, 12, 20, 18, 15],
    "timestamp": "2026-03-06 09:00:01"
  }
}
```

委託更新（推送給所有連線 client）：
```json
{
  "type": "order_update",
  "event": "...",
  "data": { ... }
}
```

> **成交事件數量已正規化為股數**：股票整股（`order_lot=Common`）成交事件的 `quantity` 在廣播前已 ×1000 由張數換成股數；零股與期貨/選擇權維持原值。因此 WS 廣播的數量全程皆以股數計（與 `quantity` 單位一致）。委託被交易所拒絕時會以 `event` 為 `OrderStatus.Failed`（`op_code != "00"`）的 `order_update` 浮現。

---

## HTTP Status Codes

| Code | 說明 |
|------|------|
| 200 | 成功 |
| 400 | 請求錯誤（參數不合法、下單失敗等） |
| 404 | 合約或委託未找到 |
| 409 | 已經登入（重複登入） |
| 422 | 請求驗證失敗（JSON 格式錯誤）、整股 `quantity` 非 1000 倍數、或 SDK 同步回報的 venue rejection |
| 500 | Server 內部錯誤 |
| 503 | 尚未登入 |
