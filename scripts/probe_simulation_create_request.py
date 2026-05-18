#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBES = ROOT / "state" / "probes"


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a simulation create request from a stored probe payload.")
    parser.add_argument("--candidate-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    probe = read_json(PROBES / f"{args.candidate_id}.json")
    payload = {
        "endpoint": "/simulations",
        "method": "POST",
        "body": probe["api_payload"],
        "candidate_id": args.candidate_id,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
