#!/usr/bin/env bash
set -euo pipefail

ROOT="${FACTOR_FACTORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${FACTOR_FACTORY_PYTHON:-python3}"
RUN_ID="${FACTOR_FACTORY_RUN_ID:-factor-factory-macos-$(date -u +%Y%m%dT%H%M%SZ)}"
INTERVAL="${FACTOR_FACTORY_INTERVAL_SECONDS:-15}"
MAX_RUNNING="${FACTOR_FACTORY_MAX_RUNNING:-3}"
PENDING_LIMIT="${FACTOR_FACTORY_PENDING_REFRESH_LIMIT:-3}"
WAITING_LIMIT="${FACTOR_FACTORY_WAITING_REFRESH_LIMIT:-5}"
SUBMIT_LIMIT="${FACTOR_FACTORY_SUBMIT_READY_LIMIT:-4}"
PROBE_LIMIT="${FACTOR_FACTORY_PROBE_BATCH_LIMIT:-3}"
PROBE_COOLDOWN="${FACTOR_FACTORY_PROBE_RATE_LIMIT_COOLDOWN_SECONDS:-180}"
GLOBAL_COOLDOWN="${FACTOR_FACTORY_RATE_LIMIT_COOLDOWN_SECONDS:-600}"
MODE="foreground"

if [[ "${1:-}" == "--dry-run-once" ]]; then
  cd "$ROOT"
  exec "$PYTHON" scripts/run_wq_sync_loop.py \
    --run-id "$RUN_ID-dry-run" \
    --max-cycles 1 \
    --interval-seconds "$INTERVAL" \
    --max-running "$MAX_RUNNING" \
    --pending-refresh-limit "$PENDING_LIMIT" \
    --waiting-refresh-limit "$WAITING_LIMIT" \
    --submit-ready-limit "$SUBMIT_LIMIT" \
    --probe-batch-limit "$PROBE_LIMIT" \
    --probe-rate-limit-cooldown-seconds "$PROBE_COOLDOWN" \
    --rate-limit-cooldown-seconds "$GLOBAL_COOLDOWN" \
    --auto-replenish \
    --dry-run
fi

if [[ "${1:-}" == "--background" ]]; then
  MODE="background"
fi

cd "$ROOT"
COMMAND=(
  "$PYTHON" scripts/run_wq_sync_loop.py
  --run-id "$RUN_ID"
  --max-cycles 0
  --interval-seconds "$INTERVAL"
  --max-running "$MAX_RUNNING"
  --pending-refresh-limit "$PENDING_LIMIT"
  --waiting-refresh-limit "$WAITING_LIMIT"
  --submit-ready-limit "$SUBMIT_LIMIT"
  --probe-batch-limit "$PROBE_LIMIT"
  --probe-rate-limit-cooldown-seconds "$PROBE_COOLDOWN"
  --rate-limit-cooldown-seconds "$GLOBAL_COOLDOWN"
  --auto-replenish
)

if [[ -n "${WQ_TARGET_ID:-}" ]]; then
  COMMAND+=(--target-id "$WQ_TARGET_ID")
fi

if [[ "$MODE" == "background" ]]; then
  mkdir -p state/logs
  nohup "${COMMAND[@]}" > "state/logs/$RUN_ID.log" 2>&1 &
  echo "$!" > "state/logs/$RUN_ID.pid"
  echo "Started background loop PID $(cat "state/logs/$RUN_ID.pid")"
  echo "Log: state/logs/$RUN_ID.log"
else
  exec "${COMMAND[@]}"
fi
