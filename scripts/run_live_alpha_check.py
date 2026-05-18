#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT = ROOT / "state" / "audit"

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.live_simulation import (  # noqa: E402
    classify_alpha_check_response,
    fetch_alpha_check,
)
from connectors.worldquant_brain.session_probe import find_worldquant_target_id  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch one live WorldQuant alpha submit-readiness check.")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--alpha-id", required=True)
    parser.add_argument("--simulation-id", help="Optional source simulation id for audit linkage.")
    parser.add_argument("--target-id", help="CDP target id of a logged-in WorldQuant tab. Auto-detected when omitted.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_id = args.target_id or find_worldquant_target_id()
    response = fetch_alpha_check(target_id, args.alpha_id)
    event = {
        "event_id": f"audit-{args.candidate_id}-alpha-check",
        "event_type": "alpha_check",
        "candidate_id": args.candidate_id,
        "simulation_id": args.simulation_id,
        "alpha_id": args.alpha_id,
        "created_at": now_iso(),
        "target_id": target_id,
        "classification": classify_alpha_check_response(response),
        "request": {
            "endpoint": f"/alphas/{args.alpha_id}/check",
            "method": "GET",
        },
        "response": response,
    }
    write_json(AUDIT / f"{args.candidate_id}-alpha-check.json", event)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
