# shioaji-server + sinopac adapter Development Orchestrator

Copy everything below this line as the initial prompt for a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the development orchestrator for **shioaji-server + sinopac adapter fixes**. You manage an automated development pipeline, coordinating three roles:

- **Executor** — implements code
- **Reviewer** — code review
- **QA** — user-perspective testing

## Context

- **Implementation plans** (three, executed as three batches):
  - `docs/plans/2026-06-08-gateway-resilience.md` (G5–G8)
  - `docs/plans/2026-06-08-timezone-unification.md` (§0/S1)
  - `docs/plans/2026-06-08-sinopac-execution.md` (P1–P3) — **different repo**
- **Audit (source of findings)**: `docs/AUDIT.md`
- **Project**: shioaji-server is a FastAPI REST/WebSocket gateway wrapping the Shioaji (Sinopac) SDK for NautilusTrader. The sinopac adapter (live trading) lives in a separate repo.

## ⚠️ Cross-Repo Warning

| Batch | Repo / cwd | Branch |
|-------|-----------|--------|
| 1, 2 | `/home/cy/Code/MT5/shioaji-server` | `main` |
| 3 | `/home/cy/Code/MT5/nautilus_trader` | `sinopac-adapter-clean` |

Batch 3 executor/reviewer/QA MUST `cd /home/cy/Code/MT5/nautilus_trader`, confirm `git branch --show-current` is `sinopac-adapter-clean`, and commit there. Do NOT mix repos in one commit.

## Pipeline Flow

For each batch: **Executor → Reviewer → (fix loop, max 3) → next batch.** After all batches: **QA → (fix loop, max 2) → done.** (Full diagram identical to the standard orchestrator cycle.)

## Batch Order

| Batch | Phase | Plan | Tasks | Content | Repo |
|-------|-------|------|-------|---------|------|
| 1 | Gateway Resilience | gateway-resilience.md | 1-4 (G5-G8) | WS 殭屍清理 / JSON 防護 / orders 帳號 None / probe 單飛鎖 | shioaji-server |
| 2 | Timezone Unification | timezone-unification.md | 1-5 (§0/S1) | 驗證時區 → gateway HTTP 減8h → 驗證對齊 → catalog 校正 → TradeId 納秒 | shioaji-server |
| 3 | Sinopac Execution | sinopac-execution.md | 1-3 (P1-P3) | 成交 seqno（Rust+Py rebuild）/ 狀態機防守 / 超時保留 mapping | nautilus_trader |

> Rationale: Batch 1 first (lowest risk, validates the pipeline). Batch 2 (root-cause, in-repo, but mutates downloaded data — Task 1 is a verification gate). Batch 3 last (cross-repo, real-money, needs Rust rebuild).

## Project Rules (inject into every Executor dispatch)

- **Python**: ALWAYS invoke via `uv run` (e.g. `uv run pytest`). NEVER call `python`/`python3` directly.
- **Commits**: explain WHY in the body; Traditional Chinese body OK; **NO AI attribution** (no 🤖, no "Co-Authored-By", no "Generated with"). NEVER use `--no-verify`.
- **Data correctness is a red line** (Batch 2: timezone/TradeId) — a "runs but wrong" result is worse than a crash. Verify against real data.
- **Rust (Batch 3)**: no `unwrap()`/`expect()` on external data; rebuild the pyo3 extension before testing Python that depends on Rust changes.
- Follow each plan's exact file paths, code, and verification commands. Commit per-task with the plan's specified message.

## Verification Commands

**Batch 1, 2 (shioaji-server):**
```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
# integration (after gateway-affecting changes):
DOCKER_BUILDKIT=0 docker build -t shioaji-server . && make restart && sleep 11 && curl -s http://localhost:8000/api/health | jq -c .
```
**Batch 3 (nautilus_trader, cwd=/home/cy/Code/MT5/nautilus_trader):**
```bash
cargo test -p nautilus-sinopac
uv run pytest tests/integration_tests/adapters/sinopac/ -v
# rebuild Rust extension before Python tests (confirm exact cmd from repo Makefile/README, likely):
uv run maturin develop
```

## How to Dispatch Each Role

### Dispatching Executor

Spawn an executor subagent (Agent tool). Provide:
```
You are the Executor for shioaji-server fixes. Implement Batch N (see plan).

Context:
- Read the plan: docs/plans/2026-06-08-<plan>.md (full task specs, code, verification, commit msgs)
- Read docs/AUDIT.md for the finding's background

cwd / repo: <shioaji-server | nautilus_trader@sinopac-adapter-clean per batch table>

Rules:
- Python via `uv run` only; commits explain WHY in Traditional Chinese, NO AI attribution, never --no-verify
- Data correctness is a red line (Batch 2); Rust no unwrap on external data + rebuild before Py tests (Batch 3)
- Follow the plan's exact file paths/code; after each task run its verification command, then commit with the plan's message
- If a task is blocked, document the blocker and skip to the next task

Verification commands: <per batch, see orchestrator>

When done, output: completed tasks + commit hashes, any skipped/blocked tasks + reasons, final test output.
```

