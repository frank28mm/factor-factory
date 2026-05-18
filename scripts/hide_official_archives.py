#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = ROOT / "state" / "ledger"
AUDIT = ROOT / "state" / "audit"
CACHE = LEDGER / "official-archive-hide-cache.json"

sys.path.insert(0, str(ROOT))
from connectors.worldquant_brain.live_simulation import fetch_alpha_detail, patch_alpha_hidden  # noqa: E402
from connectors.worldquant_brain.session_probe import find_worldquant_target_id  # noqa: E402


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_cache() -> dict[str, Any]:
    return {
        "schema": "factor_factory.official_archive_hide_cache.v1",
        "updated_at": "",
        "hidden_alpha_ids": {},
    }


def audit_cache_seed(audit_dir: pathlib.Path = AUDIT) -> dict[str, Any]:
    cache = empty_cache()
    audit_files = sorted(
        audit_dir.glob("official-archive-hide-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in audit_files[:5]:
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for row in payload.get("results", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            alpha_id = str(row.get("alpha_id") or "")
            if not alpha_id or not row.get("after_hidden"):
                continue
            cache["hidden_alpha_ids"][alpha_id] = {
                "after_hidden": True,
                "status": row.get("status", ""),
                "source": str(path),
                "updated_at": payload.get("created_at") or "",
            }
    return cache


def load_cache(cache_path: pathlib.Path = CACHE, audit_dir: pathlib.Path = AUDIT) -> dict[str, Any]:
    if cache_path.exists():
        try:
            payload = read_json(cache_path)
            if isinstance(payload, dict) and isinstance(payload.get("hidden_alpha_ids"), dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    return audit_cache_seed(audit_dir)


def remember_hidden(cache: dict[str, Any], alpha_id: str, *, status: str, source: str) -> None:
    hidden = cache.setdefault("hidden_alpha_ids", {})
    if not isinstance(hidden, dict):
        hidden = {}
        cache["hidden_alpha_ids"] = hidden
    hidden[alpha_id] = {
        "after_hidden": True,
        "status": status,
        "source": source,
        "updated_at": now_iso(),
    }
    cache["updated_at"] = now_iso()


def forget_hidden(cache: dict[str, Any], alpha_id: str) -> None:
    hidden = cache.setdefault("hidden_alpha_ids", {})
    if isinstance(hidden, dict) and alpha_id in hidden:
        hidden.pop(alpha_id, None)
        cache["updated_at"] = now_iso()


def is_submitted(result: dict[str, Any]) -> bool:
    if result.get("submitted") is True:
        return True
    if result.get("date_submitted") or result.get("dateSubmitted"):
        return True
    return str(result.get("alpha_status") or result.get("status") or "").upper() == "ACTIVE"


def submitted_alpha_ids(result_ledger: list[dict[str, Any]]) -> set[str]:
    return {
        str(row["alpha_id"])
        for row in result_ledger
        if row.get("alpha_id") and is_submitted(row)
    }


def active_or_pending_alpha_ids(result_ledger: list[dict[str, Any]]) -> set[str]:
    protected = set(submitted_alpha_ids(result_ledger))
    for row in result_ledger:
        alpha_id = str(row.get("alpha_id") or "")
        if not alpha_id:
            continue
        if row.get("submit_ready"):
            protected.add(alpha_id)
            continue
        pending_checks = row.get("pending_checks")
        if isinstance(pending_checks, list) and pending_checks:
            if row.get("core_metrics_passed") and not row.get("non_submittable_archive_reason"):
                protected.add(alpha_id)
    return protected


def protected_unhide_targets(result_ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = []
    for row in result_ledger:
        alpha_id = str(row.get("alpha_id") or "")
        if not alpha_id:
            continue
        if is_submitted(row) or row.get("submit_ready"):
            targets.append({"alpha_id": alpha_id, "reason": "submitted_or_submit_ready"})
            continue
        pending_checks = row.get("pending_checks")
        if isinstance(pending_checks, list) and pending_checks:
            if row.get("core_metrics_passed") and not row.get("non_submittable_archive_reason"):
                targets.append({"alpha_id": alpha_id, "reason": "core_passed_pending_checks"})
    deduped: dict[str, dict[str, Any]] = {}
    for target in targets:
        deduped.setdefault(str(target["alpha_id"]), target)
    return sorted(deduped.values(), key=lambda row: row["alpha_id"])


def add_archive_rows(
    targets: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    source: str,
    blocked_alpha_ids: set[str],
) -> None:
    for row in rows:
        alpha_id = str(row.get("alpha_id") or "")
        candidate_id = str(row.get("candidate_id") or "")
        if not alpha_id or alpha_id in blocked_alpha_ids:
            continue
        target = targets.setdefault(
            alpha_id,
            {
                "alpha_id": alpha_id,
                "candidate_ids": [],
                "archive_sources": [],
            },
        )
        if candidate_id and candidate_id not in target["candidate_ids"]:
            target["candidate_ids"].append(candidate_id)
        if source not in target["archive_sources"]:
            target["archive_sources"].append(source)


def select_archive_targets(
    ledger_dir: pathlib.Path = LEDGER,
    result_ledger: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if result_ledger is None:
        result_path = ledger_dir / "result-ledger.json"
        result_ledger = read_json(result_path) if result_path.exists() else []
    blocked = active_or_pending_alpha_ids(result_ledger)
    targets: dict[str, dict[str, Any]] = {}
    sources = [
        ("correlation", ledger_dir / "correlation-archive.json"),
        ("non_submittable", ledger_dir / "non-submittable-archive.json"),
    ]
    for source, path in sources:
        if not path.exists():
            continue
        payload = read_json(path)
        rows = payload.get("archived_pool", []) if isinstance(payload, dict) else []
        add_archive_rows(targets, rows, source, blocked)
    return sorted(targets.values(), key=lambda row: row["alpha_id"])


def response_payload(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("payload")
    return payload if isinstance(payload, dict) else {}


def unhide_protected_targets(
    target_id: str,
    targets: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results = []
    patched_count = 0
    for target in targets:
        alpha_id = str(target["alpha_id"])
        detail = fetch_alpha_detail(target_id, alpha_id)
        payload = response_payload(detail)
        status = str(payload.get("status") or "")
        before_hidden = bool(payload.get("hidden"))
        patch_status = None
        after_hidden = before_hidden
        if before_hidden and not dry_run:
            patch = patch_alpha_hidden(target_id, alpha_id, False)
            patch_status = patch.get("status")
            after_hidden = bool(response_payload(patch).get("hidden"))
            if not after_hidden:
                patched_count += 1
                if cache is not None:
                    forget_hidden(cache, alpha_id)
        elif before_hidden and dry_run:
            after_hidden = False
        elif cache is not None:
            forget_hidden(cache, alpha_id)
        results.append(
            {
                "alpha_id": alpha_id,
                "reason": target.get("reason", ""),
                "detail_status": detail.get("status"),
                "before_hidden": before_hidden,
                "patch_status": patch_status,
                "after_hidden": after_hidden,
                "status": status,
                "dry_run": dry_run,
            }
        )
    return {
        "selected_unique_alpha_count": len(targets),
        "patched_count": patched_count,
        "results": results,
    }


def hide_targets(
    target_id: str,
    targets: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results = []
    patched_count = 0
    skipped_submitted_count = 0
    cached_hidden_count = 0
    for target in targets:
        alpha_id = str(target["alpha_id"])
        cached = None
        if cache is not None and isinstance(cache.get("hidden_alpha_ids"), dict):
            cached = cache["hidden_alpha_ids"].get(alpha_id)
        if cached and cached.get("after_hidden") is True and not dry_run:
            cached_hidden_count += 1
            results.append(
                {
                    "alpha_id": alpha_id,
                    "candidate_ids": target.get("candidate_ids", []),
                    "archive_sources": target.get("archive_sources", []),
                    "detail_status": "cached",
                    "before_hidden": True,
                    "patch_status": None,
                    "after_hidden": True,
                    "archived": True,
                    "status": cached.get("status", ""),
                    "dry_run": dry_run,
                    "skipped_reason": "cached_hidden",
                }
            )
            continue
        detail = fetch_alpha_detail(target_id, alpha_id)
        payload = response_payload(detail)
        status = str(payload.get("status") or "")
        before_hidden = bool(payload.get("hidden"))
        submitted = bool(payload.get("dateSubmitted")) or status.upper() == "ACTIVE"
        patch_status = None
        after_hidden = before_hidden
        if submitted:
            skipped_submitted_count += 1
        elif not before_hidden and not dry_run:
            patch = patch_alpha_hidden(target_id, alpha_id, True)
            patch_status = patch.get("status")
            after_hidden = bool(response_payload(patch).get("hidden"))
            if after_hidden:
                patched_count += 1
                if cache is not None:
                    remember_hidden(cache, alpha_id, status=status, source="patch")
        elif not before_hidden and dry_run:
            after_hidden = True
        elif before_hidden and cache is not None:
            remember_hidden(cache, alpha_id, status=status, source="detail")
        results.append(
            {
                "alpha_id": alpha_id,
                "candidate_ids": target.get("candidate_ids", []),
                "archive_sources": target.get("archive_sources", []),
                "detail_status": detail.get("status"),
                "before_hidden": before_hidden,
                "patch_status": patch_status,
                "after_hidden": after_hidden,
                "archived": after_hidden,
                "status": status,
                "dry_run": dry_run,
            }
        )
    return {
        "selected_unique_alpha_count": len(targets),
        "patched_count": patched_count,
        "cached_hidden_count": cached_hidden_count,
        "skipped_submitted_count": skipped_submitted_count,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hide archived WorldQuant alphas from the default official Alpha list.")
    parser.add_argument("--target-id", help="CDP target id of a logged-in WorldQuant tab. Auto-detected when omitted.")
    parser.add_argument("--run-id", help="Audit run id. Defaults to current UTC timestamp.")
    parser.add_argument("--dry-run", action="store_true", help="Build the hide plan without PATCH calls.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_ledger = read_json(LEDGER / "result-ledger.json") if (LEDGER / "result-ledger.json").exists() else []
    targets = select_archive_targets(LEDGER, result_ledger)
    target_id = args.target_id or find_worldquant_target_id()
    cache = load_cache()
    protected_unhide = unhide_protected_targets(
        target_id,
        protected_unhide_targets(result_ledger),
        dry_run=bool(args.dry_run),
        cache=cache,
    )
    hide_result = hide_targets(target_id, targets, dry_run=bool(args.dry_run), cache=cache)
    if not args.dry_run:
        write_json(CACHE, cache)
    event = {
        "event_type": "official_archive_hide",
        "run_id": run_id,
        "created_at": now_iso(),
        "source": [
            str(LEDGER / "correlation-archive.json"),
            str(LEDGER / "non-submittable-archive.json"),
        ],
        "target_id": target_id,
        "policy": "PATCH /alphas/{alpha_id} hidden=true only for local archived, unsubmitted alphas. Never delete. Never patch submitted ACTIVE alphas.",
        "dry_run": bool(args.dry_run),
        "protected_unhide": protected_unhide,
        **hide_result,
    }
    write_json(AUDIT / f"official-archive-hide-{run_id}.json", event)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
