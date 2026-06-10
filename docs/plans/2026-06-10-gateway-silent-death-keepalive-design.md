# 設計:Gateway 靜默死亡自癒(背景 keepalive watchdog)

日期:2026-06-10
狀態:已審核通過
前置:本分支先行 commit `0bb74bf`(rename `ShioajiClient` → `ShioajiGatewaySession` /
`ShioajiGatewayClient`、`client.py` → `session.py`)——本設計一律用新名。

## 問題

Gateway 已有完整的 Solace session 復原機制(`_handle_session_down` → `_relogin`
→ re-register callbacks → `_resubscribe`,8 個回歸測試蓋住),**但它只有一個觸發點**:

```
session.py  self.api.set_session_down_callback(self._schedule_reconnect)
            _schedule_reconnect() → _handle_session_down()
```

即 Shioaji SDK 主動回報的 `session_down` callback。問題是:

1. **靜默死亡 callback 不觸發**。後端 Solace session 可能靜默停止回應(凌晨常見),
   SDK 沒發 `session_down` 事件。此時 `connected` 旗標仍為 `True`(謊報)。
2. **主動探針抓得到、卻不觸發復原**。`check_session()`(打 `api.usage()`)能偵測
   死亡(`session_alive:false`),但它回 `False` **不會**觸發 `_handle_session_down`
   ——只有 callback 會。
3. **沒有任何背景輪詢**。Dockerfile 無 `HEALTHCHECK`、無排程探活。`/api/health`
   只在外部有人來打時才會順帶探一次。閒置凌晨沒人打 → 沒人探 → 持續死著。

實證:`/api/health` 回 `{"logged_in":true,"session_alive":false,"connected":false}`
——`logged_in`(=`self.connected`)仍 true 證明 `_handle_session_down` 從沒跑過
(它一開頭就會把 `connected=False`)。對照:這正是
[[gotcha_gateway_session_health]] 描述的缺口——復原修的是「SDK 回報的斷線」,蓋不到
「靜默死亡」。

互補關係:`shioaji-data` CLI 端已有 2330 kbar 探針(commit `8451d07`),那是**客戶端
守門**(死的就拒跑),救不了 gateway;本設計補的是 **server 端自癒**。

## 決策(已逐項對齊)

| 分岔 | 選擇 |
|------|------|
| 觸發機制 | 背景 watchdog(server 端 `ShioajiGatewaySession` 內,長期 asyncio task) |
| 探活間隔 | `keepalive_interval = 5.0`s(可調 dataclass field) |
| 靈敏度 | `keepalive_fail_threshold = 2`(連續 2 次失敗才觸發,過濾瞬間 blip) |
| 復原 | **複用既有 `_handle_session_down`**,不寫新邏輯 |

## 設計

### 1. 核心機制(`session.py`,`ShioajiGatewaySession`)

給既有 `_handle_session_down` **加第二個觸發器**——一條「自己探到沒呼吸也會響」
的線。把「一拍策略」與「排程迴圈」拆開以利測試:

```python
async def _keepalive_tick(self, consecutive_fails: int) -> int:
    """探一次,回傳更新後的連續失敗數;達門檻則觸發復原。"""
    if not self.connected:
        return consecutive_fails          # 登出/恢復中 → 不探、不計
    if await self.check_session(force=True):
        return 0                          # 活著 → 歸零
    consecutive_fails += 1
    if consecutive_fails >= self.keepalive_fail_threshold:
        self._schedule_recovery()         # → _handle_session_down()
        return 0
    return consecutive_fails

async def _keepalive_loop(self) -> None:
    fails = 0
    while True:
        try:
            await asyncio.sleep(self.keepalive_interval)
            fails = await self._keepalive_tick(fails)
        except asyncio.CancelledError:
            raise                          # 乾淨退出
        except Exception:
            log.exception("[shioaji-server] keepalive tick error; continuing")
```

`_schedule_recovery()` = `self._recovery_task = asyncio.create_task(self._handle_session_down())`
(存 reference,避開 fire-and-forget 被 GC 的坑)。

### 2. 為什麼這些守衛是對的

- **`force=True`**:`check_session` 快取 `session_probe_ttl`(5s)。watchdog 要當下
  真相,不能讀到剛才 `/health` 留下的舊「ok」。
- **`if not self.connected`**:① 刻意 logout → `connected=False` → watchdog 閉嘴,
  不自作主張重登;② 復原中 `_handle_session_down` 開頭即 `connected=False` →
  watchdog 跳過,不跟進行中的復原打架;③ 靜默死亡 = `connected=True` 但探針 `False`
  → 正好是該動手的唯一情境。
