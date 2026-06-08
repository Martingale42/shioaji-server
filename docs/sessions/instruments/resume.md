# Resume Instrument-Definitions Orchestrator (Self-Healing)

Use this prompt when the instrument-definitions orchestrator session was interrupted. Copy everything below `---` into a new Claude Code session in `/home/cy/Code/MT5/shioaji-server`.

---

You are the development orchestrator for the **Shioaji → NautilusTrader instrument-definition fixes**. **Your previous session was interrupted.** Resume from where it left off.

## Recovery Steps

1. **Read progress state:** `docs/sessions/instruments/progress.json`
2. **Read the orchestrator rules:** `docs/sessions/instruments/orchestrator.md` (full pipeline flow, dispatch templates, cross-repo warning, gates, orchestration logic). Follow them exactly.
3. **Read the plans** (for context):
   - `docs/plans/2026-06-08-ws-a-gateway-contract-fields.md`
   - `docs/plans/2026-06-08-ws-b-adapter-instrument-parse.md`
   - `docs/plans/2026-06-08-ws-cd-backtest-and-regen.md`
   - `docs/plans/2026-06-08-instrument-definitions-design.md` (design)
4. **Read recent git history:** `git log --oneline -30` (and in `/home/cy/Code/MT5/nautilus_trader` if resuming Batch 2).
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

7. **Resume the orchestration loop** from the determined point, following `docs/sessions/instruments/orchestrator.md`. Mind the cross-repo warning (Batch 2 is `nautilus_trader@sinopac-adapter-clean`), the Batch 3 Task 1 GATE, and the WS-D data red line (regen only instrument defs, backup first).

## Start

Read `docs/sessions/instruments/progress.json`, then `docs/sessions/instruments/orchestrator.md`. Determine current state, announce where you're resuming from, and continue the pipeline.
