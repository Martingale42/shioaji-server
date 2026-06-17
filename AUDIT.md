# Audit Index — shioaji-server + sinopac adapter

Independent audits. Each row links a full, **immutable** report under
`docs/audits/`; once written a report is never edited (a later re-audit gets its
own file). Live status of the scheduled items is tracked in `BACKLOG.md`.

| Date | Scope | Verdict | Findings | Report |
|---|---|---|---|---|
| 2026-06-08 | `shioaji-server` (gateway + scripts) + `nautilus_trader@sinopac-adapter-clean` (sinopac adapter Rust + Python) | Session self-heal regressions stopped; a data-correctness timezone bug and several resilience/ledger gaps found | 🔴 timezone 3-way (§0) · gateway G1–G8 · scripts S1–S5 · sinopac Rust R1–R5 · Python P1–P7 · §5 features | [2026-06-08-shioaji-server.md](docs/audits/2026-06-08-shioaji-server.md) |

## Findings → backlog status

The 2026-06-08 fix pipeline pulled a curated subset of these findings into
`BACKLOG.md` as items BL-1…BL-8 (session self-heal G1–G4 landed in `1a7fa07`
ahead of that). The report's remaining open findings — gateway **G5–G8**,
scripts **S2 / S4 / S5**, sinopac Rust **R1–R5** and Python **P4–P7**, and the
§5 feature list — are deliberately **not yet scheduled**; pull them into the
backlog when a future batch picks them up. Sinopac adapter findings (§4) are
fixed in the `nautilus_trader` repo, not here.
