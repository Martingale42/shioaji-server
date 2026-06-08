# WS-B: Adapter Instrument Parse Fixes Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:orchestrator-driven-development to implement this plan.
> **⚠️ Different repo:** All work in `/home/cy/Code/MT5/nautilus_trader`, branch `sinopac-adapter-clean`. Confirm `git branch --show-current` before committing. Do NOT touch shioaji-server. Do NOT stage `Cargo.lock`/`uv.lock` unless deps genuinely change.

**Goal:** Fix the sinopac adapter's Rust instrument parser so it uses Shioaji's authoritative `multiplier`/`unit`/`currency` (instead of hardcoded tables) and correctly parses `option_right` (currently `"OptionRight.Call"` vs expected `"Call"` → every option `bail!`s). Keep the hardcoded tables only as a defensive fallback.

**Architecture:** Add the new fields to the gateway contract structs (`http/models.rs`), then update the three `parse_*_to_*` functions in `http/parse.rs` to prefer the real values. Rebuild the pyo3 extension. The TWSE tick-tier logic (`common/tick_size.rs`) is already correct — leave it.

**Tech Stack:** Rust (serde, pyo3), maturin/cargo.

**Design reference:** `docs/plans/2026-06-08-instrument-definitions-design.md` (WS-B). **Depends on WS-A** (gateway now sends the new fields + `option_right` as "C"/"P").

**Project rules:** Rust no `unwrap()`/`expect()` on external data. Rebuild via `make build-debug` before Python tests. nautilus_trader Python tests: `uv run --active --no-sync pytest …` (bare fails; don't re-sync venv). Commits WHY in Traditional Chinese, NO AI attribution, never `--no-verify`.

**Verified facts:**
- `http/models.rs` structs `StockContract`/`FuturesContract`/`OptionsContract` currently lack `unit`/`multiplier`/`currency`/`underlying_code`.
- `http/parse.rs`: `parse_stock_to_equity` (Equity, tick via `twse_stock_tick_size` ✅), `parse_futures_to_contract` (uses `futures_multiplier(category)` ❌), `parse_options_to_contract` (uses `options_multiplier(category)` ❌; `option_right` match `"Call"/"Put"` ❌).
- Hardcoded helpers: `common/instrument.rs:34,49` (`futures_multiplier`/`options_multiplier`), lot consts `STOCK_LOT_SIZE=1000`/`CONTRACT_LOT_SIZE=1`.

---

### Task 1: Add the new fields to the gateway contract structs

**Files:**
- Modify: `crates/adapters/sinopac/src/http/models.rs`

**Implementation:**

Add to `StockContract`, `FuturesContract`, `OptionsContract` (use `#[serde(default)]` so older/partial gateway responses still deserialize — no `unwrap` on missing external data):

```rust
#[serde(default)]
pub unit: f64,            // round-lot / contract unit
#[serde(default)]
pub multiplier: i64,      // contract multiplier (0 for stocks)
#[serde(default)]
pub currency: String,     // e.g. "TWD"
// futures/options only:
#[serde(default)]
pub underlying_code: String,
```

(Pick `unit: f64` to tolerate Shioaji's `int|float`. `multiplier` is `int` in Shioaji.)

**Verification:** `cargo check -p nautilus-sinopac` compiles.

**Commit:**
```bash
git add crates/adapters/sinopac/src/http/models.rs
git commit -m "feat(sinopac): 合約 struct 加 unit/multiplier/currency/underlying_code（serde default 容缺）"
```

---

### Task 2: Use authoritative values + fix option_right in the parsers

**Files:**
- Modify: `crates/adapters/sinopac/src/http/parse.rs`
- (maybe) Modify: `crates/adapters/sinopac/src/common/instrument.rs` (downgrade hardcoded tables to fallback / remove dead `extract_root_symbol`)

**Implementation:**

1. **Equity** (`parse_stock_to_equity`): use `contract.currency` (fallback "TWD") and `contract.unit` for `lot_size` (fallback `STOCK_LOT_SIZE`):
   ```rust
   let currency = parse_currency_or_twd(&contract.currency);
   let lot_size = Some(Quantity::new(if contract.unit > 0.0 { contract.unit } else { STOCK_LOT_SIZE }, SIZE_PRECISION));
   ```
2. **Futures** (`parse_futures_to_contract`): prefer Shioaji `multiplier`/`unit`; keep `futures_multiplier` only as fallback when `multiplier == 0`; set `underlying` from `underlying_code`:
   ```rust
   let multiplier_val = if contract.multiplier > 0 { contract.multiplier as f64 } else { futures_multiplier(root_symbol) };
   let multiplier = Quantity::new(multiplier_val, 0);
   let lot_size = Quantity::new(if contract.unit > 0.0 { contract.unit } else { CONTRACT_LOT_SIZE }, SIZE_PRECISION);
   let underlying = if contract.underlying_code.is_empty() { Ustr::from(root_symbol) } else { Ustr::from(contract.underlying_code.as_str()) };
   ```
   (tick stays `futures_tick_size`; currency from `contract.currency` fallback TWD.)
3. **Options** (`parse_options_to_contract`): same multiplier/unit/currency/underlying treatment, AND fix `option_right` to match WS-A's `"C"`/`"P"`:
   ```rust
   let option_kind = match contract.option_right.as_str() {
       "C" => OptionKind::Call,
       "P" => OptionKind::Put,
       other => anyhow::bail!("Unknown option_right {other:?} (expected 'C'/'P')"),
   };
   ```
4. Add a small `parse_currency_or_twd(&str) -> Currency` helper (TWD on empty/unknown — no panic).
5. **Dead code**: either remove `extract_root_symbol` (unused) or wire it; pick one to remove the inconsistency. Keep `futures_multiplier`/`options_multiplier` as documented fallback.

**Tests (Required — financial correctness):** update/extend the parse tests in `parse.rs`:
- Option with `option_right="C"` → `OptionKind::Call` (and `"P"` → Put); an unknown value still `bail!`s.
- Futures/option with a non-zero `multiplier` in the contract → that value is used (NOT the hardcoded table); with `multiplier=0` → fallback table.
- Stock/futures with `unit` set → `lot_size` uses it.

**Verification:**
```bash
cd /home/cy/Code/MT5/nautilus_trader
make build-debug                                  # rebuild pyo3 (large, ~4 min — be patient)
cargo test -p nautilus-sinopac --features python  # all pass incl. new option_right/multiplier/unit cases
```

**Commit:**
```bash
git add crates/adapters/sinopac/src/http/parse.rs crates/adapters/sinopac/src/common/instrument.rs
git commit -m "fix(sinopac): 用 Shioaji 權威 multiplier/unit/currency 取代硬編碼，並修 option_right 解析

成交事件外：合約 parse 原本硬編碼 futures/options multiplier（值脆弱、keyed on category）
與 lot_size，且 option_right 比對 'Call'/'Put' 但 gateway 送 'OptionRight.Call' → 選擇權
全部 bail!。改用合約上的 multiplier/unit/currency（硬編碼降為 fallback），option_right 對齊
gateway 的 'C'/'P'，underlying 改用 underlying_code。"
```

---

**Sequencing:** Task 1 → 2 (rebuild in Task 2). Run the full sinopac suite after Task 2. WS-C/WS-D depend on this rebuild.
