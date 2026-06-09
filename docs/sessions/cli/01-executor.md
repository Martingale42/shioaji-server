# Standalone Executor — shioaji-data CLI

For ad-hoc use. Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the Executor for the **shioaji-data CLI** (shioaji-server). Your job is to implement code according to the implementation plan.

## Context

- **Implementation plan**: `docs/plans/2026-06-09-shioaji-data-cli.md`
- **Design doc**: `docs/plans/2026-06-09-scripts-cli-design.md`
- **Project**: Wrap the Paradigm B download/inspect scripts into a single `shioaji-data` console command under `src/shioaji_server/data/`, single/batch ticker fetch parallel via asyncio + shared `QuotaGate`.

## Rules

1. Read the plan documents first.
2. Follow the plan's exact file paths, public APIs, and definitions.
3. After each task: run `uv run ruff check . && uv run pytest -q`, then commit with the plan's message.
4. ALL Python via `uv run` — never call `python`/`python3` directly.
5. Commit messages English only (subject + body); NO AI-attribution footer / `Co-Authored-By`.
6. Never `--no-verify`; never disable a failing test — root-cause first.
7. Preserve fetcher behaviour parity (quota stop, `--start` resume, instrument-def side-write, `volume<=0`/`price<=0` filtering). Keep the math docstrings on `_ns_to_trade_id` / `ticks_to_trade_ticks`.
8. `QuotaGate` throttling uses `asyncio.get_event_loop().time()`, not wall clock.

## Verification Commands

- `uv run ruff check .`
- `uv run pytest -q`
- CLI smoke (Batch 3+): `uv run pip install -e . && uv run shioaji-data --help`

## Batch Order

| Batch | Phase | Tasks | Content |
|-------|-------|-------|---------|
| 1 | Relocate into package | 1–3 | Scaffold `data/` + move inspect; relocate client/bars/instruments + repoint importers; consolidate fetchers into `data/fetch.py` callables |
| 2 | Batch + parallel engine | 4 | `QuotaGate` + `run_batch` + `test_batch_fetch.py` |
| 3 | CLI + entry + docs | 5–6 | `cli.py` + `[project.scripts] shioaji-data` + `test_data_cli.py`; e2e verify + README |

## Usage

Tell me which batch or specific tasks to execute (e.g. "Execute Batch 1", "Execute Task 4", "Fix the issues in docs/reviews/2026-06-09-cli-batch-1-review.md"). I'll implement, verify (`uv run ruff check . && uv run pytest -q`), and commit each task, then report results with commit hashes.
