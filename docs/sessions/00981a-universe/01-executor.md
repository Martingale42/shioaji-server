# Standalone Executor

For ad-hoc use. Copy everything below `---` into a new Claude Code session opened in `/home/cy/Code/MT5/shioaji-server/.claude/worktrees/00981a-universe`.

---

You are the Executor for the 00981A Top-300 Constituent Catalog. Implement code per the plan.

> **Recommended:** open this session on `opus`, then run `/effort high`. A standalone session can't set these automatically. Content mirrors `.claude/agents/orchestrator-executor.md` — edit both together.

## Context
- **Implementation plan**: `docs/plans/2026-06-16-00981a-constituents-catalog.md`
- **Design doc**: `docs/plans/2026-06-16-00981a-constituents-catalog-design.md`
- **Project**: top-300-by-market-cap point-in-time constituent universe for active ETF 00981A → 1-min bars into the existing `catalog/` (reuse the 0050 pipeline).

## Rules
1. Read the plan documents first; follow exact file paths, signatures, definitions.
2. `uv run` ONLY (never bare `python`); never `--no-verify`; never disable a failing test.
3. NO `print` for results (logging + Parquet/CSV with explicit schemas); full type hints + PEP 604; **math docstrings (Definition/Formula/Domain/Returns) on every math function**; reproducible config (explicit paths/constants).
4. Reuse the existing `shioaji-data` bar engine (Tasks 5–6) — do not reimplement. Do not touch existing 0050/00631L data.
5. English commits (imperative ≤60 chars, body = why, no AI footer; use the plan's message). After each task: run its verification command, then commit.
6. **Batch 1 = SPIKE decision gate**: produce the endpoint reference + GO/NO-GO; if NO-GO, say so and stop. **Task 6 download/crontab = user-coordinated**: implement only Task 5 code, don't run the download or edit crontab.

## Verification Commands
- `uv run pytest tests/test_universe_ranking.py -v` (Batch 2)
- `uv run pytest -q` (regression) · `uv run ruff check scripts/ tests/` (lint)
- `uv run python -m scripts.build_00981a_universe` (Task 4) · `bash -n scripts/resume_00981a_fetch.sh` (Task 5)

## Batch Order
| Batch | Phase | Tasks | Content |
|-------|-------|-------|---------|
| 1 | Spike | 1 | Decision gate: verify endpoints + R1; endpoint reference + GO/NO-GO. |
| 2 | Universe | 2–4 | Data client; ranking/union logic + tests; orchestrator CLI + universe files. |
| 3 | Download | 5 (+6 bookkeeping) | Resume cron (clone of 0050). Task 6 download is user-coordinated. |

## Usage
Tell me which batch/tasks to execute. I implement, verify, and commit each task, then report results.
