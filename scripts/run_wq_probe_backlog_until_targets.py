#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STATE = ROOT / "state"
QUEUE = STATE / "queue"
LEDGER = STATE / "ledger"
AUDIT = STATE / "audit"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def run_json_soft(command: list[str]) -> dict[str, Any]:
    try:
        return run_json(command)
    except subprocess.CalledProcessError as error:
        return {
            "command_failed": True,
            "returncode": error.returncode,
            "stdout": error.stdout,
            "stderr": error.stderr,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe a WQ simulation backlog without submitting alphas, then stop at explicit targets."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--pool-id", default="profile-stage2-field-blend-v15")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--target-probe-count", type=int, default=20)
    parser.add_argument("--target-core-passed", type=int, default=4)
    parser.add_argument("--max-running", type=int, default=3)
    parser.add_argument("--probe-batch-limit", type=int, default=3)
    parser.add_argument("--pending-refresh-limit", type=int, default=3)
    parser.add_argument("--interval-seconds", type=int, default=180)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run until a target or blocking error.")
    parser.add_argument("--target-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-sleep", action="store_true")
    return parser.parse_args()


def maintenance() -> list[dict[str, Any]]:
    events = []
    for script in ("build_pending_runs.py", "build_ledgers.py", "build_retrospectives.py", "export_visual_ledger.py"):
        events.append({"script": script, "result": run_json([sys.executable, str(SCRIPTS / script)])})
    return events


def candidate_batch_ids(batch_id: str) -> set[str]:
    ids: set[str] = set()
    for path in QUEUE.glob("cand-*.json"):
        try:
            candidate = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict):
            continue
        params = candidate.get("params", {})
        if isinstance(params, dict) and params.get("task_pool_batch_id") == batch_id:
            ids.add(str(candidate.get("candidate_id")))
    return ids


def pending_rows() -> list[dict[str, Any]]:
    path = LEDGER / "pending-runs.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_pending_runs.py")])
    rows = read_json(path)
    return rows if isinstance(rows, list) else []


def probe_ready_rows(pool_id: str, batch_id: str) -> list[dict[str, Any]]:
    path = LEDGER / "probe-pool.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    payload = read_json(path)
    rows = payload.get("ready_pool", []) if isinstance(payload, dict) else []
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("task_pool_id") != pool_id:
            continue
        if row.get("task_pool_batch_id") != batch_id:
            continue
        selected.append(row)
    return selected


def family_key(row: dict[str, Any]) -> str:
    expression = str(row.get("expression") or "").lower()
    template = str(row.get("template_id") or "")
    stage = str(row.get("stage") or "")
    analyst = "analyst"
    for field in ("est_netprofit", "est_ptp", "est_sales", "est_capex", "est_eps"):
        if field in expression:
            analyst = field
            break
    fundamental = "fundamental"
    for field in ("inventory_turnover", "cashflow_op", "operating_income", "sales"):
        if f"{field}/assets" in expression:
            fundamental = field
            break
    gate = "no_gate"
    if "ts_mean(volume" in expression:
        gate = "volume_gate"
    elif "ts_std_dev(returns" in expression:
        gate = "returns_gate"
    return f"{stage}:{template}:{analyst}:{fundamental}:{gate}"


def diversity_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    expression = str(row.get("expression") or "").lower()
    stage_score = int(row.get("stage") or 0)
    non_sales_score = 0 if "sales/assets" in expression else 1
    return (
        stage_score,
        non_sales_score,
        family_key(row),
        str(row.get("candidate_id") or ""),
    )


def select_diverse_ready_rows(pool_id: str, batch_id: str, limit: int) -> list[dict[str, Any]]:
    rows = sorted(probe_ready_rows(pool_id, batch_id), key=diversity_sort_key, reverse=True)
    selected: list[dict[str, Any]] = []
    used_families: set[str] = set()
    for row in rows:
        key = family_key(row)
        if key in used_families:
            continue
        selected.append(row)
        used_families.add(key)
        if len(selected) >= max(0, limit):
            return selected
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "")
        if any(candidate_id == str(selected_row.get("candidate_id") or "") for selected_row in selected):
            continue
        selected.append(row)
        if len(selected) >= max(0, limit):
            break
    return selected


def result_rows_for_batch(batch_id: str) -> list[dict[str, Any]]:
    ids = candidate_batch_ids(batch_id)
    path = LEDGER / "result-ledger.json"
    if not path.exists():
        return []
    rows = read_json(path)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and str(row.get("candidate_id")) in ids]


def launched_count_for_batch(batch_id: str) -> int:
    ids = candidate_batch_ids(batch_id)
    count = 0
    for candidate_id in ids:
        path = QUEUE / f"{candidate_id}.json"
        try:
            candidate = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict) and candidate.get("latest_simulation_id"):
            count += 1
    return count


def batch_status(pool_id: str, batch_id: str) -> dict[str, Any]:
    results = result_rows_for_batch(batch_id)
    core_passed = [row for row in results if row.get("core_metrics_passed")]
    submit_ready = [row for row in results if row.get("submit_ready")]
    return {
        "batch_candidate_count": len(candidate_batch_ids(batch_id)),
        "batch_launched_count": launched_count_for_batch(batch_id),
        "batch_result_count": len(results),
        "batch_core_passed_count": len(core_passed),
        "batch_submit_ready_count": len(submit_ready),
        "pending_count": len(pending_rows()),
        "probe_ready_count": len(probe_ready_rows(pool_id, batch_id)),
    }


