#!/usr/bin/env bash
set -euo pipefail

ROOT="${FACTOR_FACTORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PYTHON="${FACTOR_FACTORY_PYTHON:-python3}"
RUN_ID_FILE="$ROOT/state/logs/factor-factory-continuous.pid"
LOG_FILE="$ROOT/state/logs/factor-factory-continuous.log"

usage() {
  cat <<'USAGE'
Usage: ./start_factor_factory.sh <command>

Commands:
  dry-run           Plan one cycle without live platform writes.
  start-continuous  Start the fast continuous WQ sync loop in the background.
  stop-continuous   Stop the background WQ sync loop started by this wrapper.
  status            Show the background loop status and recent logs.
  dashboard         Open the local dashboard.

Automation:
  The continuous loop uses distribution/run_macos.sh with --max-cycles 0,
  --interval-seconds 15, --probe-batch-limit 3, and --submit-ready-limit 4.
  It replenishes candidates, polls simulation/check results, fills open WQ
  simulation slots, refreshes ledgers/dashboard, and automatically submits
  submit-ready alphas within the configured daily quota.
USAGE
}

ensure_dirs() {
  mkdir -p "$ROOT/state/logs" "$ROOT/state/visual"
}

stop_continuous() {
  ensure_dirs
  if [[ -f "$RUN_ID_FILE" ]]; then
    pid="$(cat "$RUN_ID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "Stopped Factor Factory continuous loop PID $pid"
    else
      echo "No running Factor Factory continuous loop for recorded PID ${pid:-unknown}"
    fi
    rm -f "$RUN_ID_FILE"
  else
    echo "No Factor Factory continuous PID file found"
  fi
}

start_continuous() {
  ensure_dirs
  if [[ -f "$RUN_ID_FILE" ]]; then
    old_pid="$(cat "$RUN_ID_FILE" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "Factor Factory continuous loop already running: PID $old_pid"
      exit 0
    fi
    rm -f "$RUN_ID_FILE"
  fi
  FACTOR_FACTORY_RUN_ID="${FACTOR_FACTORY_RUN_ID:-factor-factory-continuous-$(date -u +%Y%m%dT%H%M%SZ)}" \
    FACTOR_FACTORY_INTERVAL_SECONDS="${FACTOR_FACTORY_INTERVAL_SECONDS:-15}" \
    FACTOR_FACTORY_SUBMIT_READY_LIMIT="${FACTOR_FACTORY_SUBMIT_READY_LIMIT:-4}" \
    FACTOR_FACTORY_PYTHON="$PYTHON" \
    nohup bash "$ROOT/distribution/run_macos.sh" > "$LOG_FILE" 2>&1 &
  echo "$!" > "$RUN_ID_FILE"
  echo "Started Factor Factory continuous loop PID $(cat "$RUN_ID_FILE")"
  echo "Log: $LOG_FILE"
}

run_dry() {
  ensure_dirs
  FACTOR_FACTORY_INTERVAL_SECONDS="${FACTOR_FACTORY_INTERVAL_SECONDS:-15}" \
    FACTOR_FACTORY_SUBMIT_READY_LIMIT="${FACTOR_FACTORY_SUBMIT_READY_LIMIT:-4}" \
    FACTOR_FACTORY_PYTHON="$PYTHON" \
    bash "$ROOT/distribution/run_macos.sh" --dry-run-once
}

status_continuous() {
  ensure_dirs
  if [[ -f "$RUN_ID_FILE" ]]; then
    pid="$(cat "$RUN_ID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Factor Factory continuous loop: running PID $pid"
    else
      echo "Factor Factory continuous loop: stale PID ${pid:-unknown}"
    fi
  else
    echo "Factor Factory continuous loop: not started by this wrapper"
  fi
  echo
  echo "Recent wrapper log:"
  tail -n 40 "$LOG_FILE" 2>/dev/null || echo "  no wrapper log yet"
  echo
  echo "Recent distribution logs:"
  find "$ROOT/state/logs" -maxdepth 1 -type f -name 'factor-factory-*.log' -print0 2>/dev/null \
    | xargs -0 ls -t 2>/dev/null \
    | head -n 1 \
    | while read -r latest_log; do
        echo "  $latest_log"
        tail -n 40 "$latest_log"
      done
}

open_dashboard() {
  dashboard="$ROOT/state/visual/factor-factory-dashboard.html"
  if [[ ! -f "$dashboard" ]]; then
    "$PYTHON" "$ROOT/scripts/export_visual_ledger.py" >/dev/null
  fi
  open "$dashboard"
}

case "${1:-}" in
  dry-run)
    run_dry
    ;;
  start-continuous)
    start_continuous
    ;;
  stop-continuous)
    stop_continuous
    ;;
  status)
    status_continuous
    ;;
  dashboard)
    open_dashboard
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown command: $1" >&2
    usage >&2
    exit 2
    ;;
esac
