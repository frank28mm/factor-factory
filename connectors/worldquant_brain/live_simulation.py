from __future__ import annotations

from typing import Any

from .session_client import browser_fetch_json_response


def extract_simulation_id(response: dict[str, Any]) -> str | None:
    payload = response.get("payload")
    if isinstance(payload, dict):
        for key in ("id", "simulationId", "simulation_id"):
            value = payload.get(key)
            if value:
                return str(value)
        if isinstance(payload.get("results"), dict):
            for key in ("id", "simulationId", "simulation_id"):
                value = payload["results"].get(key)
                if value:
                    return str(value)

    headers = response.get("headers")
    if isinstance(headers, dict):
        location = headers.get("location")
        if isinstance(location, str) and "/simulations/" in location:
            return location.rsplit("/simulations/", 1)[-1].strip("/") or None
    return None


def extract_alpha_id(response: dict[str, Any]) -> str | None:
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return None

    for key in ("alpha", "alphaId", "alpha_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for nested_key in ("id", "alphaId", "alpha_id"):
                nested_value = value.get(nested_key)
                if nested_value:
                    return str(nested_value)
    return None


def classify_create_response(response: dict[str, Any]) -> str:
    status = int(response.get("status", 0) or 0)
    if status in {200, 201, 202}:
        return "submitted" if extract_simulation_id(response) else "accepted_without_id"
    if status in {401, 403}:
        return "auth_required"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    return "failed"


def classify_result_response(response: dict[str, Any]) -> str:
    status = int(response.get("status", 0) or 0)
    if status == 200:
        payload = response.get("payload")
        if isinstance(payload, dict):
            if any(key in payload for key in ("sharpe", "fitness", "turnover", "is", "metrics", "results", "result")):
                return "fetched"
            progress = payload.get("progress")
            if isinstance(progress, (int, float)) and progress < 1:
                return "running"
        return "fetched"
    if status in {401, 403}:
        return "auth_required"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    return "failed"


def classify_alpha_detail_response(response: dict[str, Any]) -> str:
    status = int(response.get("status", 0) or 0)
    if status == 200:
        payload = response.get("payload")
        if isinstance(payload, dict) and extract_alpha_id(response):
            return "fetched"
        return "fetched_without_alpha_id"
    if status in {401, 403}:
        return "auth_required"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    return "failed"


def classify_alpha_check_response(response: dict[str, Any]) -> str:
    status = int(response.get("status", 0) or 0)
    if status == 200:
        payload = response.get("payload")
        if isinstance(payload, dict):
            checks = payload.get("checks")
            is_metrics = payload.get("is")
            if isinstance(checks, list) or isinstance(is_metrics, dict) and isinstance(is_metrics.get("checks"), list):
                return "fetched"
        text = str(response.get("text") or "")
        retry_after = None
        headers = response.get("headers")
        if isinstance(headers, dict):
            retry_after = headers.get("retryAfter")
        if not text.strip() and retry_after:
            return "pending"
        return "fetched_without_checks"
    if status in {401, 403}:
        return "auth_required"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    return "failed"


def classify_alpha_submit_response(response: dict[str, Any]) -> str:
    status = int(response.get("status", 0) or 0)
    payload = response.get("payload")
    if status in {200, 201}:
        if isinstance(payload, dict):
            alpha_status = str(payload.get("status") or "").upper()
            if payload.get("dateSubmitted") or alpha_status in {"ACTIVE", "SUBMITTED"}:
                return "submitted"
        return "accepted"
    if status == 202:
        return "accepted"
    if status in {401, 403}:
        return "auth_required"
    if status == 404:
        return "not_found"
    if status == 409:
        return "already_submitted"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    return "failed"


def create_simulation(target_id: str, api_payload: dict[str, object]) -> dict[str, object]:
    return browser_fetch_json_response(
        target_id,
        "/simulations",
        method="POST",
        json_body=api_payload,
    )


def fetch_simulation_result(target_id: str, simulation_id: str) -> dict[str, object]:
    return browser_fetch_json_response(target_id, f"/simulations/{simulation_id}")


def fetch_alpha_detail(target_id: str, alpha_id: str) -> dict[str, object]:
    return browser_fetch_json_response(target_id, f"/alphas/{alpha_id}")


def fetch_alpha_check(target_id: str, alpha_id: str) -> dict[str, object]:
    return browser_fetch_json_response(target_id, f"/alphas/{alpha_id}/check")


def submit_alpha(target_id: str, alpha_id: str) -> dict[str, object]:
    return browser_fetch_json_response(
        target_id,
        f"/alphas/{alpha_id}/submit",
        method="POST",
        json_body={},
    )


def patch_alpha_hidden(target_id: str, alpha_id: str, hidden: bool = True) -> dict[str, object]:
    return browser_fetch_json_response(
        target_id,
        f"/alphas/{alpha_id}",
        method="PATCH",
        json_body={"hidden": hidden},
    )
