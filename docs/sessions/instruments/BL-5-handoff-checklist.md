# BL-5 Hand-off Checklist — instrument 管線剩餘 live 驗證

> 一次 live-integration session 照跑即可收尾 BL-5。cwd 預設 `/home/cy/Code/MT5/shioaji-server`，除非另註。
> 對應 `docs/BACKLOG.md` BL-5、`docs/qa/2026-06-08-instruments-full-qa.md`（Deferred 區）。

## 前置條件狀態（2026-06-09 已驗）

| 前置 | 狀態 | 備註 |
|------|------|------|
| sinopac wheel 裝入 shioaji-server venv | ✅ 已就緒 | `nt 1.226.2`；`nautilus_trader.adapters.sinopac` + `nautilus_pyo3.sinopac` 可 import |
| uv 版本 `==0.11.6`（整合測試門檻） | ✅ 已就緒 | `uv --version` = 0.11.6 |
| WS-D 真實 catalog regen | ✅ 已完成 | marker `catalog/.instruments_regenerated`、備份 `catalog_pre_instrument_regen_backup/` 在 |
| `.env`（Sinopac creds） | ✅ 在 | `make up` 走 simulation 登入 |
| **gateway 已登入並在 :8000** | ☐ **本 session 要起** | 見 Step 0 |

> 也就是說 BL-5 其實**已不再 blocked**——只差把 gateway 起來跑這幾個 probe。

---

## Step 0 · 起 gateway 並確認登入

```bash
make up                # simulation、detached（會 _ensure-build/_ensure-env/_ensure-log）
sleep 11
curl -s localhost:8000/api/health | jq -c .          # 期望 connected:true, session_alive:true
curl -s "localhost:8000/api/contracts/futures" | jq 'length'   # >0 代表合約已載入
```
- ☐ `/api/health` `connected:true`、`session_alive:true`
- ☐ `/api/contracts/futures`、`/api/contracts/options` 回非空

---

## Step 1 · 期貨 GATE probe（multiplier 走 Shioaji 權威值、lot 來自 unit）

挑一個近月期貨代碼（例如 TXF 系列）並比對 adapter 建出的 instrument：

```bash
# 1) 從 gateway 抓一個期貨代碼 + 它的 Shioaji multiplier/unit
curl -s "localhost:8000/api/contracts/futures" | jq -r '[.[] | select(.code|startswith("TXF"))][0] | {code, multiplier, unit, currency, underlying_code}'
# 2) 經 provider 建出 instrument，assert 與 Shioaji 值一致（非硬編碼 fallback 2000）
uv run python - <<'PY'
import asyncio
from nautilus_trader.model.identifiers import InstrumentId
from scripts.instruments import load_instrument
async def main():
    code = "TXFG6.SINOPAC"   # ← 換成上一行抓到的真實近月代碼
    inst = await load_instrument("http://localhost:8000", InstrumentId.from_str(code))
    print(type(inst).__name__, "mult=", inst.multiplier, "lot=", inst.lot_size,
          "tick=", inst.price_increment, "underlying=", inst.underlying)
asyncio.run(main())
PY
```
- ☐ 型別 = `FuturesContract`
- ☐ `multiplier` == Shioaji 合約 `multiplier`（例如 TXF=200），**非** fallback 2000
- ☐ `lot_size` 來自 `unit`（期貨通常 1）
- ☐ `underlying` = `underlying_code`（非 root symbol）

## Step 2 · 選擇權 GATE probe（建出 OptionContract、不再 bail）

