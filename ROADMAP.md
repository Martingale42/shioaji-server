# shioaji-server — Roadmap

**Updated**: 2026-06-16 (instrument-definition pipeline + data CLI + gateway keepalive shipped; docs migrated to the SOP layout)
**Purpose**: the single document recording where this project is going and why.
Each next step gets its own design/plan before work starts (`docs/plans/`); this
file is the map, not the plans.

## Vision

A protocol-conversion gateway that wraps the Shioaji (永豐金) Python SDK —
synchronous, callback-based — as a REST + WebSocket service so the NautilusTrader
Sinopac adapter (Rust/PyO3) reaches TWSE/TPEx/TAIFEX over the network. It is the
single backend the adapter trusts, so it optimizes for:

| Binding requirement | Standing policy |
|---|---|
| **Data correctness** | The gateway emits **true UTC** (HTTP `ts_utc = ts_tw − 8h`); HTTP, WS, and download scripts must agree. Catalog mutations are preview-before-mutate against a backup red-line. |
| **Single instrument-definition source** | Live and backtest both build instruments from the sinopac adapter's Rust parse (Shioaji-authoritative `multiplier`/`unit`/`currency`); no second hardcoded source. |
| **Resilience / self-heal** | Session relogin under lock; reconnect resubscribe; bounded backoff with alerting — a "live" container must not be silently dead. |
| **Reproducible data** | `shioaji-data` writes an NT `ParquetDataCatalog` with stamped kv metadata + canonical dtypes; quota-aware resumable fetches. |

## Layer map and status

```
NautilusTrader (Rust/PyO3) Sinopac adapter   [repo: nautilus_trader@sinopac-adapter-clean]
        │  REST + WebSocket
┌───────┴───────────────────────────────────────────────┐
│ shioaji-server (this repo)                             │
│  ✅ gateway: REST routes + WS market data + session    │
│     self-heal (G1–G4 @1a7fa07)                         │
│  ✅ instrument-definition pipeline WS-A…D (BL-6)       │
│  ✅ data: shioaji-data CLI → ParquetDataCatalog        │
│     (bars/ticks, quota-aware resume, restamp chain)    │
│  ⏳ live verification (BL-5) · audit gaps G5–G8,        │
│     S2/S4/S5, sinopac R1–R5 / P4–P7 (docs/audits/)     │
└────────────────────────────────────────────────────────┘
        │  Shioaji[speed] SDK
   TWSE / TPEx / TAIFEX  (永豐金)
```

## Roadmap (ordered; demand-triggered where noted)

1. **Live verification (BL-5)** — futures/options end-to-end, WS-C
   backtest==live instrument equivalence, sinopac Python integration tests.
   Blocked on a live gateway session; the handoff checklist is ready.
2. **Resilience hardening** — close the open audit findings: gateway G5 (WS
   zombie cleanup), G6 (bad-JSON survival), G7/G8. Sinopac R1–R5
   (panic-on-malformed) and P4–P7 are fixed in the `nautilus_trader` repo.
3. **Data** — BL-8 delisted-constituent backfill (deferred; the venue may not
   serve delisted history); broaden quota-aware resume.
4. **Features (§5 of the audit, value/cost noted there)** — WS
   `session_down`/`session_recovered` push + `GET /api/ws/subscriptions`; Docker
   HEALTHCHECK on `/api/health`; subscription ground-truth resend after reconnect.

## Sequencing (recommended, revisit each kickoff)

Live verification (BL-5) unblocks the instrument-pipeline sign-off and gates
trusting live futures/options — do it first when a gateway session is available.
Resilience gaps and the §5 features interleave as small batches. Cross-repo
items in `nautilus_trader` are scheduled in that repo.

## Development process (the institution, keep it)

Each batch runs design → plan (`docs/plans/`) → orchestrated build with review +
QA gates (`docs/reviews/`, `docs/qa/`, `docs/sessions/`) → independent audit
(`docs/audits/`) → fix. Binding lessons carried forward from past audits:
deal-report field uniqueness comes from official docs / real multi-fill reports,
not static analysis (audit §4.2 P1); catalog writes are preview-before-mutate;
timezone is converted once, at the gateway.

## Reference index

| Document | Content |
|---|---|
| `BACKLOG.md` | open work (BL-N), status table |
| `AUDIT.md` | audit index → `docs/audits/` |
| `CHANGELOG.md` | shipped changes (Keep a Changelog) |
| `CLAUDE.md` | agent commands + project conventions |
| `docs/concepts/architecture.md` | architecture, component roles, data flow |
| `docs/reference/api.md` | endpoint cheat-sheet, curl examples |
| `~/.claude/skills/maintaining-project-docs` | SOP for these docs |
