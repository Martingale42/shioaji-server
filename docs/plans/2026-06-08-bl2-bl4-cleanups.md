# Mechanical Cleanups (BL-2 + BL-4) Implementation Plan

> **For Claude:** Use superpowers:executing-plans or superpowers:orchestrator-driven-development to implement this plan.

**Goal:** Clear two zero-risk pre-existing lint/config nits — dead imports in a shioaji-server script (BL-2) and an invalid uv `exclude-newer` key in the nautilus_trader fork (BL-4).

**Architecture:** Two independent one-shot edits in two different repos. No shared state. This batch validates the new pipeline before the substantive work (BL-3, BL-1).

**Tech Stack:** Python, ruff, uv, TOML.

**Design reference:** `docs/plans/2026-06-08-backlog-fixes-design.md` (BL-2, BL-4). Source: `docs/BACKLOG.md`.

> ⚠️ **Cross-repo:** Task 1 is in `shioaji-server@main`; Task 2 is in `nautilus_trader@sinopac-adapter-clean`. Confirm `git branch --show-current` before each commit. Do NOT mix repos in one commit.

---

### Task 1: BL-2 — remove 5 dead imports in `scripts/fetch_single.py`

**Repo:** `shioaji-server` @ `main`

**Files:**
- Modify: `scripts/fetch_single.py`

**Implementation:**

`uv run ruff check scripts/fetch_single.py` reports 5 F401 unused imports: `BarSpecification`, `BarAggregation`, `PriceType`, `Venue`, `Currency`. Auto-fix them:

```bash
uv run ruff check --fix scripts/fetch_single.py
```

Then eyeball the diff to confirm only unused imports were removed (no logic touched) — the previewed fix collapses lines 16-19 to `from nautilus_trader.model.data import BarType`, `... identifiers import InstrumentId, Symbol`, `... objects import Price, Quantity`.

**Verification:**

Run: `uv run ruff check scripts/`
Expected: `All checks passed!` (the only previously-failing file was `fetch_single.py`).

**Commit:**
```bash
git add scripts/fetch_single.py
git commit -m "chore: 移除 fetch_single.py 5 個未使用 import（F401 清理）"
```

---

### Task 2: BL-4 — remove invalid `exclude-newer` from nautilus_trader pyproject

**Repo:** `nautilus_trader` @ `sinopac-adapter-clean` (`cd /home/cy/Code/MT5/nautilus_trader`, confirm branch)

**Files:**
- Modify: `pyproject.toml`

**Implementation:**

`pyproject.toml:121` has `exclude-newer = "3 days"` under `[tool.uv]`. uv does NOT support relative durations (it expects an RFC3339 timestamp), so every `uv run` prints `failed to parse "3 da" as year ...`. The key is meaningless for a fork tracking upstream — remove just that line, keeping `required-version` and the rest of `[tool.uv]` intact:

```toml
[tool.uv]
required-version = "==0.11.6"
# (exclude-newer line removed)
```

**Verification:**

Run: `cd /home/cy/Code/MT5/nautilus_trader && uv run --active --no-sync python -c "print('ok')"`
Expected: prints `ok` with NO `failed to parse "3 da"` warning above it.

**Commit:**
```bash
git add pyproject.toml
git commit -m "chore: 移除無效的 uv exclude-newer 鍵（相對語法 uv 不支援，僅產生警告）"
```

---

**Sequencing:** Independent — either order. Two separate commits in two separate repos.
