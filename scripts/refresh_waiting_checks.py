#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LEDGER = ROOT / "state" / "ledger"
RANKING = ["core_metrics_passed", "no_failed_checks", "grade", "fitness", "sharpe", "test_sharpe", "returns", "drawdown_asc"]
GRADE_SCORE = {"SPECTACULAR": 4, "EXCELLENT": 3, "GOOD": 2, "AVERAGE": 1, "INFERIOR": 0}


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def alpha_detail_payload(audit_file: pathlib.Path) -> dict[str, Any]:
    try:
        event = read_json(audit_file)
    except FileNotFoundError:
        return {}
    payload = event.get("response", {}).get("payload")
    return payload if isinstance(payload, dict) else {}


def alpha_detail_is_submitted(audit_file: pathlib.Path) -> bool:
    payload = alpha_detail_payload(audit_file)
    if payload.get("dateSubmitted"):
        return True
    return str(payload.get("status", "")).upper() == "ACTIVE"


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def metric_value(result: dict[str, Any], name: str) -> float:
    value = result.get("metrics", {}).get(name)
    return float(value) if isinstance(value, (int, float)) else 0.0


def next_refresh_command(item: dict[str, Any]) -> str:
    parts = [
        sys.executable,
        str(SCRIPTS / "run_live_alpha_detail.py"),
        "--candidate-id",
        item["candidate_id"],
        "--alpha-id",
        item["alpha_id"],
    ]
    if item.get("simulation_id"):
        parts.extend(["--simulation-id", str(item["simulation_id"])])
    return " ".join(parts)


def next_check_command(item: dict[str, Any]) -> str:
    parts = [
        sys.executable,
        str(SCRIPTS / "run_live_alpha_check.py"),
        "--candidate-id",
        item["candidate_id"],
        "--alpha-id",
        item["alpha_id"],
    ]
    if item.get("simulation_id"):
        parts.extend(["--simulation-id", str(item["simulation_id"])])
    return " ".join(parts)


def waiting_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, float, float, float, float]:
    metrics = item.get("metrics", {})
    return (
        1.0 if item.get("core_metrics_passed") else 0.0,
        1.0 if not item.get("failed_checks") else 0.0,
        float(GRADE_SCORE.get(str(item.get("grade", "")).upper(), -1)),
        float(metrics.get("fitness") or 0.0),
        float(metrics.get("sharpe") or 0.0),
        float(metrics.get("test_sharpe") or 0.0),
        float(metrics.get("returns") or 0.0),
        -float(metrics.get("drawdown") or 0.0),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh alpha detail for candidates waiting on platform checks.")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-id", help="Optional explicit CDP target id.")
    return parser.parse_args()


def waiting_candidates() -> list[dict[str, Any]]:
    result_rows = {row["candidate_id"]: row for row in read_json(LEDGER / "result-ledger.json")}
    iteration_rows = read_json(LEDGER / "iteration-ledger.json")
    waiting = []
    for row in iteration_rows:
        if row.get("review_lane") != "manual_gate_wait_checks":
            continue
        result = result_rows.get(row["candidate_id"])
        if not result or not result.get("alpha_id"):
            continue
        waiting.append(
            {
                "candidate_id": row["candidate_id"],
                "alpha_id": result["alpha_id"],
                "simulation_id": result.get("simulation_id"),
                "grade": result.get("grade", ""),
                "metrics": result.get("metrics", {}),
                "failed_checks": result.get("failed_checks", []),
                "pending_checks": result.get("pending_checks", []),
                "core_metrics_passed": bool(result.get("core_metrics_passed")),
                "review_lane": row.get("review_lane"),
            }
        )
    waiting = sorted(waiting, key=waiting_sort_key, reverse=True)
    for index, item in enumerate(waiting, start=1):
        item["priority_rank"] = index
        item["next_refresh_command"] = next_refresh_command(item)
        item["next_check_command"] = next_check_command(item)
    return waiting


