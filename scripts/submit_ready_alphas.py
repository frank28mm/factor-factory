#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LEDGER = ROOT / "state" / "ledger"
AUDIT = ROOT / "state" / "audit"
STOP_CLASSIFICATIONS = {"auth_required", "rate_limited", "upstream_error"}
SUCCESS_CLASSIFICATIONS = {"submitted", "accepted", "already_submitted"}


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automatically submit locally qualified WorldQuant alpha candidates.")
    parser.add_argument("--limit", type=int, help="Max submit-ready alphas to submit this batch. Defaults to remaining quota.")
    parser.add_argument("--target-id", help="Optional explicit WorldQuant BRAIN CDP target id.")
    parser.add_argument("--run-id", help="Optional audit run id.")
    parser.add_argument("--dry-run", action="store_true", help="List selected alphas without live submit calls.")
    return parser.parse_args()


def load_submission_pool() -> dict[str, Any]:
    path = LEDGER / "submission-pool.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def append_optional(command: list[str], flag: str, value: str | None) -> None:
    if value:
        command.extend([flag, value])


def submit_command(candidate_id: str, alpha_id: str, target_id: str | None = None) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_live_alpha_submit.py"),
        "--candidate-id",
        candidate_id,
        "--alpha-id",
        alpha_id,
    ]
    append_optional(command, "--target-id", target_id)
    return command


def alpha_detail_command(candidate_id: str, alpha_id: str, target_id: str | None = None) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_live_alpha_detail.py"),
        "--candidate-id",
        candidate_id,
        "--alpha-id",
        alpha_id,
    ]
    append_optional(command, "--target-id", target_id)
    return command


def select_candidates(submission_pool: dict[str, Any], limit: int | None, target_id: str | None) -> list[dict[str, Any]]:
    summary = submission_pool.get("summary", {})
    remaining_quota = max(0, int(summary.get("remaining_submission_quota") or 0))
    requested_limit = remaining_quota if limit is None else max(0, min(int(limit), remaining_quota))
    selected = []
    for row in submission_pool.get("today_quota", [])[:requested_limit]:
        if not isinstance(row, dict) or not row.get("candidate_id") or not row.get("alpha_id"):
            continue
        command = submit_command(str(row["candidate_id"]), str(row["alpha_id"]), target_id)
        selected.append({**row, "next_submit_command": " ".join(command)})
    return selected


def maintenance() -> list[dict[str, Any]]:
    events = []
    for script in ("build_ledgers.py", "build_retrospectives.py", "export_visual_ledger.py"):
        events.append({"script": script, "result": run_json([sys.executable, str(SCRIPTS / script)])})
    return events


def refresh_after_submit(candidate_id: str, alpha_id: str, target_id: str | None) -> list[dict[str, Any]]:
    steps = []
    detail_event = run_json(alpha_detail_command(candidate_id, alpha_id, target_id))
    steps.append({"step": "alpha_detail", "classification": detail_event.get("classification")})
    if detail_event.get("classification") != "fetched":
        return steps

    audit_file = ROOT / "state" / "audit" / f"{candidate_id}-alpha-detail.json"
    convert_event = run_json(
        [
            sys.executable,
            str(SCRIPTS / "convert_live_result_to_import.py"),
            "--candidate-id",
            candidate_id,
            "--audit-file",
            str(audit_file),
        ]
    )
    import_path = ROOT / "state" / "imports" / f"{candidate_id}-from-live.json"
    import_event = run_json([sys.executable, str(SCRIPTS / "import_result.py"), "--input", str(import_path)])
    review_event = run_json([sys.executable, str(SCRIPTS / "review_result.py"), "--candidate-id", candidate_id])
    steps.extend(
        [
            {"step": "convert", "output": convert_event.get("output")},
            {"step": "import", "status": import_event.get("status")},
            {"step": "review", "decision": review_event.get("decision")},
        ]
    )
    return steps


def stop_from(classification: str | None, candidate_id: str) -> dict[str, str] | None:
    if classification in STOP_CLASSIFICATIONS:
        return {"candidate_id": candidate_id, "reason": str(classification)}
    return None


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    submission_pool = load_submission_pool()
    summary = submission_pool.get("summary", {})
    selected = select_candidates(submission_pool, args.limit, args.target_id)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "auto_submit": True,
        "policy": {
            "source": "submission-pool.today_quota",
            "rule_cn": "只提交本地 submit_ready 且处于当日额度内的 Alpha；提交后刷新 alpha detail 并回填本地账本。",
        },
        "remaining_submission_quota": summary.get("remaining_submission_quota", 0),
        "submission_gate_locked": bool(summary.get("submission_gate_locked", False)),
        "selected_count": len(selected),
        "selected_candidates": selected,
    }
    if args.dry_run:
        return payload

    results = []
    submitted_count = 0
    stopped = None
    for row in selected:
        candidate_id = str(row["candidate_id"])
        alpha_id = str(row["alpha_id"])
        submit_event = run_json(submit_command(candidate_id, alpha_id, args.target_id))
        classification = str(submit_event.get("classification") or "")
        item = {
            "candidate_id": candidate_id,
            "alpha_id": alpha_id,
            "classification": classification,
            "submit_event": submit_event,
            "post_submit_steps": [],
        }
        if classification in SUCCESS_CLASSIFICATIONS:
            submitted_count += 1
            item["post_submit_steps"] = refresh_after_submit(candidate_id, alpha_id, args.target_id)
        results.append(item)
        stopped = stop_from(classification, candidate_id)
        if stopped:
            break

    payload["submitted_count"] = submitted_count
    payload["results"] = results
    payload["stopped"] = stopped
    payload["maintenance"] = maintenance()
    write_json(AUDIT / f"submit-ready-{run_id}.json", payload)
    return payload


def main() -> int:
    print(json.dumps(run_batch(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