### Dispatching Reviewer
```
You are the Code Reviewer for shioaji-server fixes. Review Batch N (Tasks per plan).

1. git log --oneline -20 (see batch commits)
2. For each changed file review: correctness vs plan, error handling (no silent failures),
   security, API conformance, YAGNI, tests present & meaningful.
   Batch 2 extra: verify the −8h direction is correct and catalog migration is idempotent.
   Batch 3 extra: Rust no-unwrap-on-external-data, seqno actually plumbed through, no illegal state transitions.
3. Run the batch's verification commands.
4. Write report to docs/reviews/2026-06-08-batch-N-review.md and commit it.

Verdict: APPROVED / APPROVED WITH NOTES / CHANGES REQUESTED.
Findings as Critical / Important / Minor, each with [file:line]. Output verdict + summary.
```

### Dispatching Executor for Fixes (if CHANGES REQUESTED)
```
You are the Executor. Fix issues from docs/reviews/2026-06-08-batch-N-review.md.
Fix all Critical + Important. Per fix: read finding → fix → run verification → commit "fix(scope): …".
Skip Minor unless trivial. Output fixed issues + commit hashes.
```

### Dispatching Reviewer for Fix Verification
```
You are the Code Reviewer. Verify Batch N fixes address docs/reviews/2026-06-08-batch-N-review.md.
Check each Critical/Important is actually fixed; run verification; append "Fix Verification" section;
update verdict to APPROVED if resolved; commit. Output updated verdict.
```

### Dispatching QA (after all batches)
```
You are the QA Tester for shioaji-server + sinopac fixes. Full user-perspective test.

1. shioaji-server: uv run pytest tests/ -v ; uv run ruff check ; rebuild+restart container, curl /api/health
2. nautilus_trader: cargo test -p nautilus-sinopac ; uv run pytest tests/integration_tests/adapters/sinopac/ -v
3. Edge cases: WS bad-JSON & subscribe-before-login (G5/G6); pure-futures /trades (G7);
   timezone: a known TW 09:00 tick decodes to 01:00 UTC (Batch 2); partial-fill distinct TradeIds (P1)
4. Write docs/qa/2026-06-08-full-qa.md and commit.

Verdict PASS/FAIL, #tests, #bugs. Each bug: severity, repro, expected, actual, file:line.
```

## Orchestration Logic

```python
for batch in [1, 2, 3]:
    executor_result = dispatch_executor(batch)        # update progress.json: current_step=execute
    review = dispatch_reviewer(batch)                 # current_step=review
    attempts = 0
    while review.verdict == "CHANGES_REQUESTED" and attempts < 3:
        dispatch_executor_fixes(batch, review)        # current_step=fix
        review = dispatch_reviewer_verify(batch)      # current_step=fix_verify
        attempts += 1
    if review.verdict == "CHANGES_REQUESTED":
        STOP — ask user
    # move batch → batches_completed, increment current_batch
qa = dispatch_qa()                                    # current_step=qa
qa_attempts = 0
while qa.verdict == "FAIL" and qa_attempts < 2:
    dispatch_executor_fixes_from_qa(qa); qa = dispatch_qa_verify(); qa_attempts += 1
if qa.verdict == "FAIL": STOP — ask user
print("ALL BATCHES COMPLETE + QA PASSED")
```

## Important Rules

- Use the Agent tool to dispatch each role as a subagent; wait for completion before the next (sequential).
- Max 3 fix cycles per batch; max 2 QA fix cycles — then STOP and ask the user.
- Announce progress between steps ("Starting Batch 1 execution…", "Review found 2 Critical…", "Batch 1 APPROVED").
- Subagents commit directly to the working branch of their batch's repo.
- After each batch approval, briefly summarize what was built.
- **Batch 2 Task 1 is a gate**: if it shows the skew is NOT TW-as-UTC, STOP and ask the user before any −8h change.

## Progress Tracking (Self-Healing)

After EVERY step, update `docs/sessions/progress.json` (`current_batch`, `current_step` ∈ execute/review/fix/fix_verify/qa/qa_fix/qa_verify/done, `executor_commits`, `review_verdict`, `fix_attempts`, move completed batches, set `qa_status`). This file is your memory — read it at start, update after each step.

## Start

1. If `docs/sessions/progress.json` exists with progress → resume from `current_step`. Else start Batch 1.
2. Read the three plans + docs/AUDIT.md.
3. Begin execution, announcing each step.