- **threshold=2**:單次 Solace blip(SDK event 12→13,通常幾秒自癒)→ 下一拍探針
  成功 → `fails` 歸零,不白白 rebuild SDK。interval=5s 下確認窗口 ~10s。
- **`create_task` 而非既有 `_schedule_reconnect`**:`_schedule_reconnect` 用
  `run_coroutine_threadsafe`(給 SDK callback 跨執行緒用);watchdog 本身在 event
  loop 裡,`create_task` 才是 loop-native。兩路最終都進同一支 `_handle_session_down`,
  它有 `_reconnecting` 鎖把並發觸發收斂成一次——**SDK callback 與 watchdog 同時響
  也安全**。

### 3. 生命週期(`ShioajiGatewaySession` 自持,不動 app.py)

keepalive 起停綁在 login/logout,最內聚也最好測:

| 觸發 | 動作 |
|------|------|
| `login()` 成功尾端 | `start_keepalive()`(idempotent:task 還活著就不開第二個) |
| `logout()` | `stop_keepalive()`(cancel + await + 清 `None`) |
| `_relogin()` | **不碰** keepalive(觸發它的 watchdog 本來就還在跑) |

連帶:`_auto_login → login()` 自動起;lifespan 關機 `await logout()` 自動停。
**app.py 不需改**(現有 lifespan 已 call `_auto_login` 與 `logout`)。手動
`POST /api/auth/login` 也照樣起。

新增 dataclass field:`keepalive_interval: float = 5.0`、
`keepalive_fail_threshold: int = 2`、
`_keepalive_task / _recovery_task: asyncio.Task | None = None`(同現有
`session_probe_ttl` 等風格,之後要調不動邏輯)。

### 4. 邊界與韌性

- **loop 絕不可因例外自殺**:每拍包 try/except;非 `CancelledError` log 後續跑,
  `CancelledError` 才退出。`check_session` 已把探針例外吞成 `False`,故失敗是正常
  `False`、計入門檻,不是例外。
- **never logged in**(auto-login 失敗)→ `login()` 沒成功 → keepalive 不啟動。
- **可測性**:`_keepalive_tick(fails) -> int` 是純策略(探一次、計數、必要時觸發、
  回傳新計數);`_keepalive_loop` 只負責 sleep + 呼叫 tick。測試直接打 tick,不碰
  真實時間、不玩 cancel 體操。

### 5. 否決的替代方案

- **`/health` 觸發**(check_session 回 False 時就觸發):零新 task,但只在「有人打
  /health」時才探——閒置凌晨沒人打就不自癒,治標不治本。
- **Docker HEALTHCHECK + /health 觸發**:複用容器基建,但只在 Docker 生效
  (`make local` 裸跑不癒),且 healthcheck 的本能是重啟整個容器、非重登 session。

### 6. 測試(`tests/test_session_recovery.py` 延伸,offline MagicMock)

| 測試 | 驗證 |
|------|------|
| `tick_triggers_recovery_after_2_consecutive_fails` | 連續 2 次 False → `_schedule_recovery` 一次;**1 次不觸發** |
| `tick_resets_on_success` | 序列 [False, True, False] → 永不到門檻、零觸發 |
| `tick_skips_when_not_connected` | `connected=False` → `check_session` 沒被呼叫、零觸發 |
| `loop_survives_tick_exception` | tick 拋例外 → loop log 後續跑、不死 |
| `start_keepalive_idempotent` | 連叫兩次 → 單一 task |
| `stop_keepalive_cancels` | start 後 stop → task cancelled/done |
| `login_starts_logout_stops_keepalive` | login 起、logout 停 |

既有 8 個 recovery 測試不動(複用同一支 `_handle_session_down`,行為不變)。

### 7. 刻意不做(YAGNI)

- 不加 Docker HEALTHCHECK(選了純 watchdog)。
- 不改 `check_session` 本身。
- 不做「僅盤中探活」(`api.usage()` 走帳務限流 25 req/5s,5s 一筆只用 1/25、不吃
  資料配額,24/7 成本可忽略)。
- 不加 env 覆寫(dataclass 預設夠用)。

可選 follow-up(不在此範圍):`POST /api/auth/reconnect` 端點直接呼叫
`_handle_session_down`,給維運一個手動槓桿;watchdog 上線後大多用不到。

## 核心取捨

你已有「醫生」(`_handle_session_down`,完整且測過),缺的是「定時量脈搏的護士」。
watchdog 不是新醫術,是把護士補進來——而且護士跟原本那個「SDK 警報器」共用同一個
叫醫生的鈴(`_reconnecting` 鎖),兩個一起拉鈴也只來一個醫生。
