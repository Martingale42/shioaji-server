# WS-A: Gateway Contract Fields + Fixes Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Make the gateway `/api/contracts/*` endpoints expose the authoritative Shioaji fields the instrument builder needs (`unit`, `multiplier`, `currency`, `underlying_code`) and fix the enum serialization bugs (`option_right`/`currency` currently emit `"OptionRight.Call"`/`"Currency.TWD"` instead of their values).

**Architecture:** Pure gateway change in `routes/contracts.py` (the `_*_to_dict` serializers) + the matching Pydantic response models in `models.py`. No logic beyond reading more fields off the Shioaji contract object and emitting enum `.value`.

**Tech Stack:** Python, FastAPI, Pydantic, Shioaji SDK.

**Design reference:** `docs/plans/2026-06-08-instrument-definitions-design.md` (WS-A). All work in `shioaji-server@main`.

**Project rules:** Python via `uv run`. Commits explain WHY in Traditional Chinese, NO AI attribution, never `--no-verify`.

**Verified facts (rely on these):**
- Shioaji `Contract` carries `unit` (lot), `multiplier:int`, `currency` (enum, `.value`="TWD"), `underlying_code`, `option_right` (enum, `.value`="C"/"P") — `.venv/.../shioaji/contracts.py:46-107`.
- `str(OptionRight.Call)` → `"OptionRight.Call"` (wrong); `OptionRight.Call.value` → `"C"`. Same for `Currency`.
- Current serializers omit `unit`/`multiplier`/`currency`/`underlying_code` and use `str(enum)` — `src/shioaji_server/routes/contracts.py:8-44`.

---

### Task 1: Fix enum serialization + add fields to the dict serializers

**Files:**
- Modify: `src/shioaji_server/routes/contracts.py`

**Implementation:**

In `_stock_to_dict` / `_futures_to_dict` / `_options_to_dict`, (a) emit enum `.value` not `str(enum)`, (b) add the authoritative fields. Use `getattr(contract, "x", default)` defensively (some fields may be absent on some contract subtypes):

```python
def _stock_to_dict(contract) -> dict:
    return {
        # ...existing code/symbol/name/exchange/category/limit_up/limit_down/reference/update_date...
        "day_trade": contract.day_trade.value,            # was str(...) -> "DayTrade.Yes"
        "currency": contract.currency.value,              # NEW -> "TWD"
        "unit": contract.unit,                            # NEW lot size
        "multiplier": contract.multiplier,                # NEW (0 for stocks)
    }

def _futures_to_dict(contract) -> dict:
    return {
        # ...existing...
        "currency": contract.currency.value,              # NEW
        "unit": contract.unit,                            # NEW
        "multiplier": contract.multiplier,                # NEW (e.g. 200 for TXF)
        "underlying_code": contract.underlying_code,      # NEW
    }

def _options_to_dict(contract) -> dict:
    d = _futures_to_dict(contract)
    d["strike_price"] = float(contract.strike_price)
    d["option_right"] = contract.option_right.value       # was str(...) -> "OptionRight.Call"; now "C"/"P"
    return d
```

Note: `day_trade` is also an enum — emit `.value` for consistency. Keep `exchange` as-is (already `str(contract.exchange)` — verify it yields a usable code; if it emits `"Exchange.TSE"`, switch to `.value` too).

**Tests:** `tests/test_contracts_serialization.py` — build a `MagicMock` contract with enum-like fields (or a real Shioaji `Stock(...)`/`Future(...)`/`Option(...)`) and assert the dict contains `unit`/`multiplier`/`currency` and that `option_right` is `"C"`/`"P"` (NOT `"OptionRight.Call"`), `currency` is `"TWD"`.

**Verification:**
Run: `uv run python -c "import shioaji_server.routes.contracts"`
Run: `uv run pytest tests/test_contracts_serialization.py -v` → pass.

**Commit:**
```bash
git add src/shioaji_server/routes/contracts.py tests/test_contracts_serialization.py
git commit -m "fix: gateway 合約序列化改用 enum .value 並補 unit/multiplier/currency/underlying_code

option_right/currency 原本用 str(enum) 送出 'OptionRight.Call'/'Currency.TWD'，下游
adapter 解析不到（選擇權全失敗）。改送 .value（C/P、TWD），並補回 instrument 建構
所需的權威欄位 unit(lot)/multiplier(契約乘數)/currency/underlying_code。"
```

---

### Task 2: Update the Pydantic response models

**Files:**
- Modify: `src/shioaji_server/models.py`

**Implementation:**

Add the new fields to `StockContract` / `FuturesContract` / `OptionsContract` Pydantic models so the OpenAPI schema and `response_model` validation match the enriched dicts. Fix the `OptionsContract.option_right` description from `"'Call' or 'Put'"` to `"'C' or 'P'"`.

```python
class StockContract(BaseModel):
    # ...existing...
    currency: str = Field(description="Quote currency, e.g. 'TWD'")
    unit: int = Field(description="Round-lot size (shares per lot, e.g. 1000)")
    multiplier: int = Field(description="Contract multiplier (0 for stocks)")

class FuturesContract(BaseModel):
    # ...existing...
    currency: str = Field(description="Quote currency, e.g. 'TWD'")
    unit: int = Field(description="Lot size (contracts, e.g. 1)")
    multiplier: int = Field(description="Contract multiplier in TWD per point, e.g. 200 for TXF")
    underlying_code: str = Field(description="Underlying index/stock code")

class OptionsContract(BaseModel):
    # ...existing...
    option_right: str = Field(description="'C' (Call) or 'P' (Put)")
    currency: str = Field(...)
    unit: int = Field(...)
    multiplier: int = Field(...)
    underlying_code: str = Field(...)
```

Match the field types to what Shioaji emits (`unit` is `int|float` in Shioaji — use `float` if fractional units are possible; default to `int` and widen only if a real contract shows otherwise).

**Verification:**
Run: `uv run pytest tests/ -v` → pass (no regressions).
Run: `uv run ruff check src/ tests/` → clean.
Manual (if gateway can run with creds): `curl -s localhost:8000/api/contracts/options | jq '.[0]'` shows `option_right` `"C"`/`"P"` + `multiplier`/`unit`/`currency`.

**Commit:**
```bash
git add src/shioaji_server/models.py
git commit -m "feat: 合約 response model 補 unit/multiplier/currency/underlying_code 並更正 option_right 描述"
```

---

**Sequencing:** Task 1 → 2. This WS must land before WS-B's adapter parse can consume the new fields end-to-end (WS-B unit tests use fixtures, but integration needs this gateway).
