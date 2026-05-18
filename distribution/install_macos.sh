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
echo "1. Open Chrome and log in to https://platform.worldquantbrain.com/"
echo "2. Run: $PYTHON distribution/doctor.py"
echo "3. Start dry run: bash distribution/run_macos.sh --dry-run-once"
