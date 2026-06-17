#!/usr/bin/env bash
# Daily resume + verify for the 00981A point-in-time constituent universe download.
#
# Idempotent by design: re-runs `shioaji-data fetch-bars` over the full 81-code
# union; codes already loaded through the latest trading day are skipped with
# ZERO API calls (catalog-aware resume), quota-truncated tails auto-resume from
# the last real bar, and the rest download until the daily Shioaji 500MB traffic
# quota is spent. Then runs `inspect` for a coverage report.
#
# Safe to run repeatedly. If the daily quota is already exhausted (or the gateway
# session is stale), the CLI's 2330 pre-flight probe returns empty and fetch-bars
# exits 1 without writing anything — a harmless no-op, still logged.
#
# Remove the crontab entry once `inspect` shows every code's `last` at the latest
# trading day. Delisted members 2823 中壽 / 2888 新光金 are handled separately
# (recent-month probe skips them; they need a historical-window fetch).
#
# NOTE: each successful run consumes the full 500MB daily quota — on days you
# need quota for live trading, comment out the crontab line or move its time.
set -uo pipefail

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO=/home/cy/Code/MT5/shioaji-server
CODES="$REPO/universe/00981a_top300_constituents.txt"
CATALOG="$REPO/catalog"
GATEWAY="${SHIOAJI_GATEWAY_URL:-http://localhost:8123}"
LOGDIR="$HOME/.shioaji-server/logs/00981a_fetch"

mkdir -p "$LOGDIR"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/run-$TS.log"

cd "$REPO" || { echo "repo not found: $REPO" >&2; exit 1; }

{
  echo "=================================================================="
  echo "00981A constituent resume run @ $(date -Is)"
  echo "------------------------------------------------------------------"
  echo "--- quota before ---"
  curl -s -m 10 "$GATEWAY/api/account/usage" || echo "(usage query failed)"
  echo
  echo "--- fetch-bars (idempotent resume) ---"
  # --concurrency 1 is REQUIRED, not a tunable: the gateway shares one non-thread-safe
  # Shioaji session, so concurrent kbars() calls interfere and return empty → the probe
  # reads empty → a silent no_data cascade for the rest of the run (see CLAUDE.md
  # Gotchas / BACKLOG BL-14). Do NOT raise it.
  uv run shioaji-data fetch-bars \
      --gateway-url "$GATEWAY" \
      --codes-file "$CODES" \
      --catalog "$CATALOG" \
      --start 2020-03-02 --concurrency 1
  echo "fetch-bars exit_code=$?"
  echo
  echo "--- quota after ---"
  curl -s -m 10 "$GATEWAY/api/account/usage" || echo "(usage query failed)"
  echo
  echo "--- inspect (coverage report) ---"
  uv run shioaji-data inspect --catalog "$CATALOG"
  echo "=== done @ $(date -Is) ==="
} >> "$LOG" 2>&1

# Surface the newest log path on stdout (visible in `grep` of cron mail, if any).
echo "log: $LOG"
