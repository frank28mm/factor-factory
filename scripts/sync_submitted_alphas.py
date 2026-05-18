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

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.live_simulation import classify_alpha_detail_response  # noqa: E402
from connectors.worldquant_brain.session_client import browser_fetch_json_response  # noqa: E402
from connectors.worldquant_brain.session_probe import find_worldquant_target_id  # noqa: E402


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync official WorldQuant submitted Alpha rows into local ledgers.")
    parser.add_argument("--limit", type=int, default=10, help="Max submitted alphas to read from the official list.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--target-id", help="Optional explicit WorldQuant BRAIN CDP target id.")
    parser.add_argument("--run-id", help="Optional audit run id.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def endpoint(limit: int, offset: int) -> str:
    return (
        "/users/self/alphas"
        f"?limit={max(1, int(limit))}"
        f"&offset={max(0, int(offset))}"
        "&status!=UNSUBMITTED%1FIS-FAIL"
        "&order=-dateSubmitted"
        "&hidden=false"
    )


def fetch_submitted_alphas(target_id: str, limit: int = 10, offset: int = 0) -> dict[str, Any]:
    return browser_fetch_json_response(target_id, endpoint(limit, offset))


def candidate_by_alpha_id() -> dict[str, dict[str, Any]]:
    path = LEDGER / "result-ledger.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    rows = read_json(path)
    if not isinstance(rows, list):
        return {}
    return {
        str(row["alpha_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("candidate_id") and row.get("alpha_id")
    }


def alpha_payloads(response: dict[str, Any]) -> list[dict[str, Any]]:
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return []
    results = payload.get("results", [])
    return [row for row in results if isinstance(row, dict) and row.get("id")]


def write_alpha_detail_audit(candidate_id: str, alpha_id: str, payload: dict[str, Any], target_id: str) -> pathlib.Path:
    event = {
        "event_id": f"audit-{candidate_id}-alpha-detail",
        "event_type": "alpha_detail",
        "candidate_id": candidate_id,
        "simulation_id": None,
        "alpha_id": alpha_id,
        "created_at": now_iso(),
        "target_id": target_id,
        "classification": classify_alpha_detail_response({"status": 200, "payload": payload}),
        "request": {
            "endpoint": f"/users/self/alphas submitted list row for {alpha_id}",
            "method": "GET",
        },
        "response": {
            "ok": True,
            "status": 200,
            "url": "submitted-list",
            "headers": {"location": None, "contentType": "application/json", "retryAfter": None},
            "text": json.dumps(payload, ensure_ascii=False),
            "payload": payload,
        },
    }
    path = AUDIT / f"{candidate_id}-alpha-detail.json"
    write_json(path, event)
    return path


def sync_candidate(candidate_id: str, alpha_id: str, payload: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    audit_file = write_alpha_detail_audit(candidate_id, alpha_id, payload, target_id)
    steps = []
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
            {"step": "alpha_detail_audit", "classification": "fetched"},
            {"step": "convert", "output": convert_event.get("output")},
            {"step": "import", "status": import_event.get("status")},
            {"step": "review", "decision": review_event.get("decision")},
        ]
    )
    return steps


def maintenance() -> list[dict[str, Any]]:
    events = []
    for script in ("build_ledgers.py", "build_retrospectives.py", "export_visual_ledger.py"):
        events.append({"script": script, "result": run_json([sys.executable, str(SCRIPTS / script)])})
    return events


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_id = args.target_id or find_worldquant_target_id()
    response = fetch_submitted_alphas(target_id, args.limit, args.offset)
    submitted_rows = alpha_payloads(response)
    local_by_alpha = candidate_by_alpha_id()
    matched = []
    unmatched = []
    for payload in submitted_rows:
        alpha_id = str(payload.get("id") or "")
        local_row = local_by_alpha.get(alpha_id)
        if not local_row:
            unmatched.append({"alpha_id": alpha_id, "dateSubmitted": payload.get("dateSubmitted")})
            continue
        matched.append(
            {
                "candidate_id": local_row["candidate_id"],
                "alpha_id": alpha_id,
                "status": payload.get("status"),
                "dateSubmitted": payload.get("dateSubmitted"),
                "grade": payload.get("grade"),
            }
        )

    payload_out: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "endpoint": endpoint(args.limit, args.offset),
        "official_submitted_count": response.get("payload", {}).get("count") if isinstance(response.get("payload"), dict) else None,
        "fetched_count": len(submitted_rows),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "matched": matched,
        "unmatched": unmatched,
    }
    if args.dry_run:
        return payload_out

    results = []
    synced = 0
    for payload in submitted_rows:
        alpha_id = str(payload.get("id") or "")
        local_row = local_by_alpha.get(alpha_id)
        if not local_row:
            continue
        steps = sync_candidate(str(local_row["candidate_id"]), alpha_id, payload, target_id)
        results.append({"candidate_id": local_row["candidate_id"], "alpha_id": alpha_id, "steps": steps})
        synced += 1

    payload_out["synced_count"] = synced
    payload_out["results"] = results
    payload_out["maintenance"] = maintenance()
    write_json(AUDIT / f"sync-submitted-{run_id}.json", payload_out)
    return payload_out


def main() -> int:
    print(json.dumps(run_batch(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
