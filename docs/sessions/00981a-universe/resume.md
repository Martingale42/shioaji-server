# Resume Orchestrator (Self-Healing)

Use this prompt when the orchestrator session's context exploded or was interrupted. Copy everything below `---` into a new Claude Code session opened in `/home/cy/Code/MT5/shioaji-server/.claude/worktrees/00981a-universe`.

---

You are the development orchestrator for the **00981A Top-300 Constituent Catalog**. **Your previous session was interrupted.** Resume from where it left off.

## Recovery Steps

1. **Read progress state:** `docs/sessions/00981a-universe/progress.json`. Note `model_assignments`. To re-dispatch a role:
   (1) If `.claude/agents/orchestrator-<role>.md` exists, dispatch with `subagent_type: "orchestrator-<role>"` — model + effort enforced by frontmatter.
   (2) If the agent file is missing but `model_assignments` has the role, dispatch with the Agent tool's `model` parameter and inline the role content from `docs/sessions/00981a-universe/0{1,2,3}-*.md`; effort can't be enforced in this mode.
   (3) If neither exists, dispatch with no `model` — session defaults.

2. **Read the orchestrator rules:** `docs/sessions/00981a-universe/orchestrator.md` — full pipeline flow, dispatch templates, the **Batch 1 spike decision gate**, and the **Batch 3 Task 6 user-coordination gate**. Follow exactly.

3. **Read the plans:** `docs/plans/2026-06-16-00981a-constituents-catalog.md` and `...-design.md`.

4. **Read recent git history:** `git log --oneline -30`.

5. **Read any existing reports:** `ls docs/reviews/*00981a* docs/qa/*00981a* 2>/dev/null`.

6. **Determine where to resume** from `progress.json` `current_step`:

| `current_step` | What to do |
|----------------|------------|
| `execute` | Executor in progress. Check git log; dispatch executor for remaining tasks only. (Batch 1: if a NO-GO verdict was recorded, STOP and ask the user.) |
| `review` | Dispatch reviewer if no/incomplete report. |
| `fix` | Dispatch executor for remaining Critical/Important issues. |
| `fix_verify` | Dispatch reviewer to verify fixes. |
| `qa` | Dispatch QA if no QA report. |
| `qa_fix` | Dispatch executor for remaining QA bugs. |
| `qa_verify` | Dispatch QA to verify fixes. |
| `final_audit` | Re-invoke `/code-review` (or the reviewer-audit fallback) on the whole branch vs main. |
| `audit_fix` | Dispatch executor for unresolved Critical audit findings. |
| `audit_verify` | Re-run the audit (counts toward the 2-cycle cap). |
| `done` | Complete. Nothing to do (unless the Task 6 download is still pending user coordination). |

7. **Resume the loop** per `docs/sessions/00981a-universe/orchestrator.md`.

## Start

Read `docs/sessions/00981a-universe/progress.json`, then `orchestrator.md`. Announce where you're resuming from, and continue. Honor the Batch 1 spike gate and the Task 6 user-coordination gate.