def refresh(args: argparse.Namespace) -> dict[str, Any]:
    py = sys.executable
    waiting = waiting_candidates()
    if args.dry_run:
        selected = waiting[: args.limit]
        return {
            "dry_run": True,
            "policy": {
                "auto_submit": False,
                "default_limit": 2,
                "rule_cn": "只低频刷新官方 alpha detail 并回填本地台账；不自动提交。",
            },
            "ranking": RANKING,
            "limit": args.limit,
            "waiting_count": len(waiting),
            "returned_count": len(selected),
            "omitted_count": max(0, len(waiting) - len(selected)),
            "candidates": selected,
            "omitted_candidate_ids": [item["candidate_id"] for item in waiting[args.limit :]],
        }

    refreshed = []
    steps = []
    for item in waiting[: args.limit]:
        candidate_id = item["candidate_id"]
        alpha_id = item["alpha_id"]
        simulation_id = item.get("simulation_id")
        alpha_cmd = [
            py,
            str(SCRIPTS / "run_live_alpha_detail.py"),
            "--candidate-id",
            candidate_id,
            "--alpha-id",
            alpha_id,
        ]
        if simulation_id:
            alpha_cmd.extend(["--simulation-id", str(simulation_id)])
        if args.target_id:
            alpha_cmd.extend(["--target-id", args.target_id])
        alpha_event = run_json(alpha_cmd)
        steps.append({"candidate_id": candidate_id, "step": "alpha_detail", "classification": alpha_event.get("classification")})
        if alpha_event.get("classification") != "fetched":
            continue

        audit_file = ROOT / "state" / "audit" / f"{candidate_id}-alpha-detail.json"
        check_event: dict[str, Any] = {"classification": "skipped_already_submitted"} if alpha_detail_is_submitted(audit_file) else {}
        if check_event:
            steps.append({"candidate_id": candidate_id, "step": "alpha_check", "classification": check_event["classification"]})
        else:
            check_cmd = [
                py,
                str(SCRIPTS / "run_live_alpha_check.py"),
                "--candidate-id",
                candidate_id,
                "--alpha-id",
                alpha_id,
            ]
            if simulation_id:
                check_cmd.extend(["--simulation-id", str(simulation_id)])
            if args.target_id:
                check_cmd.extend(["--target-id", args.target_id])
            check_event = run_json(check_cmd)
            steps.append({"candidate_id": candidate_id, "step": "alpha_check", "classification": check_event.get("classification")})

        convert_cmd = [
            py,
            str(SCRIPTS / "convert_live_result_to_import.py"),
            "--candidate-id",
            candidate_id,
            "--audit-file",
            str(audit_file),
        ]
        if check_event.get("classification") == "fetched":
            convert_cmd.extend(["--check-audit-file", str(ROOT / "state" / "audit" / f"{candidate_id}-alpha-check.json")])
        convert_event = run_json(convert_cmd)
        import_path = ROOT / "state" / "imports" / f"{candidate_id}-from-live.json"
        import_event = run_json([py, str(SCRIPTS / "import_result.py"), "--input", str(import_path)])
        review_event = run_json([py, str(SCRIPTS / "review_result.py"), "--candidate-id", candidate_id])
        steps.extend(
            [
                {"candidate_id": candidate_id, "step": "convert", "output": convert_event.get("output")},
                {"candidate_id": candidate_id, "step": "import", "status": import_event.get("status")},
                {"candidate_id": candidate_id, "step": "review", "decision": review_event.get("decision")},
            ]
        )
        refreshed.append(candidate_id)

    ledger_event = run_json([py, str(SCRIPTS / "build_ledgers.py")])
    retro_event = run_json([py, str(SCRIPTS / "build_retrospectives.py")])
    visual_event = run_json([py, str(SCRIPTS / "export_visual_ledger.py")])
    return {
        "dry_run": False,
        "waiting_count": len(waiting),
        "refreshed_count": len(refreshed),
        "refreshed_candidate_ids": refreshed,
        "steps": steps,
        "ledger": ledger_event,
        "retrospectives": retro_event,
        "visual": visual_event,
    }


def main() -> int:
    print(json.dumps(refresh(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
