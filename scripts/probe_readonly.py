#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
AUDIT = STATE / "audit"

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.session_probe import run_read_only_contract_check  # noqa: E402


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a low-frequency read-only WorldQuant contract probe.")
    parser.add_argument("--target-id", required=True, help="CDP target id of the logged-in WorldQuant tab.")
    parser.add_argument("--candidate-id", default="cand-system-probe", help="Audit correlation id.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_read_only_contract_check(args.target_id)
    event = {
        "event_id": f"audit-{args.candidate_id}-contract-check",
        "event_type": "contract_check",
        "candidate_id": args.candidate_id,
        "created_at": report["checkedAt"],
        "details": report,
    }
    write_json(AUDIT / f"{args.candidate_id}-contract-check.json", event)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
