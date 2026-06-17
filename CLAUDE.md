# shioaji-server — Agent Instructions

## What this is

A REST/WebSocket gateway that wraps the Shioaji (永豐金) Python SDK as the
backend for the NautilusTrader Sinopac adapter. FastAPI + uvicorn over Shioaji,
exposing HTTP endpoints and a WebSocket market-data push so NautilusTrader
(Rust/PyO3) reaches TWSE/TPEx/TAIFEX over standard network protocols. Python
3.13+, packaged with uv/hatchling. v0.1.0; test suite under `tests/` (unit,
`acceptance/`, and `gateway`-marked live-gate tests).

## Commands

```bash
uv run shioaji-server                    # run locally (simulation); add --live for live
uv run shioaji-data <cmd>                # data CLI (historical bars/ticks → NT catalog)
uv run pytest                            # full suite (asyncio auto-mode, testpaths=tests)
uv run pytest -m "not gateway"           # skip live-gate tests (they need a running gateway)
uv run ruff check src/ tests/ scripts/   # lint
uv run ruff format src/ tests/ scripts/  # format
make help                                # Docker workflow: build / up / up-live / down / logs / status
make up                                  # build + run container (simulation, detached)
```

Live-gate acceptance tests carry the `gateway` marker and require
`SINOPAC_GATEWAY_URL` pointing at a running simulation gateway.

## Architecture

FastAPI app in `src/shioaji_server/` — `app.py` (wiring), `session.py` (Shioaji
login/lifecycle), `routes/` (REST), `ws/` (WebSocket market data), `data/`
(historical-data CLI), with `models.py` / `errors.py`. `scripts/` holds the
curated data-download chain (`bars.py` engine, `fetch_single*`, one-shot
`maintenance/`); `catalog/` is the NautilusTrader `ParquetDataCatalog`;
`universe/` holds index-constituent lists. Design: `docs/concepts/architecture.md`;
endpoint/field reference: `docs/reference/api.md`.

## Project-specific conventions

System-level rules (always `uv run`, English commits, …) apply and are not
repeated. Specific to this project:

- **Cross-repo with a forked NautilusTrader.** Depends on `nautilus_trader` @
  `sinopac-adapter-clean`, installed as a pinned per-platform wheel
  (`v1.226.3-sinopac`; see `[tool.uv.sources]` in `pyproject.toml`). Many changes
  span both repos — backlog items tag repo + branch + commit on each side.
- **Timezone contract: the gateway emits true UTC.** Shioaji returns TW-local
  nanoseconds encoded as UTC; the gateway converts HTTP timestamps
  `ts_utc = ts_tw − 8h` so HTTP, WS, and download scripts agree. Diverging here is
  a data-correctness bug (the headline finding in `docs/audits/2026-06-08-shioaji-server.md`).
- **Catalog mutations are preview-before-mutate.** Regen/restamp scripts diff
  against a backup red-line before writing (this caught a bad ETF tick-size write).
- **Runtime artifacts live in `~/.shioaji-server/`** (`.env`, `Sinopac.pfx`,
  `logs/`), never in the repo — the Makefile mounts them into the container.

## Gotchas

- **The NT fork's integration tests need uv `0.11.6`** (`required-version` pin in
  the fork's `pyproject.toml`); a newer uv fails to resolve them.
- **ETF tick size ≠ stock tick size.** TWSE category `"00"` (ETF) uses a different
  tick ladder (`<50 → 0.01`, `≥50 → 0.05`); applying the stock ladder mis-sizes
  `0050` / `00631L`.
- **`shioaji-data` bar/tick fetch MUST run `--concurrency 1`.** The gateway shares
  ONE Shioaji session (`sj.api`); `--concurrency >1` fires concurrent `api.kbars()`
  calls on that single, non-thread-safe session — they interfere and return empty,
  the availability probe reads empty, and the ticker is reported `no_data` (a silent
  load-failure masquerading as "this code has no data"). Symptom: the first few codes
  succeed, then a monotonic `no_data` cascade for the rest of the run. The
  `scripts/resume_*.sh` scripts pin `--concurrency 1`; do not raise it. Verified
  2026-06-17: codes returning `no_data` at `--concurrency 4` downloaded 250k–370k bars
  each at `--concurrency 1`. See `BACKLOG.md` BL-14.
- Credentials (`SHIOAJI_SECRET_KEY`) are shown once at creation — never echo or
  commit them.

## Lifecycle docs

`ROADMAP.md` (map) · `BACKLOG.md` (open work, BL-N, status table) ·
`CHANGELOG.md` · `AUDIT.md` (index → `docs/audits/`). Knowledge docs:
`docs/concepts/` (architecture) · `docs/reference/` (API cheat-sheet). Process
trail: `docs/plans/` · `docs/qa/` · `docs/reviews/` · `docs/sessions/`.
Maintenance SOP: the `maintaining-project-docs` skill.
