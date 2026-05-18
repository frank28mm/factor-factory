#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT = ROOT / "state" / "audit"
sys.path.insert(0, str(ROOT))

from connectors.worldquant_brain.session_client import browser_fetch_json_response  # noqa: E402
from connectors.worldquant_brain.session_probe import find_worldquant_target_id  # noqa: E402


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Low-impact WQ session watchdog.")
    parser.add_argument("--target-id", help="Optional explicit WorldQuant BRAIN CDP target id.")
    parser.add_argument("--run-id", help="Optional audit run id.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def classify(response: dict[str, object]) -> dict[str, object]:
    status = int(response.get("status", 0) or 0)
    payload = response.get("payload")
    if status == 200 and isinstance(payload, dict):
        return {
            "classification": "authenticated",
            "authenticated": True,
            "status": status,
            "user_id": payload.get("id") or payload.get("userId") or payload.get("user_id") or "",
        }
    if status in {401, 403}:
        return {"classification": "auth_required", "authenticated": False, "status": status}
    if status == 429:
        return {"classification": "rate_limited", "authenticated": False, "status": status}
    if status >= 500:
        return {"classification": "upstream_error", "authenticated": False, "status": status}
    return {"classification": "failed", "authenticated": False, "status": status}


def main() -> int:
    args = parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.dry_run:
        event = {
            "run_id": run_id,
            "dry_run": True,
            "classification": "authenticated",
            "authenticated": True,
            "reason": "dry_run_assumes_session_ok",
        }
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0

    try:
        target_id = args.target_id or find_worldquant_target_id()
        response = browser_fetch_json_response(target_id, "/users/self")
        event = {
            "run_id": run_id,
            "target_id": target_id,
            "endpoint": "/users/self",
            **classify(response),
        }
    except Exception as exc:
        event = {
            "run_id": run_id,
            "endpoint": "/users/self",
            "classification": "auth_required",
            "authenticated": False,
            "error": str(exc),
        }

    write_json(AUDIT / f"session-watchdog-{run_id}.json", event)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
