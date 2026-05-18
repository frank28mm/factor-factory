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


def read_json(path: pathlib.Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh running WorldQuant simulation results and rebuild ledgers.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--target-id", help="Optional explicit WorldQuant BRAIN CDP target id.")
    parser.add_argument("--run-id", help="Optional audit run id.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def pending_runs() -> list[dict[str, Any]]:
    path = LEDGER / "pending-runs.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_pending_runs.py")])
    return read_json(path)


def refresh_command(row: dict[str, Any], target_id: str | None) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_live_simulation_pipeline.py"),
        "--candidate-id",
        str(row["candidate_id"]),
        "--simulation-id",
        str(row["simulation_id"]),
        "--skip-create",
    ]
    if target_id:
        command.extend(["--target-id", target_id])
    return command


def selected_runs(limit: int, target_id: str | None) -> list[dict[str, Any]]:
    selected = []
    for row in pending_runs()[: max(0, limit)]:
        command = refresh_command(row, target_id)
        selected.append({**row, "next_refresh_command": " ".join(command)})
    return selected


def stop_reason(event: dict[str, Any]) -> str | None:
    for step in event.get("steps", []):
        if not isinstance(step, dict):
            continue
        classification = step.get("classification")
        if classification in STOP_CLASSIFICATIONS:
            return str(classification)
    return None


def run_maintenance() -> list[dict[str, Any]]:
    events = []
    for script in ("build_pending_runs.py", "build_ledgers.py", "build_retrospectives.py", "export_visual_ledger.py"):
        events.append({"script": script, "result": run_json([sys.executable, str(SCRIPTS / script)])})
    return events


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    selected = selected_runs(args.limit, args.target_id)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "auto_probe": True,
        "auto_submit": False,
        "limit": args.limit,
        "selected_count": len(selected),
        "selected_runs": selected,
    }

    if args.dry_run:
        return payload

    queried = []
    stopped = None
    for row in selected:
        command = refresh_command(row, args.target_id)
        event = run_json(command)
        queried.append(
            {
                "candidate_id": row["candidate_id"],
                "simulation_id": row["simulation_id"],
                "command": " ".join(command),
                "event": event,
            }
        )
        reason = stop_reason(event)
        if reason:
            stopped = {"candidate_id": row["candidate_id"], "reason": reason}
            break

    payload["queried_count"] = len(queried)
    payload["queried"] = queried
    payload["stopped"] = stopped
    payload["maintenance"] = run_maintenance()
    write_json(AUDIT / f"pending-refresh-{run_id}.json", payload)
    return payload


def main() -> int:
    print(json.dumps(run_batch(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
