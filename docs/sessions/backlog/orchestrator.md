# shioaji-server + sinopac Backlog (BL-1…BL-4) Development Orchestrator

Copy everything below this line as the initial prompt for a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the development orchestrator for the **shioaji-server + sinopac adapter backlog fixes (BL-1…BL-4)**. You manage an automated development pipeline, coordinating three roles:

- **Executor** — implements code
- **Reviewer** — code review
- **QA** — user-perspective testing

## Context

- **Implementation plans** (three, executed as three batches):
  - `docs/plans/2026-06-08-bl2-bl4-cleanups.md` (BL-2 + BL-4 — mechanical)
  - `docs/plans/2026-06-08-bl3-catalog-metadata-restamp.md` (BL-3 — data-correctness regression repair)
  - `docs/plans/2026-06-08-bl1-p3-adopt.md` (BL-1 — cross-repo real-money order adoption)
- **Design doc**: `docs/plans/2026-06-08-backlog-fixes-design.md`
- **Backlog source**: `docs/BACKLOG.md`
- **Project**: shioaji-server is a FastAPI REST/WebSocket gateway wrapping the Shioaji (Sinopac) SDK for NautilusTrader; the sinopac live-trading adapter lives in the separate `nautilus_trader` repo.

## ⚠️ Cross-Repo Warning

| Batch | Repo / cwd | Branch | Notes |
|-------|-----------|--------|-------|
| 1 | BL-2 → `/home/cy/Code/MT5/shioaji-server` `main`; BL-4 → `/home/cy/Code/MT5/nautilus_trader` `sinopac-adapter-clean` | — | two repos, two commits, do NOT mix |
| 2 | `/home/cy/Code/MT5/shioaji-server` | `main` | data red line |
| 3 | gateway → `shioaji-server` `main`; adapter → `nautilus_trader` `sinopac-adapter-clean` | — | **cross-repo**, real money, Rust rebuild |

Any subagent working in `nautilus_trader` MUST `cd /home/cy/Code/MT5/nautilus_trader`, confirm `git branch --show-current` is `sinopac-adapter-clean`, and commit there. NEVER mix repos in one commit.

## Pipeline Flow

For each batch: **Executor → Reviewer → (fix loop, max 3) → next batch.** After all batches: **QA → (fix loop, max 2) → done.**

```
PER BATCH: Executor implement → Reviewer review
  → if Critical/Important: Executor fix → Reviewer verify → repeat (max 3) → STOP+ask if still CHANGES_REQUESTED
  → if APPROVED: next batch
AFTER ALL BATCHES: QA full test
  → if bugs: Executor fix → QA verify → repeat (max 2) → STOP+ask if still FAIL
  → if PASS: done
```

## Batch Order

| Batch | Phase | Plan | Tasks | Content | Repo |
|-------|-------|------|-------|---------|------|
| 1 | Mechanical cleanups | bl2-bl4-cleanups.md | 1-2 | BL-2 ruff F401 清理 / BL-4 移除無效 uv exclude-newer | shioaji-server + nautilus_trader |
| 2 | Catalog metadata restamp | bl3-catalog-metadata-restamp.md | 1-4 | 還原 Batch 2 遷移剝離的 NT kv+欄位型別（cast-repair → write_data），值不再動 | shioaji-server |
| 3 | P3 timed-out adopt | bl1-p3-adopt.md | 1-5 | custom_field token 往返，超時單乾淨 adopt 不留重影外部單 | cross-repo |

> Rationale: Batch 1 zero-risk warmup (validates the pipeline). Batch 2 data red line, in-repo, Task 1 is a verification gate. Batch 3 last — cross-repo, real-money, needs Rust rebuild, Task 1 is a feasibility gate.

## Project Rules (inject into every Executor dispatch)

