#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a simulation result query request.")
    parser.add_argument("--simulation-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "endpoint": f"/simulations/{args.simulation_id}",
        "method": "GET",
        "candidate_id": args.candidate_id,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
