# Batch 1 — Gateway Resilience (G5–G8) 程式碼審查報告

**審查日期：** 2026-06-08
**審查範圍：** commits `9353ba3`（G5+G6）、`ed508de`（G7）、`acd7ce9`（G8）
**對照文件：** `docs/plans/2026-06-08-gateway-resilience.md`、`docs/AUDIT.md`（G5–G8）

## 裁決：APPROVED WITH NOTES

四個缺口都依計畫關閉，正確性與韌性目標達成，無 Critical / Important 問題。
有兩個 Minor 觀察（log 噪音與測試覆蓋），不阻擋合併。

---

## 驗證輸出

### `uv run pytest tests/ -v`（tail）
```
tests/test_health.py::test_not_logged_in_is_not_alive PASSED             [  7%]
tests/test_health.py::test_live_session_is_alive PASSED                  [ 14%]
tests/test_health.py::test_dead_backend_is_not_alive PASSED             [ 21%]
tests/test_health.py::test_probe_result_is_cached_within_ttl PASSED      [ 28%]
tests/test_health.py::test_force_bypasses_cache PASSED                   [ 35%]
tests/test_health.py::test_concurrent_probes_are_single_flight PASSED    [ 42%]
tests/test_session_recovery.py::... (8 項) PASSED
======================== 14 passed, 1 warning in 0.37s =========================
```

### `uv run ruff check src/ tests/`
```
All checks passed!
```

### import smoke
```
IMPORTS OK
```

### G8 變異測試（reviewer 自行驗證測試非平凡通過）
把 `async with self._probe_lock:` 改為 `if True:`（拿掉鎖、保留 double-check），
`test_concurrent_probes_are_single_flight` 立即 fail：
```
AssertionError: Expected 'usage' to have been called once. Called 10 times.
```
還原後重新 pass。**測試確實守得住回歸**，不是假綠燈。

---

## 逐項評估

### G5 — WebSocket 連線檢查 + finally 清理 [`src/shioaji_server/app.py:147-232`]

**正確性 vs 計畫：達成。**
- 迴圈開頭 `if not sj_client.connected` 守衛回錯誤幀並 `continue`，不再硬做訂閱（`app.py:178-182`）。
- 每個 action body 包 `try/except Exception`（`app.py:187-224`），`resolve_contract` 的
  ValueError/AttributeError 等回錯誤幀、連線存活。
- `try/.../except WebSocketDisconnect: pass / finally:`（`app.py:226-232`）保證所有離開路徑
  都跑 `manager.disconnect(ws)` + `_unsubscribe_orphaned`。

**zombie 驗證：通過。** 非 WebSocketDisconnect 例外在 per-action try 內被吃掉並回幀；
若例外發生在 try 之外（如 receive_text / 錯誤幀的 send_text 自身丟非 WSDisconnect 例外），
仍會逃出 while 迴圈但被 `finally` 接住清理。`manager.active_connections` /
`subscriptions` 不會殘留殭屍，Shioaji 訂閱不會變孤兒。

**finally 不會二次拋出：通過。** `_unsubscribe_orphaned`（`app.py:132-144`）對每個 key
單獨 `try/except Exception` + log，無法把例外丟穿 finally。✓

### G6 — 容忍非法 JSON [`src/shioaji_server/app.py:155-162`]

`json.loads` 包 `try/except json.JSONDecodeError`，單條壞幀回錯誤幀並 `continue`，
不再永久斷線。符合計畫，正確。

### G7 — `stock_account is None` 防守 [`orders.py:38-45`、`account.py`]

**正確性 vs 計畫：達成且做了同類掃描（揪頭髮）。**
- `orders.py:41-42`：`update_status(api.stock_account)` 加 `is not None` 守衛，
  純期貨帳號不再 500；**仍回 `api.list_trades()`**（`orders.py:45`），不是空/500，
  期貨 trades 照常回傳。✓ 符合 reviewer 要求。
