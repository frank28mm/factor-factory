#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
QUEUE = ROOT / "state" / "queue"
STATE = ROOT / "state"
PROBES = STATE / "probes"
CONFIG = ROOT / "config"

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.simulation_probe import build_simulation_request, to_api_payload  # noqa: E402


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_yaml(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a simulation probe payload from a queued candidate.")
    parser.add_argument("--candidate-id", required=True, help="Candidate id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidate = read_json(QUEUE / f"{args.candidate_id}.json")
    rules = read_yaml(CONFIG / "official-worldquant-rules.yaml")
    settings = rules["simulation_defaults"]
    probe = build_simulation_request(candidate, settings)
    payload = {
        "candidate_id": probe.candidate_id,
        "expression": probe.expression,
        "api_payload": to_api_payload(probe),
        "settings": {
            "region": probe.region,
            "universe": probe.universe,
            "delay": probe.delay,
            "neutralization": probe.neutralization,
        },
        "result_query_contract": {
            "endpoint_pattern": "/simulations/{id}",
            "expected_follow_up": "poll only after separate gate and valid login session"
        },
        "status": "ready_for_manual_gate"
    }
    write_json(PROBES / f"{args.candidate_id}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
