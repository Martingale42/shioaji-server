#!/usr/bin/env bash
# Market-open live verification for the Sinopac adapter against the SIMULATION gateway.
#
# Runs the NautilusTrader data + exec example testers for a bounded time so the cron
# job never hangs (the testers' node.run() otherwise blocks forever).
#
# SAFETY (the whole point): the exec tester PLACES ORDERS, so it runs ONLY when the
# gateway reports simulation=true. If the gateway is LIVE (or unreachable), the exec
# tester is SKIPPED — no order is ever placed against a live account. The data tester
# places no orders and always runs.
#
# Gateway is expected on :8123 (moved off 8000). Override with SINOPAC_GATEWAY_URL.
# Bounded by RUN_SECS (default 120s) per tester via `timeout`.
set -uo pipefail

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

GATEWAY="${SINOPAC_GATEWAY_URL:-http://localhost:8123}"
NT=/home/cy/Code/MT5/nautilus_trader
RUN_SECS="${RUN_SECS:-120}"
LOGDIR="$HOME/sinopac_market_open_logs"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOGDIR/verify_$STAMP.log"

log() { echo "[$(date '+%F %T %Z')] $*" | tee -a "$LOG"; }

log "market-open verify START — gateway=$GATEWAY run_secs=$RUN_SECS"

AUTH="$(curl -s -m 10 "$GATEWAY/api/auth/status" 2>/dev/null || true)"
HEALTH="$(curl -s -m 10 "$GATEWAY/api/health" 2>/dev/null || true)"
log "auth=$AUTH"
log "health=$HEALTH"

if [ -z "$AUTH" ]; then
  log "ABORT — gateway not reachable at $GATEWAY"
  exit 1
fi

# --- Data tester (no orders, always safe) ---
log "data tester (${RUN_SECS}s)…"
( cd "$NT" && timeout --kill-after=20 "$RUN_SECS" uv run python examples/live/sinopac/sinopac_data_tester.py ) >>"$LOG" 2>&1
log "data tester exit=$?"

# --- Exec tester (PLACES ORDERS) — HARD simulation guard ---
# Order-semantics scenarios driven by SINOPAC_EXEC_SCENARIO (see the exec tester).
# Override the set with SINOPAC_EXEC_SCENARIOS; each scenario gets its own bounded
# timeout and a distinct log-line prefix.
SCENARIOS="${SINOPAC_EXEC_SCENARIOS:-common intraday_odd mkp futures_octype}"
if printf '%s' "$AUTH" | grep -q '"simulation":true'; then
  log "simulation CONFIRMED → exec tester scenarios=[$SCENARIOS] (${RUN_SECS}s each, REAL sim placement)…"
  for s in $SCENARIOS; do
    log "[scenario:$s] exec tester (${RUN_SECS}s)…"
    ( cd "$NT" && SINOPAC_EXEC_SCENARIO="$s" timeout --kill-after=20 "$RUN_SECS" \
        uv run python examples/live/sinopac/sinopac_exec_tester.py ) >>"$LOG" 2>&1
    log "[scenario:$s] exec tester exit=$?"
  done
else
  log "!!! gateway is NOT simulation (auth=$AUTH) → SKIPPING exec tester, NO orders placed !!!"
fi

log "market-open verify DONE — full log: $LOG"
