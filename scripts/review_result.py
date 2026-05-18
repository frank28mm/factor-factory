#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import jsonschema
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
QUEUE = STATE / "queue"
REVIEWS = STATE / "reviews"
CONFIG = ROOT / "config"
SCHEMAS = ROOT / "schemas"


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_yaml(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review an imported WorldQuant result inside Factor Factory.")
    parser.add_argument("--candidate-id", required=True, help="Candidate id, e.g. cand-example-01")
    return parser.parse_args()


def check_names(result: dict, expected_result: str) -> list[str]:
    names = []
    for check in result.get("checks", []):
        if not isinstance(check, dict):
            continue
        if str(check.get("result", "")).upper() == expected_result:
            name = check.get("name")
            if name:
                names.append(str(name))
    return names


def evaluate(result: dict, scoring: dict) -> tuple[str, str]:
    metrics = result["metrics"]
    gates = scoring["official_platform_gates"]["delay_1"]
    turnover = scoring["official_platform_gates"]["turnover_range"]
    self_corr = scoring["official_platform_gates"]["self_correlation"]["max"]

    if metrics["sharpe"] < gates["sharpe_min"] or metrics["fitness"] < gates["fitness_min"]:
        return "blocked", "Core gates failed: Sharpe or Fitness below Delay 1 threshold."
    if not (turnover["min"] <= metrics["turnover"] <= turnover["max"]):
        return "blocked", "Turnover outside allowed platform range."
    if metrics.get("self_correlation") is not None and metrics["self_correlation"] >= self_corr:
        return "blocked", "Self-correlation too high for submission review."
    failed_checks = check_names(result, "FAIL")
    if failed_checks:
        return "blocked", f"Platform checks failed: {', '.join(failed_checks)}."
    pending_checks = check_names(result, "PENDING")
    if pending_checks:
        return "deferred", f"Core gates passed, but platform checks are still pending: {', '.join(pending_checks)}."
    return "approved", "Candidate passes the first submission-readiness review."


def main() -> int:
    args = parse_args()
    candidate_path = QUEUE / f"{args.candidate_id}.json"
    if not candidate_path.exists():
        raise FileNotFoundError(f"Candidate not found: {candidate_path}")

    candidate = read_json(candidate_path)
    latest_result = candidate.get("latest_result_import")
    if not latest_result:
        raise RuntimeError("No imported result found on candidate.")

    scoring = read_yaml(CONFIG / "scoring.yaml")
    decision, reason = evaluate(latest_result, scoring)
    history = list(candidate.get("status_history", [candidate["status"]]))
    if history[-1] != "reviewed":
        history.append("reviewed")
    candidate["status_history"] = history
    candidate["status"] = "reviewed"
    if decision == "approved":
        candidate["review_status"] = "approved"
    elif decision == "deferred":
        candidate["review_status"] = "needs_human_gate"
    else:
        candidate["review_status"] = "rejected"

    review_record = {
        "candidate_id": args.candidate_id,
        "gate_level": "gate_3_submission_decision",
        "decision": decision,
        "reason": reason,
        "notes": "Generated from imported simulation metrics."
    }
    schema = read_json(SCHEMAS / "review-gate.schema.json")
    jsonschema.Draft202012Validator(schema).validate(review_record)

    write_json(candidate_path, candidate)
    write_json(REVIEWS / f"{args.candidate_id}-submission.json", review_record)
    print(json.dumps(review_record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
