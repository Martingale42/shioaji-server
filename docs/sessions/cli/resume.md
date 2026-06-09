# Resume Orchestrator (Self-Healing) — shioaji-data CLI

Use this prompt when the orchestrator session's context exploded or was interrupted.
Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the development orchestrator for the **shioaji-data CLI** feature in `shioaji-server`. **Your previous session was interrupted.** Resume from where it left off.

## Recovery Steps

1. **Read progress state:** `docs/sessions/cli/progress.json`
2. **Read the orchestrator rules:** `docs/sessions/cli/orchestrator.md` — full pipeline flow, dispatch templates, orchestration logic. Follow those rules exactly.
3. **Read the plans** (for context):
   - `docs/plans/2026-06-09-shioaji-data-cli.md`
   - `docs/plans/2026-06-09-scripts-cli-design.md`
4. **Read recent git history:** `git log --oneline -30`
5. **Read any existing review/QA reports:** `ls docs/reviews/ docs/qa/ 2>/dev/null` (this run's are named `2026-06-09-cli-batch-N-review.md` / `2026-06-09-cli-full-qa.md`)
6. **Determine where to resume** based on `progress.json`:

| `current_step` | What to do |
|----------------|------------|
| `execute` | Executor was in progress. Check git log — if commits exist for current batch tasks, executor may have partially finished. Dispatch executor for remaining tasks only. |
| `review` | Review in progress or not started. If no review report, dispatch reviewer; if incomplete, dispatch again. |
| `fix` | Fixes in progress. Check for fix commits since the review report. Dispatch executor for remaining Critical/Important issues. |
| `fix_verify` | Fix verification in progress. Dispatch reviewer to verify fixes. |
| `qa` | QA in progress. If no QA report, dispatch QA. |
| `qa_fix` | QA bug fixes in progress. Check QA report for remaining bugs. Dispatch executor. |
| `qa_verify` | QA re-verification. Dispatch QA to verify fixes. |
| `done` | Everything complete. Nothing to do. |

7. **Resume the orchestration loop** from the determined point, following `docs/sessions/cli/orchestrator.md`.

## Start

Read `docs/sessions/cli/progress.json`, then `docs/sessions/cli/orchestrator.md`. Determine current state, announce where you're resuming from, and continue the pipeline.