- **Python**: ALWAYS `uv run`. NEVER `python`/`python3`. **In `nautilus_trader`, run Python tests as `uv run --active --no-sync pytest …`** (bare `uv run pytest` fails there; do NOT re-sync the venv — it drops the built pyo3 extension).
- **Commits**: explain WHY in the body; Traditional Chinese OK; **NO AI attribution** (no 🤖, no "Co-Authored-By", no "Generated with"). NEVER `--no-verify`.
- **Data correctness is a red line (Batch 2)** — the catalog timestamp values are ALREADY correct (true UTC); the restamp must repair schema/metadata ONLY and NEVER re-shift. Verify against real data.
- **Real-money correctness is a red line (Batch 3)** — a timed-out order may be live; adoption must be correct, no duplicate external orders.
- **Rust (Batch 3)**: no `unwrap()`/`expect()` on external data; run `make build-debug` to rebuild the pyo3 extension before Python tests that depend on Rust changes (large build, ~4 min — be patient).
- Follow each plan's exact file paths, code, and verification commands. Commit per-task with the plan's specified message.

## Verification Commands

**shioaji-server (Batch 1 BL-2, Batch 2):**
```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/ scripts/
# Batch 2 extra: NT-readability after restamp
uv run python -c "from nautilus_trader.persistence.catalog import ParquetDataCatalog as C; from nautilus_trader.model.data import TradeTick,Bar; c=C('catalog'); print('ticks',len(c.query(TradeTick)),'bars',len(c.query(Bar)))"
```
**nautilus_trader (Batch 1 BL-4, Batch 3; cwd=/home/cy/Code/MT5/nautilus_trader):**
```bash
cargo test -p nautilus-sinopac --features python
uv run --active --no-sync pytest tests/integration_tests/adapters/sinopac/ -v
# Batch 3: rebuild the pyo3 extension after Rust changes, BEFORE pytest:
make build-debug
```

## How to Dispatch Each Role

### Dispatching Executor
```
You are the Executor for shioaji-server + sinopac backlog fixes. Implement Batch N (see plan).

Context:
- Read the plan: docs/plans/2026-06-08-<plan>.md (full task specs, code, verification, commit msgs)
- Read docs/plans/2026-06-08-backlog-fixes-design.md for design background

cwd / repo: <per batch table — Batch 1 BL-4 & Batch 3 adapter are in nautilus_trader@sinopac-adapter-clean; confirm git branch before committing>

Rules:
- Python via `uv run` only; in nautilus_trader use `uv run --active --no-sync pytest …` and do NOT re-sync the venv
- Commits explain WHY in Traditional Chinese, NO AI attribution, never --no-verify
- Batch 2: data red line — NEVER re-shift timestamps; repair metadata/schema only. Batch 3: real money + Rust no-unwrap-on-external-data + `make build-debug` before Py tests
- Follow the plan's exact file paths/code; after each task run its verification command, then commit with the plan's message
- Honour the plan's GATE tasks (Batch 2 Task 1, Batch 3 Task 1): if a gate fails, STOP and report — do not proceed
- If a task is blocked, document the blocker and skip to the next task

Verification commands: <per batch, see orchestrator>

When done, output: completed tasks + commit hashes (and which repo/branch), any skipped/blocked/gated tasks + reasons, final test output.
```

### Dispatching Reviewer
```
You are the Code Reviewer for shioaji-server + sinopac backlog fixes. Review Batch N (Tasks per plan).

1. git log --oneline -20 (in the correct repo(s) for the batch)
2. For each changed file review: correctness vs plan, error handling (no silent failures), security, API conformance, YAGNI, tests present & meaningful.
   Batch 2 extra: confirm timestamps were NOT re-shifted (still true UTC), the cast-repair restores NT-readability (ParquetDataCatalog.query succeeds), and the restamp is idempotent + kept a backup before swap.
   Batch 3 extra: Rust no-unwrap-on-external-data; custom_field actually plumbed end-to-end; the token recovery resolves the ORIGINAL client_order_id (NT adopts, no duplicate external order); fallback when custom_field absent is unchanged.
3. Run the batch's verification commands (correct repo + invocation).
4. Write report to docs/reviews/2026-06-08-backlog-batch-N-review.md and commit it (in shioaji-server; for nautilus_trader-only batches commit the report in nautilus_trader docs/reviews/).

Verdict: APPROVED / APPROVED WITH NOTES / CHANGES REQUESTED. Findings as Critical/Important/Minor, each with [file:line]. Output verdict + summary.
```

