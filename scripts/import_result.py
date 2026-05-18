#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import jsonschema


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
QUEUE = STATE / "queue"
IMPORTS = STATE / "imports"
SCHEMAS = ROOT / "schemas"


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a WorldQuant result record into Factor Factory state.")
    parser.add_argument("--input", required=True, help="Path to a result-import JSON file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload_path = pathlib.Path(args.input).expanduser().resolve()
    payload = read_json(payload_path)
    schema = read_json(SCHEMAS / "result-import.schema.json")
    jsonschema.Draft202012Validator(schema).validate(payload)

    candidate_id = payload["candidate_id"]
    candidate_path = QUEUE / f"{candidate_id}.json"
    if not candidate_path.exists():
        raise FileNotFoundError(f"Candidate not found: {candidate_path}")

    candidate = read_json(candidate_path)
    history = list(candidate.get("status_history", [candidate["status"]]))
    if history[-1] != "manual_result_pending":
        if history[-1] != "ready_for_platform_probe":
            history.append("ready_for_platform_probe")
        history.append("manual_result_pending")
    history.append("result_imported")
    candidate["status_history"] = history
    candidate["status"] = "result_imported"
    candidate["artifact_state"] = "simulated_on_platform"
    candidate["latest_result_import"] = payload

    write_json(candidate_path, candidate)
    write_json(IMPORTS / f"{candidate_id}.json", payload)
    print(json.dumps({"candidate_id": candidate_id, "status": candidate["status"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