def refresh_pending(args: argparse.Namespace) -> dict[str, Any]:
    selected = pending_rows()[: max(0, int(args.pending_refresh_limit))]
    events = []
    for row in selected:
        command = [
            sys.executable,
            str(SCRIPTS / "run_live_simulation_pipeline.py"),
            "--candidate-id",
            str(row["candidate_id"]),
            "--simulation-id",
            str(row["simulation_id"]),
            "--skip-create",
        ]
        if args.target_id:
            command.extend(["--target-id", args.target_id])
        if args.dry_run:
            events.append({"candidate_id": row["candidate_id"], "simulation_id": row["simulation_id"], "dry_run": True})
        else:
            events.append(
                {
                    "candidate_id": row["candidate_id"],
                    "simulation_id": row["simulation_id"],
                    "command": " ".join(command),
                    "event": run_json_soft(command),
                }
            )
    return {"selected_count": len(selected), "events": events}


def launch_probe_batch(args: argparse.Namespace, limit: int) -> dict[str, Any]:
    selected = select_diverse_ready_rows(args.pool_id, args.batch_id, max(0, int(limit)))
    launched = []
    stopped = None
    for row in selected:
        candidate_id = str(row["candidate_id"])
        command = [
            sys.executable,
            str(SCRIPTS / "run_live_simulation_pipeline.py"),
            "--candidate-id",
            candidate_id,
        ]
        if args.target_id:
            command.extend(["--target-id", args.target_id])
        if args.dry_run:
            event = {"dry_run": True, "candidate_id": candidate_id}
        else:
            event = run_json_soft(command)
        launched.append(
            {
                "candidate_id": candidate_id,
                "family_key": family_key(row),
                "command": " ".join(command),
                "event": event,
            }
        )
        for step in event.get("steps", []) if isinstance(event, dict) else []:
            if not isinstance(step, dict):
                continue
            classification = step.get("classification")
            if classification in {"auth_required", "rate_limited", "upstream_error"}:
                stopped = {"candidate_id": candidate_id, "reason": str(classification)}
                break
        if stopped:
            break
    return {
        "selected_count": len(selected),
        "launched_count": len(launched),
        "selected_candidates": [
            {
                "candidate_id": str(row.get("candidate_id") or ""),
                "family_key": family_key(row),
                "expression": row.get("expression"),
            }
            for row in selected
        ],
        "launched": launched,
        "stopped": stopped,
    }


def stop_reason(args: argparse.Namespace, status: dict[str, Any]) -> str | None:
    if status["batch_core_passed_count"] >= max(0, int(args.target_core_passed)):
        return "target_core_passed_reached"
    if status["batch_launched_count"] >= max(0, int(args.target_probe_count)):
        return "target_probe_count_reached"
    if status["probe_ready_count"] <= 0 and status["pending_count"] <= 0:
        return "no_ready_or_pending_candidates"
    return None


def run_controller(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": args.run_id,
        "batch_id": args.batch_id,
        "pool_id": args.pool_id,
        "started_at": now_iso(),
        "dry_run": bool(args.dry_run),
        "auto_submit": False,
        "targets": {
            "target_probe_count": args.target_probe_count,
            "target_core_passed": args.target_core_passed,
        },
        "cycles": [],
    }
    max_cycles = float("inf") if args.max_cycles == 0 else max(0, int(args.max_cycles))
    cycle_index = 0

    while cycle_index < max_cycles:
        cycle_index += 1
        cycle: dict[str, Any] = {"cycle": cycle_index, "started_at": now_iso()}
        cycle["maintenance_before"] = maintenance()
        before = batch_status(args.pool_id, args.batch_id)
        cycle["status_before"] = before

        reason = stop_reason(args, before)
        if reason:
            cycle["stopped"] = {"reason": reason, "at": "before_actions"}
            payload["cycles"].append(cycle)
            payload["stopped"] = cycle["stopped"]
            break

        cycle["pending_refresh"] = refresh_pending(args)
        cycle["maintenance_after_refresh"] = maintenance()
        after_refresh = batch_status(args.pool_id, args.batch_id)
        cycle["status_after_refresh"] = after_refresh

        reason = stop_reason(args, after_refresh)
        if reason:
            cycle["stopped"] = {"reason": reason, "at": "after_refresh"}
            payload["cycles"].append(cycle)
            payload["stopped"] = cycle["stopped"]
            break

        open_slots = max(0, int(args.max_running) - int(after_refresh["pending_count"]))
        remaining = max(0, int(args.target_probe_count) - int(after_refresh["batch_launched_count"]))
        launch_limit = min(max(0, int(args.probe_batch_limit)), open_slots, remaining)
        cycle["open_slots"] = open_slots
        cycle["launch_limit"] = launch_limit

        if launch_limit > 0:
            cycle["probe_batch"] = launch_probe_batch(args, launch_limit)
        else:
            cycle["probe_batch"] = {"selected_count": 0, "reason": "no_open_slots_or_no_remaining_target"}

        cycle["maintenance_after_launch"] = maintenance()
        cycle["status_after_launch"] = batch_status(args.pool_id, args.batch_id)
        cycle["finished_at"] = now_iso()
        payload["cycles"].append(cycle)
        payload["cycles_completed"] = cycle_index
        write_json(AUDIT / f"wq-probe-backlog-{args.run_id}.json", payload)

        reason = stop_reason(args, cycle["status_after_launch"])
        if reason:
            payload["stopped"] = {"reason": reason, "at": "after_launch"}
            break
        if args.dry_run or args.no_sleep:
            break
        time.sleep(max(0, int(args.interval_seconds)))

    payload["finished_at"] = now_iso()
    payload["final_status"] = batch_status(args.pool_id, args.batch_id)
    write_json(AUDIT / f"wq-probe-backlog-{args.run_id}.json", payload)
    return payload


def main() -> int:
    print(json.dumps(run_controller(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
