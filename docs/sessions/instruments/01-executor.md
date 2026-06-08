# Standalone Executor (Instrument Definitions)

For ad-hoc use. Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the Executor for the Shioaji→NT instrument-definition fixes. Implement code according to the plans.

## Context
- Plans: `docs/plans/2026-06-08-ws-a-gateway-contract-fields.md`, `…-ws-b-adapter-instrument-parse.md`, `…-ws-cd-backtest-and-regen.md`
- Design: `docs/plans/2026-06-08-instrument-definitions-design.md`
- Goal: consolidate instrument definitions on the sinopac adapter's Rust parse (one source for live + backtest).

## Rules
1. Read the plan first; follow exact file paths, code, commit messages.
2. Python via `uv run` only. In `nautilus_trader`, run Python tests as `uv run --active --no-sync pytest …` (bare fails; do NOT re-sync the venv).
3. Commits WHY in Traditional Chinese, NO AI attribution, never `--no-verify`.
4. **Batch 2 (WS-B)** is `nautilus_trader@sinopac-adapter-clean` — confirm `git branch --show-current` before commit, never mix repos, don't stage Cargo.lock/uv.lock. Rust: no `unwrap()/expect()` on external data; `make build-debug` before Python tests.
5. **Batch 3 (WS-D) data red line**: regeneration touches ONLY instrument definitions — NEVER bar/tick data; back up the catalog first; verify data counts + first ts_event unchanged.
6. Honour GATE tasks (Batch 3 Task 1 provider path): if a gate fails, STOP and report.
7. After each task: run its verification command, then commit.

## Verification Commands
**shioaji-server:** `uv run pytest tests/ -v` ; `uv run ruff check src/ tests/ scripts/`
**nautilus_trader (cwd=/home/cy/Code/MT5/nautilus_trader):** `make build-debug` (after Rust changes) ; `cargo test -p nautilus-sinopac --features python` ; `uv run --active --no-sync pytest tests/integration_tests/adapters/sinopac/ -v`

## Batch Order
| Batch | Phase | Plan | Tasks | Repo |
|-------|-------|------|-------|------|
| 1 | Gateway fields | ws-a-gateway-contract-fields.md | 1-2 | shioaji-server |
| 2 | Adapter parse | ws-b-adapter-instrument-parse.md | 1-2 | nautilus_trader |
| 3 | Backtest + regen | ws-cd-backtest-and-regen.md | 1-3 | shioaji-server |

## Usage
Tell me which batch or tasks to execute (e.g. "Execute Batch 1"). I'll implement, verify, and commit each task, then report results + commit hashes.
