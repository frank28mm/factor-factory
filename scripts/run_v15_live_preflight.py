#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from verify_official_course_read_gate import build_payload as build_official_course_read_gate


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LEDGER = ROOT / "state" / "ledger"
AUDIT = ROOT / "state" / "audit"
LIVE_PROCESS_PATTERNS = [
    "run_wq_sync_loop.py",
    "run_probe_pool_batch.py",
    "run_live_simulation",
    "run_live_alpha",
    "probe_simulation",
    "submit_ready_alphas.py",
]


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight a V1.5 WorldQuant live simulation loop without platform actions.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-running", type=int, default=3)
    parser.add_argument("--pending-refresh-limit", type=int, default=3)
    parser.add_argument("--waiting-refresh-limit", type=int, default=2)
    parser.add_argument("--probe-batch-limit", type=int, default=3)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--max-cycles", type=int, default=1)
    parser.add_argument("--target-id")
    parser.add_argument("--pool-id")
    parser.add_argument("--batch-id")
    return parser.parse_args()


def ensure_ledgers() -> dict[str, Any]:
    events = []
    for script in ("build_pending_runs.py", "build_ledgers.py", "build_retrospectives.py", "export_visual_ledger.py"):
        events.append({"script": script, "result": run_json([sys.executable, str(SCRIPTS / script)])})
    return {"events": events}


def live_processes() -> list[str]:
    result = subprocess.run(["ps", "aux"], check=True, capture_output=True, text=True)
    lines = []
    current_pid = str(__import__("os").getpid())
    for line in result.stdout.splitlines():
        if current_pid in line:
            continue
        if any(pattern in line for pattern in LIVE_PROCESS_PATTERNS):
            lines.append(line)
    return lines


def load_probe_inventory() -> dict[str, Any]:
    payload = read_json(LEDGER / "probe-pool.json")
    ready_pool = payload.get("ready_pool", []) if isinstance(payload, dict) else []
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    return {
        "summary": summary,
        "ready_pool": ready_pool if isinstance(ready_pool, list) else [],
    }


def pending_rows() -> list[dict[str, Any]]:
    path = LEDGER / "pending-runs.json"
    if not path.exists():
        return []
    payload = read_json(path)
    return payload if isinstance(payload, list) else []


def submission_summary() -> dict[str, Any]:
    path = LEDGER / "submission-pool.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload.get("summary", {}) if isinstance(payload, dict) else {}


def sync_loop_command(args: argparse.Namespace, *, dry_run: bool, offline_plan: bool = False) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_wq_sync_loop.py"),
        "--run-id",
        args.run_id,
        "--max-cycles",
        str(max(0, int(args.max_cycles))),
        "--interval-seconds",
        str(max(0, int(args.interval_seconds))),
        "--max-running",
        str(max(0, int(args.max_running))),
        "--pending-refresh-limit",
        str(max(0, int(args.pending_refresh_limit))),
        "--waiting-refresh-limit",
        str(max(0, int(args.waiting_refresh_limit))),
        "--probe-batch-limit",
        str(max(0, int(args.probe_batch_limit))),
    ]
    for flag, value in (("--target-id", args.target_id), ("--pool-id", args.pool_id), ("--batch-id", args.batch_id)):
        if value:
            command.extend([flag, value])
    if dry_run:
        command.append("--dry-run")
    if offline_plan:
        command.append("--offline-plan")
    return command


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    official_course_gate = build_official_course_read_gate()
    maintenance = ensure_ledgers()
    processes = live_processes()
    probe_inventory = load_probe_inventory()
    pending = pending_rows()
    submission = submission_summary()
    pending_count = len(pending)
    max_running = max(0, int(args.max_running))
    open_slots = max(0, max_running - pending_count)
    ready_pool = probe_inventory["ready_pool"]
    planned_probe_launch_count = min(max(0, int(args.probe_batch_limit)), open_slots, len(ready_pool))
    can_start = (
        official_course_gate["confirmed"]
        and not processes
        and len(ready_pool) > 0
        and open_slots > 0
    )
    blockers = []
    if not official_course_gate["confirmed"]:
        blockers.append("official_course_read_gate_failed")
    if processes:
        blockers.append("live_process_already_running")
    if not ready_pool:
        blockers.append("no_probe_ready_candidates")
    if open_slots <= 0:
        blockers.append("no_open_simulation_slots")
    payload = {
        "run_id": args.run_id,
        "mode": "preflight_only",
        "live_platform_actions": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "official_course_gate": {
            "confirmed": official_course_gate["confirmed"],
            "summary": official_course_gate["summary"],
        },
        "process_check": {
            "live_process_count": len(processes),
            "live_processes": processes,
        },
        "inventory": {
            "probe_ready_count": len(ready_pool),
            "official_tested_count": probe_inventory["summary"].get("official_tested_count", 0),
            "official_core_passed_count": probe_inventory["summary"].get("official_core_passed_count", 0),
            "waiting_checks_count": probe_inventory["summary"].get("waiting_checks_count", 0),
            "submit_ready_count": probe_inventory["summary"].get("submit_ready_count", 0),
            "submitted_count": probe_inventory["summary"].get("submitted_count", 0),
            "pending_count": pending_count,
            "submission_ready_count": submission.get("ready_count", 0),
            "remaining_submission_quota": submission.get("remaining_submission_quota", 0),
            "submission_gate_locked": bool(submission.get("submission_gate_locked", False)),
        },
        "plan": {
            "can_start_live_loop_after_user_confirmation": can_start,
            "blockers": blockers,
            "max_running": max_running,
            "open_slots": open_slots,
            "probe_batch_limit": max(0, int(args.probe_batch_limit)),
            "planned_probe_launch_count": planned_probe_launch_count,
            "selected_candidates_preview": [
                {
                    "candidate_id": row.get("candidate_id"),
                    "task_pool_id": row.get("task_pool_id"),
                    "screen_decision": row.get("screen_decision"),
                    "priority": row.get("priority"),
                }
                for row in ready_pool[:planned_probe_launch_count]
                if isinstance(row, dict)
            ],
        },
        "commands": {
            "offline_plan_sync_loop": sync_loop_command(args, dry_run=True, offline_plan=True),
            "dry_run_sync_loop": sync_loop_command(args, dry_run=True),
            "live_sync_loop": sync_loop_command(args, dry_run=False),
        },
        "maintenance": maintenance,
        "requires_user_confirmation_for_live": True,
    }
    return payload


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    write_json(AUDIT / f"v15-live-preflight-{args.run_id}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
