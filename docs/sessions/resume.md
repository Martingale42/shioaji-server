# Resume Orchestrator (Self-Healing)

Use this prompt when the orchestrator session's context exploded or was interrupted.
Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the development orchestrator for **shioaji-server + sinopac adapter fixes**. **Your previous session was interrupted.** Resume from where it left off.

## Recovery Steps

1. **Read progress state:** `docs/sessions/progress.json`
2. **Read the orchestrator rules:** `docs/sessions/orchestrator.md` (full pipeline flow, dispatch templates, cross-repo warning, orchestration logic). Follow those rules exactly.
3. **Read the plans** (for context):
   - `docs/plans/2026-06-08-gateway-resilience.md`
   - `docs/plans/2026-06-08-timezone-unification.md`
   - `docs/plans/2026-06-08-sinopac-execution.md` (cross-repo: nautilus_trader@sinopac-adapter-clean)
   - `docs/AUDIT.md` (findings background)
4. **Read recent git history:** `git log --oneline -30` (and in `/home/cy/Code/MT5/nautilus_trader` if resuming Batch 3)
5. **Read existing reports:** `ls docs/reviews/ docs/qa/ 2>/dev/null`
6. **Determine where to resume** from `progress.json`:

| `current_step` | What to do |
|----------------|------------|
| `execute` | Executor was in progress. Check git log — dispatch executor for remaining tasks only. |
| `review` | Dispatch reviewer (check if a report already exists for this batch). |
| `fix` | Dispatch executor for remaining Critical/Important issues in the batch review. |
| `fix_verify` | Dispatch reviewer to verify fixes. |
| `qa` | Dispatch QA (check if QA report exists). |
| `qa_fix` | Dispatch executor for remaining QA bugs. |
| `qa_verify` | Dispatch QA to verify fixes. |
| `done` | Everything complete. Nothing to do. |

7. **Resume the orchestration loop** from the determined point, following `docs/sessions/orchestrator.md`.

## Start

Read `docs/sessions/progress.json`, then `docs/sessions/orchestrator.md`. Determine current state, announce where you're resuming from, and continue the pipeline. Mind the cross-repo warning for Batch 3.
