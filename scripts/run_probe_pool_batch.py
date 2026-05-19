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


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def command_error_text(error: subprocess.CalledProcessError) -> str:
    text = "\n".join(part for part in (error.stderr, error.stdout) if part)
    return text.strip() or str(error)


def subprocess_error_event(command: list[str], error: subprocess.CalledProcessError) -> dict[str, Any]:
    return {
        "classification": "subprocess_error",
        "stopped": {"reason": "subprocess_error"},
        "returncode": error.returncode,
        "command": " ".join(command),
        "error": command_error_text(error),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automatically send locally screened candidates to official simulation.")
    parser.add_argument("--limit", type=int, default=3, help="Maximum candidates to send in this batch.")
    parser.add_argument("--pool-id", help="Only launch candidates from this task pool.")
    parser.add_argument("--batch-id", help="Only launch candidates from this task pool batch.")
    parser.add_argument("--target-id", help="Optional explicit WorldQuant BRAIN CDP target id.")
    parser.add_argument("--run-id", help="Optional audit run id.")
    parser.add_argument("--dry-run", action="store_true", help="List selected candidates without live platform calls.")
    return parser.parse_args()


def load_probe_pool() -> dict[str, Any]:
    path = LEDGER / "probe-pool.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    return read_json(path)


def simulation_command(candidate_id: str, target_id: str | None = None) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_live_simulation_pipeline.py"),
        "--candidate-id",
        candidate_id,
    ]
    if target_id:
        command.extend(["--target-id", target_id])
    return command


def select_candidates(
    probe_pool: dict[str, Any],
    pool_id: str | None,
    batch_id: str | None,
    limit: int,
    target_id: str | None,
) -> list[dict[str, Any]]:
    selected = []
    for row in probe_pool.get("ready_pool", []):
        if pool_id and row.get("task_pool_id") != pool_id:
            continue
        if batch_id and row.get("task_pool_batch_id") != batch_id:
            continue
        command = simulation_command(str(row["candidate_id"]), target_id)
        selected.append(
            {
                **row,
                "next_probe_command": " ".join(command),
            }
        )
        if len(selected) >= max(0, limit):
            break
    return selected


def stop_reason(event: dict[str, Any]) -> str | None:
    classification = event.get("classification")
    if classification in STOP_CLASSIFICATIONS or classification == "subprocess_error":
        return str(classification)
    stopped = event.get("stopped")
    if isinstance(stopped, dict) and stopped.get("reason"):
        return str(stopped["reason"])
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
    probe_pool = load_probe_pool()
    selected = select_candidates(probe_pool, args.pool_id, args.batch_id, args.limit, args.target_id)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "auto_probe": bool(probe_pool.get("policy", {}).get("auto_probe", True)),
        "auto_submit": bool(probe_pool.get("policy", {}).get("auto_submit", False)),
        "pool_id": args.pool_id or "",
        "batch_id": args.batch_id or "",
        "limit": args.limit,
        "selected_count": len(selected),
        "selected_candidates": selected,
    }

    if args.dry_run:
        return payload

    launched = []
    stopped = None
    for row in selected:
        candidate_id = str(row["candidate_id"])
        command = simulation_command(candidate_id, args.target_id)
        try:
            event = run_json(command)
        except subprocess.CalledProcessError as error:
            event = subprocess_error_event(command, error)
        launched.append(
            {
                "candidate_id": candidate_id,
                "command": " ".join(command),
                "event": event,
            }
        )
        reason = stop_reason(event)
        if reason:
            stopped = {"candidate_id": candidate_id, "reason": reason}
            break

    payload["launched_count"] = len(launched)
    payload["launched"] = launched
    payload["stopped"] = stopped
    payload["maintenance"] = run_maintenance()
    write_json(AUDIT / f"probe-batch-{run_id}.json", payload)
    return payload


def main() -> int:
    print(json.dumps(run_batch(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