- `account.py:64-71`：`list_positions` 的 stock 路徑補 None 檢查回 400，與 futures 路徑對稱。✓
- `account.py:95-96`：`profit_loss` stock_account 為 None 回 400（而非 SDK 內 500）。✓

對稱性正確，bug class 端到端修完。

### G8 — check_session 單飛鎖 [`client.py:116-163`、`tests/test_health.py:70-91`]

**正確性 vs 計畫：達成。**
- `_probe_lock`（`client.py:37`）加入，鎖內 double-check 快取
  （`client.py:147-153`），第一個進鎖者打 `usage()` 寫快取，後續進鎖者讀到
  `_session_checked_at` 已更新 → 直接回 `_session_ok`，不再重複打 `usage()`。
- double-check 條件 `self._session_checked_at >= 0.0`：初始值 `-1.0`
  （`client.py:32`），首次必然 miss → 觸發探活，邏輯正確，無「初始就誤判已快取」。
- 鎖內仍包 `asyncio.wait_for(..., timeout=session_probe_timeout)`
  （`client.py:155-158`），後端 hang 時鎖最多被持有 `session_probe_timeout`（預設 5s），
  不會無限期卡住事件迴圈／其他等鎖者。✓ 符合 reviewer 對「鎖不可跨越無 timeout 的 hang」的要求。
- 並發下 `usage()` 恰好打一次：由 `test_concurrent_probes_are_single_flight`
  以 10 並發 + 0.05s 慢速 usage 驗證，並經變異測試證明非平凡通過。✓

---

## 問題清單

### Critical
無。

### Important
無。

### Minor

1. **[`src/shioaji_server/app.py:217`] per-action 的 `except Exception` 會吃到
   `WebSocketDisconnect`（它是 `Exception` 子類）。**
   若 client 在 handler 處理途中（例如成功幀 `ws.send_text` 當下）斷線，`send_text`
   會丟 `WebSocketDisconnect`，被這個寬 except 接住，接著 handler 又嘗試發一次錯誤幀
   → 第二次 `send_text` 在已死 socket 上再丟一次 → 逃出 while → 被外層
   `except WebSocketDisconnect: pass` 接住 → finally 仍正常清理。
   **正確性與清理不受影響（無殭屍）**，但會留下一筆帶完整 traceback 的
   `log.warning("WS %s failed ...")`，把正常斷線記成「失敗」造成 log 噪音，且多做一次
   無用 send。
   **建議修法：** 在寬 except 前先攔截 disconnect 讓它穿透：
   ```python
   except WebSocketDisconnect:
       raise
   except Exception as exc:  # noqa: BLE001
       ...
   ```
   或把 `except Exception` 收斂為預期的 `(ValueError, AttributeError, RuntimeError)`。

2. **[`src/shioaji_server/app.py`] G5/G6 缺單元測試。** G8 有並發測試、且做了變異驗證，
   但 G5 的「非 disconnect 例外不留殭屍 / finally 必清理」與 G6 的「壞 JSON 不斷線」
   目前只有 import smoke 與計畫裡的手動步驟，無自動化回歸。計畫對此標為 optional，
   故不阻擋；建議補一支以 fake WebSocket 驅動 `websocket_endpoint`、斷言
   `manager.disconnect` 被呼叫 + 壞幀後迴圈續行的測試，把 #1 的行為也一併釘住。

---

## 結論

依計畫四個缺口（G5 殭屍連線/孤兒訂閱、G6 壞 JSON 斷線、G7 純期貨帳號 500、
G8 並發探活配額浪費）皆正確關閉，對稱性與 timeout 邊界處理到位，測試經變異驗證有效。
無 Critical/Important 問題。兩項 Minor（disconnect 被寬 except 吃造成 log 噪音、
G5/G6 無自動化測試）可在後續批次順手收掉，不阻擋本批合併。

**裁決：APPROVED WITH NOTES**
