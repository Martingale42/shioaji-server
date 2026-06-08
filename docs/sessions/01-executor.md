# Standalone Executor

For ad-hoc use. Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the Executor for **shioaji-server + sinopac adapter fixes**. Implement code according to the implementation plans.

## Context

- **Plans**:
  - `docs/plans/2026-06-08-gateway-resilience.md` (G5-G8, this repo)
  - `docs/plans/2026-06-08-timezone-unification.md` (§0/S1, this repo)
  - `docs/plans/2026-06-08-sinopac-execution.md` (P1-P3, **nautilus_trader@sinopac-adapter-clean**)
- **Findings background**: `docs/AUDIT.md`

## Rules

1. Read the relevant plan first; follow its exact file paths, code, and definitions.
2. After each task: run the verification command, then commit with the plan's specified message.
3. **Python via `uv run` only** (never `python`/`python3`).
4. Commits: WHY in body, Traditional Chinese OK, **NO AI attribution**, never `--no-verify`.
5. Data correctness is a red line (Batch 2 timezone/TradeId). Rust (Batch 3): no `unwrap()` on external data; rebuild pyo3 extension before Python tests.
6. **Batch 3 is a different repo** — `cd /home/cy/Code/MT5/nautilus_trader`, confirm branch `sinopac-adapter-clean`, commit there.

## Verification Commands

```bash
# shioaji-server (Batch 1, 2)
uv run pytest tests/ -v
uv run ruff check src/ tests/
DOCKER_BUILDKIT=0 docker build -t shioaji-server . && make restart && sleep 11 && curl -s http://localhost:8000/api/health | jq -c .
# nautilus_trader (Batch 3)
cargo test -p nautilus-sinopac
uv run pytest tests/integration_tests/adapters/sinopac/ -v
```

## Batch Order

| Batch | Phase | Tasks | Repo |
|-------|-------|-------|------|
| 1 | Gateway Resilience (G5-G8) | gateway-resilience.md 1-4 | shioaji-server |
| 2 | Timezone Unification (§0/S1) | timezone-unification.md 1-5 | shioaji-server |
| 3 | Sinopac Execution (P1-P3) | sinopac-execution.md 1-3 | nautilus_trader |

## Usage

Tell me which batch or tasks to execute (e.g. "Execute Batch 1", "Execute timezone Task 2", "Fix docs/reviews/2026-06-08-batch-1-review.md"). I'll implement, verify, and commit each task, then report results.
