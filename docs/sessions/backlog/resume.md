# Resume Backlog Orchestrator (Self-Healing)

Use this prompt when the backlog orchestrator session was interrupted. Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the development orchestrator for the **shioaji-server + sinopac backlog fixes (BL-1…BL-4)**. **Your previous session was interrupted.** Resume from where it left off.

## Recovery Steps

1. **Read progress state:** `docs/sessions/backlog/progress.json`
2. **Read the orchestrator rules:** `docs/sessions/backlog/orchestrator.md` (full pipeline flow, dispatch templates, cross-repo warning, gates, orchestration logic). Follow them exactly.
3. **Read the plans** (for context):
   - `docs/plans/2026-06-08-bl2-bl4-cleanups.md`
   - `docs/plans/2026-06-08-bl3-catalog-metadata-restamp.md`
   - `docs/plans/2026-06-08-bl1-p3-adopt.md`
   - `docs/plans/2026-06-08-backlog-fixes-design.md` (design)
4. **Read recent git history:** `git log --oneline -30` (and in `/home/cy/Code/MT5/nautilus_trader` if resuming Batch 1 BL-4 or Batch 3).
5. **Read existing reports:** `ls docs/reviews/ docs/qa/ 2>/dev/null`
6. **Determine where to resume** from `progress.json`:

| `current_step` | What to do |
|----------------|------------|
| `execute` | Executor in progress. Check git log — dispatch executor for remaining tasks only. |
| `review` | Dispatch reviewer (check if a report already exists for this batch). |
| `fix` | Dispatch executor for remaining Critical/Important issues. |
| `fix_verify` | Dispatch reviewer to verify fixes. |
| `qa` | Dispatch QA (check if QA report exists). |
| `qa_fix` | Dispatch executor for remaining QA bugs. |
| `qa_verify` | Dispatch QA to verify fixes. |
| `done` | Everything complete. Nothing to do. |

7. **Resume the orchestration loop** from the determined point, following `docs/sessions/backlog/orchestrator.md`. Mind the cross-repo warning (Batch 1 BL-4 and Batch 3 adapter are in `nautilus_trader@sinopac-adapter-clean`) and the gates (Batch 2 Task 1, Batch 3 Task 1).

## Start

Read `docs/sessions/backlog/progress.json`, then `docs/sessions/backlog/orchestrator.md`. Determine current state, announce where you're resuming from, and continue the pipeline.