```bash
curl -s "localhost:8000/api/contracts/options" | jq -r '[.[] | select(.code|startswith("TXO"))][0] | {code, option_right, strike_price, multiplier}'
uv run python - <<'PY'
import asyncio
from nautilus_trader.model.identifiers import InstrumentId
from scripts.instruments import load_instrument
async def main():
    code = "TXO..._.SINOPAC"   # ← 換成上一行抓到的真實 TXO 代碼
    inst = await load_instrument("http://localhost:8000", InstrumentId.from_str(code))
    print(type(inst).__name__, "kind=", inst.option_kind, "strike=", inst.strike_price,
          "mult=", inst.multiplier)
asyncio.run(main())
PY
```
- ☐ 型別 = `OptionContract`（**不** raise / 不 `bail!`）— 這是 WS-A/WS-B 修 `option_right` 的核心驗證
- ☐ `option_kind` 對應 gateway 的 `"C"`/`"P"`、`strike_price` 正確、`multiplier` == Shioaji 值

## Step 3 · WS-C 同 instrument 等價（backtest 腳本 == live node）

兩條路徑都走同一個 `SinopacInstrumentProvider` → 同一 Rust parse；驗證它們對同一 `InstrumentId` 給出完全一致的欄位。最簡可跑版本：provider 建出的 == 已 regen 進 catalog 的定義。

```bash
uv run python - <<'PY'
import asyncio
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from scripts.instruments import load_instrument
async def main():
    iid = InstrumentId.from_str("0050.SINOPAC")
    via_provider = await load_instrument("http://localhost:8000", iid)
    via_catalog  = ParquetDataCatalog("catalog").instrument(iid)   # WS-D regen 後的定義
    for f in ("id", "price_precision", "price_increment", "lot_size"):
        a, b = getattr(via_provider, f), getattr(via_catalog, f)
        print(f, a, b, "OK" if a == b else "MISMATCH")
asyncio.run(main())
PY
```
- ☐ `id` / `price_precision` / `price_increment` / `lot_size`（期/選另比 `multiplier`/`underlying`）全 `OK`
- ☐（可選，最完整）啟一個 minimal live node、`cache.instrument(iid)` 與上面比對一致

## Step 4 · sinopac Python 整合測試

```bash
cd /home/cy/Code/MT5/nautilus_trader        # branch sinopac-adapter-clean
uv run --active --no-sync pytest tests/integration_tests/adapters/sinopac/ -v
```
- ☐ `test_config.py` / `test_execution.py` / `test_factories.py` 全綠（uv 已 0.11.6，版本門檻已過）
- ☐ 不 re-sync venv（保留已裝的 sinopac extension）

## Step 5 · 收尾與簽核

- ☐ 四步全綠後，更新 `docs/BACKLOG.md` **BL-5 → ✅ 已修**，記下各 probe 的觀察值 + commit。
- ☐ 資料紅線簽核：確認 `catalog` 的 bar/tick 筆數 + first ts_event 與 `catalog_pre_instrument_regen_backup/` 一致後，**才**可刪除備份（345M）。
  ```bash
  uv run python -c "from nautilus_trader.persistence.catalog import ParquetDataCatalog as C; from nautilus_trader.model.data import TradeTick,Bar; c=C('catalog'); print('ticks',len(c.query(TradeTick)),'bars',len(c.query(Bar)))"
  # 與備份比對一致後： rm -rf catalog_pre_instrument_regen_backup
  ```
- ☐ 關 gateway：`make down`

---

## 疑難排查

- **gateway `connected:false`**：`.env` 的 `SHIOAJI_API_KEY`/`SECRET_KEY` 缺或失效；simulation 仍需有效 key。見 [[gotcha-gateway-session-health]]（`/api/health` 假活）。
- **`make up` build 失敗**：buildx 太舊 → 改 `DOCKER_BUILDKIT=0 make build` 再 `make up`。見 [[gotcha-docker-buildx-stale]]。
- **整合測試 `Required uv version ==0.11.6`**：本機 uv 已 0.11.6；若報錯確認沒被別的 shim 蓋掉。見 [[gotcha-nautilus-uv-version-pin]]。
- **mixed catalog 全掃壞掉**：regen 用 `--ids-from-equity-dir` 做 scoped 列舉、`--id-suffix .SINOPAC` 過濾、`--no-backup`（外部已備份時）。
