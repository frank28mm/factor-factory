#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT = ROOT / "state" / "audit"
LEDGER = ROOT / "state" / "ledger"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_result_events() -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for path in sorted(AUDIT.glob("cand-*-simulation-result.json")):
        event = read_json(path)
        candidate_id = event.get("candidate_id")
        if candidate_id:
            events[str(candidate_id)] = event
    return events


def retry_after_seconds(event: dict[str, Any]) -> float | None:
    headers = event.get("response", {}).get("headers", {})
    value = headers.get("retryAfter") if isinstance(headers, dict) else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def progress(event: dict[str, Any]) -> float | None:
    payload = event.get("response", {}).get("payload", {})
    value = payload.get("progress") if isinstance(payload, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def main() -> int:
    pending = []
    for event in latest_result_events().values():
        if event.get("classification") != "running":
            continue
        candidate_id = str(event["candidate_id"])
        simulation_id = str(event["simulation_id"])
        pending.append(
            {
                "candidate_id": candidate_id,
                "simulation_id": simulation_id,
                "classification": "running",
                "progress": progress(event),
                "retry_after_seconds": retry_after_seconds(event),
                "last_checked_at": event.get("created_at"),
                "next_query_command": (
                    f"{pathlib.Path(sys.executable).name} scripts/run_live_simulation_result.py "
                    f"--candidate-id {candidate_id} --simulation-id {simulation_id}"
                ),
            }
        )
    write_json(LEDGER / "pending-runs.json", pending)
    print(json.dumps({"pending_runs": len(pending)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
