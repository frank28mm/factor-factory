#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBES = ROOT / "state" / "probes"
AUDIT = ROOT / "state" / "audit"

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.live_simulation import (  # noqa: E402
    classify_create_response,
    create_simulation,
    extract_simulation_id,
)
from connectors.worldquant_brain.state_ops import update_candidate_after_live_create  # noqa: E402
from connectors.worldquant_brain.session_probe import find_worldquant_target_id  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one live WorldQuant simulation create request.")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--target-id", help="CDP target id of a logged-in WorldQuant tab. Auto-detected when omitted.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_id = args.target_id or find_worldquant_target_id()
    probe = read_json(PROBES / f"{args.candidate_id}.json")
    response = create_simulation(target_id, probe["api_payload"])
    classification = classify_create_response(response)
    simulation_id = extract_simulation_id(response)
    event = {
        "event_id": f"audit-{args.candidate_id}-simulation-create",
        "event_type": "simulation_create",
        "candidate_id": args.candidate_id,
        "created_at": now_iso(),
        "target_id": target_id,
        "classification": classification,
        "simulation_id": simulation_id,
        "request": {
            "endpoint": "/simulations",
            "method": "POST",
            "body": probe["api_payload"],
        },
        "response": response,
    }
    write_json(AUDIT / f"{args.candidate_id}-simulation-create.json", event)
    update_candidate_after_live_create(args.candidate_id, classification, simulation_id)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
