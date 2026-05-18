#!/usr/bin/env bash
set -euo pipefail

ROOT="${FACTOR_FACTORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${FACTOR_FACTORY_PYTHON:-python3}"
RUN_ID="wq-sync-launchd-$(date -u +%Y%m%dT%H%M%SZ)"
LOCK_DIR="$ROOT/state/audit/wq-sync-once.lock"
PID_FILE="$LOCK_DIR/pid"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$PID_FILE" ]]; then
    existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
      echo "Another WQ sync cycle is already running; skipping $RUN_ID"
      exit 0
    fi
  fi
  echo "Removing stale lock for $RUN_ID"
  rm -f "$PID_FILE" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
  mkdir "$LOCK_DIR"
fi

printf '%s\n' "$$" > "$PID_FILE"

if [[ ! -d "$LOCK_DIR" ]]; then
  echo "Another WQ sync cycle is already running; skipping $RUN_ID"
  exit 0
fi

cleanup() {
  rm -f "$PID_FILE" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$PYTHON" "$ROOT/scripts/run_wq_sync_loop.py" \
  --max-cycles 1 \
  --no-sleep \
  --auto-replenish \
  --replenish-pool-id tp-stage3-analyst-earnings-event-reset-v0 \
  --fallback-replenish-pool-id tp-stage3-calm-market-contrast-v0 \
  --fallback-replenish-pool-id tp-stage2-earnings-yield-momentum-v0 \
  --replenish-batch-prefix analystevent \
  --replenish-min-ready 60 \
  --replenish-batch-size 60 \
  --run-id "$RUN_ID"
