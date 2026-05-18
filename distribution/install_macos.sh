#!/usr/bin/env bash
set -euo pipefail

ROOT="${FACTOR_FACTORY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${FACTOR_FACTORY_PYTHON:-python3}"

cd "$ROOT"

echo "== Factor Factory macOS install =="
"$PYTHON" --version

if [[ -f requirements.txt ]]; then
  "$PYTHON" -m pip install -r requirements.txt
fi

"$PYTHON" distribution/doctor.py --skip-live-session

echo
echo "Install OK. Next:"
echo "1. Bootstrap local candidates: $PYTHON scripts/run_v15_local_cycle.py --run-id first-local-cycle --candidate-limit 20"
echo "2. Optional live sync: start the browser bridge, open Chrome, log in to https://platform.worldquantbrain.com/"
echo "3. Optional official metadata sync: $PYTHON scripts/sync_worldquant_official.py --fields-only"
echo "4. Live doctor: $PYTHON distribution/doctor.py"
echo "5. Start dry run: bash distribution/run_macos.sh --dry-run-once"
