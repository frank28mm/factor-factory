#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT = ROOT / "state" / "audit"
PROBES = ROOT / "state" / "probes"
IMPORTS = ROOT / "state" / "imports"

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.result_adapter import build_result_import_from_live_payload, merge_alpha_check_payload  # noqa: E402


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a live simulation result audit into result-import schema.")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--audit-file", help="Optional explicit path to a simulation-result audit file.")
    parser.add_argument("--check-audit-file", help="Optional alpha-check audit file whose checks should override alpha detail checks.")
    parser.add_argument("--output", help="Optional explicit path for the normalized import JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit_path = pathlib.Path(args.audit_file).resolve() if args.audit_file else AUDIT / f"{args.candidate_id}-simulation-result.json"
    audit_event = read_json(audit_path)
    response = audit_event.get("response", {})
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Live simulation result audit does not contain a JSON payload.")
    if args.check_audit_file:
        check_audit = read_json(pathlib.Path(args.check_audit_file).resolve())
        check_payload = check_audit.get("response", {}).get("payload")
        if isinstance(check_payload, dict):
            payload = merge_alpha_check_payload(payload, check_payload)

    probe_path = PROBES / f"{args.candidate_id}.json"
    probe = read_json(probe_path)
    normalized = build_result_import_from_live_payload(
        candidate_id=args.candidate_id,
        simulation_settings=probe.get("settings", {}),
        payload=payload,
        simulation_id=audit_event.get("simulation_id"),
        alpha_id=audit_event.get("alpha_id"),
        source_type=str(audit_event.get("event_type") or "simulation_result"),
        notes=f"Converted from live {audit_event.get('event_type', 'simulation_result')} audit: {audit_path}",
    )
    output_path = pathlib.Path(args.output).resolve() if args.output else IMPORTS / f"{args.candidate_id}-from-live.json"
    write_json(output_path, normalized)
    print(json.dumps({"output": str(output_path), "record": normalized}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
