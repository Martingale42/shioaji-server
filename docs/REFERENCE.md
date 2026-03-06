# API Reference 速查手冊

所有 API 端點的 curl 範例和常用操作速查。

Server 預設地址：`http://localhost:8000`
Swagger UI：`http://localhost:8000/docs`

---

## 啟動與停止

```bash
# 啟動（模擬環境，自動登入）
cd shioaji-server && uv run shioaji-server

# 啟動（正式環境）
cd shioaji-server && uv run shioaji-server --live

# 背景啟動
cd shioaji-server && uv run shioaji-server &> server.log &

# 查看 log
tail -f server.log

# 停止
kill $(lsof -ti :8000)
```

---

## 健康檢查

```bash
curl http://localhost:8000/api/health
# {"status":"ok","connected":true}
```

---

## 認證

### 手動登入（自動登入失敗時使用）

```bash
curl -X POST http://localhost:8000/api/auth/login \
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

### 登出

```bash
curl -X POST http://localhost:8000/api/auth/logout
```

### 查詢連線狀態

```bash
curl http://localhost:8000/api/auth/status
# {"connected":true,"simulation":true}
```

---

## 合約查詢

### 查詢單一股票

```bash
curl http://localhost:8000/api/contracts/stocks/2330
# {"code":"2330","name":"台積電","reference":580.0,"limit_up":638.0,...}
```

### 列出所有股票

```bash
curl http://localhost:8000/api/contracts/stocks
```

### 列出所有期貨

```bash
curl http://localhost:8000/api/contracts/futures
```

### 列出所有選擇權

```bash
curl http://localhost:8000/api/contracts/options
```

---

## 下單

### 股票買入

```bash
curl -X POST http://localhost:8000/api/orders/place \
  -H "Content-Type: application/json" \
  -d '{
    "code": "2330",
    "action": "Buy",
    "price": 580.0,
    "quantity": 1,
    "price_type": "LMT",
    "order_type": "ROD",
    "market": "stock"
  }'
```

### 股票賣出

```bash
curl -X POST http://localhost:8000/api/orders/place \
  -H "Content-Type: application/json" \
  -d '{
    "code": "2330",
    "action": "Sell",
    "price": 590.0,
    "quantity": 1,
    "price_type": "LMT",
    "order_type": "ROD",
    "market": "stock"
  }'
```

### 期貨買入

```bash
curl -X POST http://localhost:8000/api/orders/place \
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
| `order_lot` | `Common`, `Odd`, `IntradayOdd`, `Fixing` | 整股/盤後零股/盤中零股/定盤（僅股票） |
| `market` | `stock`, `futures`, `options` | 市場類型 |

> 注意：市價單（`MKT`/`MKP`）必須搭配 `IOC` 或 `FOK`，不能用 `ROD`。

### 改單

```bash
# 改價
curl -X PUT http://localhost:8000/api/orders/update \
  -H "Content-Type: application/json" \
  -d '{"trade_id": "0001E0", "price": 585.0}'

# 減量（只能減少，不能增加）
curl -X PUT http://localhost:8000/api/orders/update \
  -H "Content-Type: application/json" \
  -d '{"trade_id": "0001E0", "quantity": 1}'
```

### 刪單

```bash
curl -X DELETE http://localhost:8000/api/orders/cancel \
  -H "Content-Type: application/json" \
  -d '{"trade_id": "0001E0"}'
```

### 查詢所有委託

```bash
curl http://localhost:8000/api/orders/trades
```

---

## 行情查詢

### 即時快照（多檔）

```bash
curl "http://localhost:8000/api/market/snapshots?codes=2330,2317&market=stock"
```

### 歷史逐筆成交

```bash
curl "http://localhost:8000/api/market/ticks?code=2330&date=2026-03-06&market=stock"
```

### 歷史 K 線

```bash
curl "http://localhost:8000/api/market/kbars?code=2330&start=2026-03-01&end=2026-03-06&market=stock"
```

---

## 帳務查詢

### 持倉

```bash
# 股票持倉
curl "http://localhost:8000/api/account/positions?market=stock"

# 期貨持倉
curl "http://localhost:8000/api/account/positions?market=futures"
```

### 帳戶餘額

```bash
curl http://localhost:8000/api/account/balance
```

### 保證金（期貨）

```bash
curl http://localhost:8000/api/account/margin
```

### 損益

```bash
curl http://localhost:8000/api/account/pnl
```

---

## WebSocket 即時行情

### 連線

```bash
# 使用 websocat 工具
websocat ws://localhost:8000/ws
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

---

## HTTP Status Codes

| Code | 說明 |
|------|------|
| 200 | 成功 |
| 400 | 請求錯誤（參數不合法、下單失敗等） |
| 404 | 合約或委託未找到 |
| 409 | 已經登入（重複登入） |
| 422 | JSON 格式錯誤 |
| 500 | Server 內部錯誤 |
| 503 | 尚未登入 |
