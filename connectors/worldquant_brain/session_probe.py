from __future__ import annotations

from typing import Any

from .contracts import CONTRACT_ENDPOINTS, expected_contract_shape
from .session_client import browser_fetch_json_response, find_worldquant_target_id


def shape_ok(name: str, payload: Any) -> dict[str, object]:
    contract = expected_contract_shape()[name]
    expected_type = contract.get("type")
    ok = True
    details: dict[str, object] = {}

    if expected_type == "dict":
        ok = isinstance(payload, dict)
        details["type"] = "dict" if isinstance(payload, dict) else type(payload).__name__
        for key in contract.get("required_keys", []):
            if not isinstance(payload, dict) or key not in payload:
                ok = False
                details[f"missing_{key}"] = True
        items = payload.get("results", []) if isinstance(payload, dict) else []
    elif expected_type == "list":
        ok = isinstance(payload, list)
        details["type"] = "list" if isinstance(payload, list) else type(payload).__name__
        items = payload if isinstance(payload, list) else []
    else:
        items = []

    required_item_keys = contract.get("required_item_keys", [])
    if required_item_keys and items:
        first = items[0] if isinstance(items[0], dict) else {}
        for key in required_item_keys:
            if key not in first:
                ok = False
                details[f"first_item_missing_{key}"] = True
    details["ok"] = ok
    return details


def run_read_only_contract_check(target_id: str) -> dict[str, object]:
    checks = {}
    all_ok = True
    for name, endpoint in CONTRACT_ENDPOINTS.items():
        response = browser_fetch_json_response(target_id, endpoint)
        payload = response.get("payload")
        shape = shape_ok(name, payload)
        ok = int(response.get("status", 0) or 0) == 200 and bool(shape.get("ok"))
        all_ok = all_ok and ok
        checks[name] = {
            "endpoint": endpoint,
            "status": response.get("status"),
            "ok": ok,
            "shape": shape,
        }
    return {"all_ok": all_ok, "checks": checks}
