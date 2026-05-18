#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PROBES = ROOT / "state" / "probes"


def run_json(command: list[str]) -> dict:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the single-candidate live simulation pipeline.")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--simulation-id", help="Required when skipping create and running from result query onward.")
    parser.add_argument("--alpha-id", help="Optional alpha id when skipping result and fetching alpha detail directly.")
    parser.add_argument("--target-id", help="Optional explicit CDP target id.")
    parser.add_argument("--skip-create", action="store_true")
    parser.add_argument("--skip-result", action="store_true")
    parser.add_argument("--skip-alpha-detail", action="store_true")
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--skip-import", dest="skip_import", action="store_true")
    parser.add_argument("--skip-review", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    py = sys.executable
    summary: dict[str, object] = {"candidate_id": args.candidate_id, "steps": []}
    simulation_id = args.simulation_id
    alpha_id = getattr(args, "alpha_id", None)
    convert_audit_file: pathlib.Path | None = None

    if not args.skip_create:
        probe_path = PROBES / f"{args.candidate_id}.json"
        if not probe_path.exists():
            probe_event = run_json(
                [py, str(SCRIPTS / "probe_simulation.py"), "--candidate-id", args.candidate_id]
            )
            summary["steps"].append({"name": "probe", "status": probe_event.get("status")})

        create_cmd = [py, str(SCRIPTS / "run_live_simulation_create.py"), "--candidate-id", args.candidate_id]
        if args.target_id:
            create_cmd.extend(["--target-id", args.target_id])
        create_event = run_json(create_cmd)
        summary["steps"].append({"name": "create", "classification": create_event.get("classification")})
        simulation_id = create_event.get("simulation_id") or simulation_id
        if create_event.get("classification") not in {"submitted", "accepted_without_id"}:
            summary["stopped_after"] = "create"
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

    if not args.skip_result:
        if not simulation_id:
            raise ValueError("simulation_id is required to query live simulation results.")
        result_cmd = [
            py,
            str(SCRIPTS / "run_live_simulation_result.py"),
            "--candidate-id",
            args.candidate_id,
            "--simulation-id",
            str(simulation_id),
        ]
        if args.target_id:
            result_cmd.extend(["--target-id", args.target_id])
        result_event = run_json(result_cmd)
        summary["steps"].append({"name": "result", "classification": result_event.get("classification")})
        alpha_id = result_event.get("alpha_id") or alpha_id
        if result_event.get("classification") != "fetched":
            summary["stopped_after"] = "result"
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

    if not getattr(args, "skip_alpha_detail", False) and alpha_id:
        alpha_cmd = [
            py,
            str(SCRIPTS / "run_live_alpha_detail.py"),
            "--candidate-id",
            args.candidate_id,
            "--alpha-id",
            str(alpha_id),
        ]
        if simulation_id:
            alpha_cmd.extend(["--simulation-id", str(simulation_id)])
        if args.target_id:
            alpha_cmd.extend(["--target-id", args.target_id])
        alpha_event = run_json(alpha_cmd)
        summary["steps"].append({"name": "alpha_detail", "classification": alpha_event.get("classification")})
        alpha_id = alpha_event.get("alpha_id") or alpha_id
        if alpha_event.get("classification") != "fetched":
            summary["stopped_after"] = "alpha_detail"
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        convert_audit_file = ROOT / "state" / "audit" / f"{args.candidate_id}-alpha-detail.json"

    if not args.skip_convert:
        convert_cmd = [py, str(SCRIPTS / "convert_live_result_to_import.py"), "--candidate-id", args.candidate_id]
        if convert_audit_file:
            convert_cmd.extend(["--audit-file", str(convert_audit_file)])
        convert_event = run_json(convert_cmd)
        summary["steps"].append({"name": "convert", "output": convert_event.get("output")})

    if not args.skip_import:
        import_path = ROOT / "state" / "imports" / f"{args.candidate_id}-from-live.json"
        import_event = run_json([py, str(SCRIPTS / "import_result.py"), "--input", str(import_path)])
        summary["steps"].append({"name": "import", "status": import_event.get("status")})

    if not args.skip_review:
        review_event = run_json([py, str(SCRIPTS / "review_result.py"), "--candidate-id", args.candidate_id])
        summary["steps"].append({"name": "review", "decision": review_event.get("decision")})

    summary["completed"] = True
    summary["simulation_id"] = simulation_id
    if alpha_id:
        summary["alpha_id"] = alpha_id
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
