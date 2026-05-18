from __future__ import annotations

import json
import pathlib
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
QUEUE = ROOT / "state" / "queue"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_candidate_after_live_create(candidate_id: str, classification: str, simulation_id: str | None = None) -> dict[str, Any]:
    candidate_path = QUEUE / f"{candidate_id}.json"
    candidate = read_json(candidate_path)
    history = list(candidate.get("status_history", [candidate["status"]]))
    if classification in {"submitted", "accepted_without_id"}:
        if history[-1] != "ready_for_platform_probe":
            history.append("ready_for_platform_probe")
        if history[-1] != "manual_result_pending":
            history.append("manual_result_pending")
        candidate["status"] = "manual_result_pending"
        candidate["artifact_state"] = "candidate"
    else:
        if history[-1] != "probe_blocked":
            history.append("probe_blocked")
        candidate["status"] = "probe_blocked"
    candidate["status_history"] = history
    if simulation_id:
        candidate["latest_simulation_id"] = simulation_id
    candidate["last_live_create_classification"] = classification
    write_json(candidate_path, candidate)
    return candidate