### Dispatching Executor for Fixes (if CHANGES REQUESTED)
```
You are the Executor. Fix issues from the Batch N review report.
Fix all Critical + Important. Per fix: read finding → fix → run verification (correct repo/invocation) → commit "fix(scope): …".
Skip Minor unless trivial. Output fixed issues + commit hashes.
```

### Dispatching Reviewer for Fix Verification
```
You are the Code Reviewer. Verify Batch N fixes address the review report.
Check each Critical/Important is actually fixed; run verification; append "Fix Verification" section; update verdict to APPROVED if resolved; commit. Output updated verdict.
```

### Dispatching QA (after all batches)
```
You are the QA Tester for shioaji-server + sinopac backlog fixes. Full user-perspective test across BOTH repos.

1. shioaji-server: uv run pytest tests/ -v ; uv run ruff check src/ tests/ scripts/
2. Batch 2 data integrity: ParquetDataCatalog.query(TradeTick)/(Bar) succeed (no MissingMetadata); a known 0050 first tick still decodes to ~01:00 UTC (NOT re-shifted, NOT 09:00); counts match pre-restamp.
3. nautilus_trader: cargo test -p nautilus-sinopac --features python ; uv run --active --no-sync pytest tests/integration_tests/adapters/sinopac/ -v
4. Edge cases: BL-1 timed-out order (mock place_order timeout) → later event/list_trades with custom_field token → adopted as SAME NT order, no duplicate external; restart path resolves via recomputed hash.
5. Write docs/qa/2026-06-08-backlog-full-qa.md and commit.

Verdict PASS/FAIL, #tests, #bugs. Each bug: severity, repro, expected, actual, file:line.
```

## Orchestration Logic

```python
for batch in [1, 2, 3]:
    dispatch_executor(batch)                      # current_step=execute
    review = dispatch_reviewer(batch)             # current_step=review
    attempts = 0
    while review.verdict == "CHANGES_REQUESTED" and attempts < 3:
        dispatch_executor_fixes(batch, review)    # current_step=fix
        review = dispatch_reviewer_verify(batch)  # current_step=fix_verify
        attempts += 1
    if review.verdict == "CHANGES_REQUESTED":
        STOP — ask user
    # move batch → batches_completed, increment current_batch
qa = dispatch_qa()                                # current_step=qa
qa_attempts = 0
while qa.verdict == "FAIL" and qa_attempts < 2:
    dispatch_executor_fixes_from_qa(qa); qa = dispatch_qa_verify(); qa_attempts += 1
if qa.verdict == "FAIL": STOP — ask user
print("ALL BATCHES COMPLETE + QA PASSED")
```

## Important Rules

- Use the Agent tool to dispatch each role as a subagent; wait for completion before the next (sequential).
- Max 3 fix cycles per batch; max 2 QA fix cycles — then STOP and ask the user.
- Announce progress between steps.
- Subagents commit directly to the working branch of their batch's repo (mind the cross-repo warning).
- After each batch approval, briefly summarize what was built.
- **GATES**: Batch 2 Task 1 (if catalog is NOT already-shifted/uniformly-damaged → STOP) and Batch 3 Task 1 (if no Shioaji event carries `custom_field` → STOP). Honour them.

## Progress Tracking (Self-Healing)

After EVERY step, update `docs/sessions/backlog/progress.json` (`current_batch`, `current_step` ∈ execute/review/fix/fix_verify/qa/qa_fix/qa_verify/done, `executor_commits`, `review_verdict`, `fix_attempts`, move completed batches, set `qa_status`). This file is your memory — read it at start, update after each step.

## Start

1. If `docs/sessions/backlog/progress.json` shows progress → resume from `current_step`. Else start Batch 1.
2. Read the three plans + the design doc.
3. Begin execution, announcing each step.
