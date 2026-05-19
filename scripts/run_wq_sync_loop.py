#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LEDGER = ROOT / "state" / "ledger"
AUDIT = ROOT / "state" / "audit"

DEFAULT_REPLENISH_POOL_ID = "tp-stage3-analyst-earnings-event-reset-v0"
PROFILE_REPLENISH_POOL_ID = "profile-stage2-field-blend-v15"
DEFAULT_PROFILE_ANALYST_SELECTION_RUN_ID = "v15-local-cycle-20260517-select-analyst4"
DEFAULT_PROFILE_FUNDAMENTAL_SELECTION_RUN_ID = "v15-local-cycle-20260517-select-fundamental6"
DEFAULT_PROFILE_PV_SELECTION_RUN_ID = "v15-local-cycle-20260517-select-pv1"
DEFAULT_PROFILE_SEED_CANDIDATE_ID = "seed-profile-stage2-core-pass"


def read_json(path: pathlib.Path) -> Any:
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
        "selected_count": 0,
        "launched_count": 0,
        "stopped": {"reason": "subprocess_error"},
        "returncode": error.returncode,
        "command": " ".join(command),
        "error": command_error_text(error),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Keep the local Factor Factory synchronized with WorldQuant BRAIN: "
            "refresh running simulations, refresh pending checks, and fill open simulation slots."
        )
    )
    parser.add_argument("--max-cycles", type=int, default=1, help="Number of loop cycles. Use 0 for continuous mode.")
    parser.add_argument("--interval-seconds", type=int, default=15, help="Sleep between cycles in continuous mode.")
    parser.add_argument("--rate-limit-cooldown-seconds", type=int, default=600, help="Sleep before retrying after a 429/rate-limited response.")
    parser.add_argument(
        "--probe-rate-limit-cooldown-seconds",
        type=int,
        default=180,
        help="Cooldown only new simulation launches after probe/create rate limiting. Other sync steps keep running.",
    )
    parser.add_argument("--max-running", type=int, default=3, help="Official simulation thread cap to respect.")
    parser.add_argument("--pending-refresh-limit", type=int, default=3, help="Max running simulations to query per cycle.")
    parser.add_argument("--waiting-refresh-limit", type=int, default=2, help="Max alpha detail/check rows to refresh per cycle.")
    parser.add_argument("--submit-ready-limit", type=int, default=1, help="Max submit-ready alphas to submit per cycle.")
    parser.add_argument("--probe-batch-limit", type=int, default=3, help="Max new simulations to launch per cycle.")
    parser.add_argument("--auto-replenish", action="store_true", help="Generate local candidates when probe inventory is low.")
    parser.add_argument("--replenish-pool-id", default=DEFAULT_REPLENISH_POOL_ID, help="Task pool used for auto replenishment.")
    parser.add_argument(
        "--fallback-replenish-pool-id",
        action="append",
        default=[],
        help="Optional fallback task pool used when the primary pool is strategy-blocked. Can be repeated or comma-separated.",
    )
    parser.add_argument("--replenish-batch-prefix", default="auto", help="Prefix for generated replenishment batch ids.")
    parser.add_argument("--replenish-min-ready", type=int, default=6, help="Replenish when ready candidates in the pool fall below this count.")
    parser.add_argument("--replenish-batch-size", type=int, default=6, help="Number of local candidates to generate per replenishment.")
    parser.add_argument(
        "--profile-replenish",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When configured pools are blocked, generate profile-driven field-blend candidates as a diversified replenishment lane.",
    )
    parser.add_argument("--profile-analyst-selection-run-id", default=DEFAULT_PROFILE_ANALYST_SELECTION_RUN_ID)
    parser.add_argument("--profile-fundamental-selection-run-id", default=DEFAULT_PROFILE_FUNDAMENTAL_SELECTION_RUN_ID)
    parser.add_argument("--profile-pv-selection-run-id", default=DEFAULT_PROFILE_PV_SELECTION_RUN_ID)
    parser.add_argument("--profile-seed-candidate-id", default=DEFAULT_PROFILE_SEED_CANDIDATE_ID)
    parser.add_argument("--pool-id", help="Optional task pool filter for new simulations.")
    parser.add_argument("--batch-id", help="Optional task pool batch filter for new simulations.")
    parser.add_argument("--target-id", help="Optional explicit WorldQuant BRAIN CDP target id.")
    parser.add_argument("--run-id", help="Optional audit run id prefix.")
    parser.add_argument("--dry-run", action="store_true", help="Plan one or more cycles without live platform calls.")
    parser.add_argument("--offline-plan", action="store_true", help="Plan from local ledgers only; do not call any browser/API refresh helpers.")
    parser.add_argument("--no-sleep", action="store_true", help="Do not sleep between cycles; mostly for tests.")
    return parser.parse_args()


def maintenance() -> list[dict[str, Any]]:
    events = []
    for script in ("build_pending_runs.py", "build_ledgers.py", "build_retrospectives.py", "export_visual_ledger.py"):
        events.append({"script": script, "result": run_json([sys.executable, str(SCRIPTS / script)])})
    return events


def pending_count() -> int:
    path = LEDGER / "pending-runs.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_pending_runs.py")])
    rows = read_json(path)
    return len(rows) if isinstance(rows, list) else 0


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def submission_quota_status() -> dict[str, Any]:
    path = LEDGER / "submission-pool.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    payload = read_json(path) if path.exists() else {}
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    remaining_quota = optional_int(summary.get("remaining_submission_quota"))
    submitted_today = optional_int(summary.get("submitted_today_count"))
    ready_count = optional_int(summary.get("ready_count"))
    return {
        "quota_date": summary.get("quota_date", ""),
        "submitted_today_count": submitted_today,
        "remaining_submission_quota": remaining_quota,
        "submission_gate_locked": bool(summary.get("submission_gate_locked", False)),
        "ready_count": ready_count,
    }


def submission_quota_stop(status: dict[str, Any]) -> dict[str, str] | None:
    remaining_quota = status.get("remaining_submission_quota")
    if isinstance(remaining_quota, int) and remaining_quota <= 0:
        return {"stage": "submission_quota", "reason": "remaining_submission_quota_zero"}
    return None


def continuous_should_continue_after_stop(stopped: dict[str, Any]) -> bool:
    return stopped.get("stage") == "submission_quota" and stopped.get("reason") in {
        "remaining_submission_quota_zero",
        "submission_gate_locked",
    }


def submission_quota_reserved_stop(status: dict[str, Any], submit_event: dict[str, Any]) -> dict[str, str] | None:
    if submit_event.get("dry_run"):
        return None
    selected_count = optional_int(submit_event.get("selected_count")) or 0
    remaining_quota = status.get("remaining_submission_quota")
    if selected_count > 0 and isinstance(remaining_quota, int) and selected_count >= remaining_quota:
        return {"stage": "submission_quota", "reason": "submission_quota_reserved_by_submit_requests"}
    return None


def append_optional(command: list[str], flag: str, value: str | None) -> None:
    if value:
        command.extend([flag, value])


def refresh_pending_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "refresh_pending_runs.py"),
        "--limit",
        str(max(0, args.pending_refresh_limit)),
    ]
    append_optional(command, "--target-id", args.target_id)
    command.extend(["--run-id", f"{run_id}-pending"])
    if args.dry_run:
        command.append("--dry-run")
    return command


def sync_submitted_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "sync_submitted_alphas.py"),
        "--limit",
        "10",
        "--run-id",
        f"{run_id}-submitted",
    ]
    append_optional(command, "--target-id", args.target_id)
    if args.dry_run:
        command.append("--dry-run")
    return command


def session_watchdog_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "check_wq_session.py"),
        "--run-id",
        f"{run_id}-session",
    ]
    append_optional(command, "--target-id", args.target_id)
    if args.dry_run:
        command.append("--dry-run")
    return command


def refresh_waiting_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "refresh_waiting_checks.py"),
        "--limit",
        str(max(0, args.waiting_refresh_limit)),
    ]
    append_optional(command, "--target-id", args.target_id)
    if args.dry_run:
        command.append("--dry-run")
    return command


def submit_ready_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "submit_ready_alphas.py"),
    ]
    append_optional(command, "--target-id", args.target_id)
    command.extend(["--run-id", f"{run_id}-submit"])
    command.extend(["--limit", str(max(0, int(getattr(args, "submit_ready_limit", 1) or 0)))])
    if args.dry_run:
        command.append("--dry-run")
    return command


def ready_probe_count(pool_id: str | None = None) -> int:
    path = LEDGER / "probe-pool.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    payload = read_json(path)
    if not isinstance(payload, dict):
        return 0
    rows = payload.get("ready_pool", [])
    if not isinstance(rows, list):
        return 0
    if not pool_id:
        return len(rows)
    return sum(1 for row in rows if isinstance(row, dict) and row.get("task_pool_id") == pool_id)


def ready_probe_rows(pool_id: str | None = None, batch_id: str | None = None) -> list[dict[str, Any]]:
    path = LEDGER / "probe-pool.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    payload = read_json(path)
    if not isinstance(payload, dict):
        return []
    rows = payload.get("ready_pool", [])
    if not isinstance(rows, list):
        return []
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if pool_id and row.get("task_pool_id") != pool_id:
            continue
        if batch_id and row.get("task_pool_batch_id") != batch_id:
            continue
        selected.append(row)
    return selected


def pool_strategy(pool_id: str | None) -> dict[str, Any] | None:
    if not pool_id:
        return None
    path = LEDGER / "pool-strategy.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    if not path.exists():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    for row in payload.get("pools", []):
        if isinstance(row, dict) and row.get("pool_id") == pool_id:
            return row
    return None


def read_pool_strategy_payload() -> dict[str, Any]:
    path = LEDGER / "pool-strategy.json"
    if not path.exists():
        run_json([sys.executable, str(SCRIPTS / "build_ledgers.py")])
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def pool_strategy_from_payload(payload: dict[str, Any], pool_id: str | None) -> dict[str, Any] | None:
    if not pool_id:
        return None
    for row in payload.get("pools", []):
        if isinstance(row, dict) and row.get("pool_id") == pool_id:
            return row
    return None


def replenish_batch_id(args: argparse.Namespace, run_id: str) -> str:
    prefix = str(args.replenish_batch_prefix or "auto").strip("-") or "auto"
    return f"{prefix}-{run_id}"


def quota_batch_id(quota: dict[str, Any], run_id: str) -> str:
    prefix = str(quota.get("batch_prefix") or quota.get("role") or "quota").strip("-") or "quota"
    return f"{prefix}-{run_id}"


def replenish_limit(args: argparse.Namespace, ready_before: int) -> int:
    threshold = max(0, int(args.replenish_min_ready))
    batch_size = max(0, int(args.replenish_batch_size))
    missing = max(0, threshold - max(0, int(ready_before)))
    if missing == 0:
        return 0
    return min(batch_size, missing)


def replenish_command(args: argparse.Namespace, run_id: str, pool_id: str, limit: int) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS / "generate_task_pool.py"),
        "--pool-id",
        pool_id,
        "--run-id",
        replenish_batch_id(args, run_id),
        "--limit",
        str(max(0, int(limit))),
    ]


def quota_replenish_command(quota: dict[str, Any], run_id: str, pool_id: str, limit: int) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS / "generate_task_pool.py"),
        "--pool-id",
        pool_id,
        "--run-id",
        quota_batch_id(quota, run_id),
        "--limit",
        str(max(0, int(limit))),
    ]


def profile_replenish_batch_id(run_id: str) -> str:
    return f"auto-profile-{run_id}"


def profile_replenish_command(args: argparse.Namespace, run_id: str, limit: int) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS / "generate_profile_stage2_pool.py"),
        "--run-id",
        profile_replenish_batch_id(run_id),
        "--analyst-selection-run-id",
        str(getattr(args, "profile_analyst_selection_run_id", DEFAULT_PROFILE_ANALYST_SELECTION_RUN_ID)),
        "--fundamental-selection-run-id",
        str(getattr(args, "profile_fundamental_selection_run_id", DEFAULT_PROFILE_FUNDAMENTAL_SELECTION_RUN_ID)),
        "--pv-selection-run-id",
        str(getattr(args, "profile_pv_selection_run_id", DEFAULT_PROFILE_PV_SELECTION_RUN_ID)),
        "--seed-candidate-id",
        str(getattr(args, "profile_seed_candidate_id", DEFAULT_PROFILE_SEED_CANDIDATE_ID)),
        "--limit",
        str(max(0, int(limit))),
    ]


def replenish_pool_ids(args: argparse.Namespace) -> list[str]:
    raw_values = [getattr(args, "replenish_pool_id", None), *getattr(args, "fallback_replenish_pool_id", [])]
    pool_ids = []
    seen = set()
    for raw_value in raw_values:
        for pool_id in str(raw_value or "").split(","):
            pool_id = pool_id.strip()
            if not pool_id or pool_id in seen:
                continue
            pool_ids.append(pool_id)
            seen.add(pool_id)
    return pool_ids


def weighted_replenish_quotas(payload: dict[str, Any]) -> list[dict[str, Any]]:
    policy = payload.get("policy", {})
    if not isinstance(policy, dict):
        return []
    if str(policy.get("replenish_strategy", "")).lower() != "weighted_rotation":
        return []
    quotas = policy.get("replenish_quotas", [])
    return [quota for quota in quotas if isinstance(quota, dict) and quota.get("pool_id")]


def weighted_quota_choice(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any] | None:
    choices = []
    skipped_pools = []
    for quota in weighted_replenish_quotas(payload):
        pool_id = str(quota["pool_id"])
        strategy = pool_strategy_from_payload(payload, pool_id)
        ready_before = ready_probe_count(pool_id)
        if strategy and strategy.get("blocked_for_auto_replenish"):
            skipped_pools.append(
                {
                    "pool_id": pool_id,
                    "quota_role": quota.get("role", ""),
                    "ready_before": ready_before,
                    "reason": "pool_strategy_blocked",
                    "pool_status": strategy.get("pool_status"),
                    "recommended_action_cn": strategy.get("recommended_action_cn"),
                }
            )
            continue

        threshold = int(quota.get("min_ready") or quota.get("weight") or args.replenish_min_ready or 0)
        batch_size = int(quota.get("batch_size") or args.replenish_batch_size or threshold)
        missing = max(0, threshold - ready_before)
        planned_count = min(max(0, batch_size), missing)
        if planned_count <= 0:
            continue
        choices.append(
            {
                "quota": quota,
                "pool_id": pool_id,
                "ready_before": ready_before,
                "threshold": threshold,
                "missing": missing,
                "planned_count": planned_count,
                "deficit_ratio": missing / threshold if threshold else 0,
                "weight": int(quota.get("weight") or threshold or 0),
                "skipped_pools": skipped_pools,
            }
        )

    if choices:
        return sorted(
            choices,
            key=lambda row: (row["deficit_ratio"], row["missing"], row["weight"], row["pool_id"]),
            reverse=True,
        )[0]

    quotas = weighted_replenish_quotas(payload)
    if not quotas:
        return None
    return {
        "pool_id": "",
        "ready_before": 0,
        "threshold": 0,
        "planned_count": 0,
        "missing": 0,
        "quota": {},
        "skipped_pools": skipped_pools,
        "reason": "weighted_quota_inventory_above_threshold"
        if len(skipped_pools) < len(quotas)
        else "no_unblocked_replenish_pool",
    }


def maybe_replenish(args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    if not getattr(args, "auto_replenish", False):
        return {"enabled": False, "generated_count": 0, "ready_before": ready_probe_count(args.pool_id)}

    strategy_payload = read_pool_strategy_payload()
    operating_mode = strategy_payload.get("policy", {}) if isinstance(strategy_payload.get("policy"), dict) else {}
    if str(operating_mode.get("replenish_strategy", "")).lower() == "profile_stage3_only":
        fallback = maybe_profile_replenish(args, run_id, {"reason": "profile_stage3_only"})
        if fallback:
            return fallback

    quota_choice = weighted_quota_choice(args, strategy_payload)
    if quota_choice is not None:
        pool_id = str(quota_choice.get("pool_id") or "")
        quota = quota_choice.get("quota") or {}
        planned_count = int(quota_choice.get("planned_count") or 0)
        if not pool_id or planned_count <= 0:
            if getattr(args, "profile_replenish", True):
                fallback = maybe_profile_replenish(args, run_id, quota_choice)
                if fallback:
                    return fallback
            return {
                "enabled": True,
                "quota_strategy": "weighted_rotation",
                "pool_id": pool_id,
                "ready_before": quota_choice.get("ready_before", 0),
                "threshold": quota_choice.get("threshold", 0),
                "generated_count": 0,
                "reason": quota_choice.get("reason", "weighted_quota_inventory_above_threshold"),
                "skipped_pools": quota_choice.get("skipped_pools", []),
            }

        if args.dry_run:
            return {
                "enabled": True,
                "quota_strategy": "weighted_rotation",
                "pool_id": pool_id,
                "quota_role": quota.get("role", ""),
                "ready_before": quota_choice["ready_before"],
                "threshold": quota_choice["threshold"],
                "planned_count": planned_count,
                "generated_count": 0,
                "planned_batch_id": quota_batch_id(quota, run_id),
                "dry_run": True,
                "skipped_pools": quota_choice.get("skipped_pools", []),
            }

        try:
            event = run_json(quota_replenish_command(quota, run_id, pool_id, planned_count))
        except subprocess.CalledProcessError as error:
            return {
                "enabled": True,
                "quota_strategy": "weighted_rotation",
                "pool_id": pool_id,
                "quota_role": quota.get("role", ""),
                "ready_before": quota_choice["ready_before"],
                "threshold": quota_choice["threshold"],
                "planned_count": planned_count,
                "generated_count": 0,
                "reason": "replenish_command_failed",
                "error": command_error_text(error),
                "skipped_pools": quota_choice.get("skipped_pools", []),
            }
        maintenance()
        ready_after = ready_probe_count(pool_id)
        return {
            "enabled": True,
            "quota_strategy": "weighted_rotation",
            "pool_id": pool_id,
            "quota_role": quota.get("role", ""),
            "ready_before": quota_choice["ready_before"],
            "ready_after": ready_after,
            "threshold": quota_choice["threshold"],
            "planned_count": planned_count,
            "batch_id": quota_batch_id(quota, run_id),
            "generated_count": int(event.get("generated_count", 0) or 0),
            "skipped_count": int(event.get("skipped_count", 0) or 0),
            "auto_submit": bool(event.get("auto_submit", False)),
            "skipped_pools": quota_choice.get("skipped_pools", []),
        }

    threshold = max(0, int(args.replenish_min_ready))
    skipped_pools = []
    for pool_id in replenish_pool_ids(args):
        strategy = pool_strategy(pool_id)
        ready_before = ready_probe_count(pool_id)
        if strategy and strategy.get("blocked_for_auto_replenish"):
            skipped_pools.append(
                {
                    "pool_id": pool_id,
                    "ready_before": ready_before,
                    "reason": "pool_strategy_blocked",
                    "pool_status": strategy.get("pool_status"),
                    "recommended_action_cn": strategy.get("recommended_action_cn"),
                }
            )
            continue

        if ready_before >= threshold:
            return {
                "enabled": True,
                "pool_id": pool_id,
                "ready_before": ready_before,
                "threshold": threshold,
                "generated_count": 0,
                "reason": "ready_inventory_above_threshold",
                "skipped_pools": skipped_pools,
            }

        planned_count = replenish_limit(args, ready_before)

        if args.dry_run:
            return {
                "enabled": True,
                "pool_id": pool_id,
                "ready_before": ready_before,
                "threshold": threshold,
                "planned_count": planned_count,
                "generated_count": 0,
                "planned_batch_id": replenish_batch_id(args, run_id),
                "dry_run": True,
                "skipped_pools": skipped_pools,
            }

        try:
            event = run_json(replenish_command(args, run_id, pool_id, planned_count))
        except subprocess.CalledProcessError as error:
            skipped_pools.append(
                {
                    "pool_id": pool_id,
                    "ready_before": ready_before,
                    "reason": "replenish_command_failed",
                    "error": command_error_text(error),
                }
            )
            continue
        maintenance()
        ready_after = ready_probe_count(pool_id)
        return {
            "enabled": True,
            "pool_id": pool_id,
            "ready_before": ready_before,
            "ready_after": ready_after,
            "threshold": threshold,
            "planned_count": planned_count,
            "batch_id": replenish_batch_id(args, run_id),
            "generated_count": int(event.get("generated_count", 0) or 0),
            "skipped_count": int(event.get("skipped_count", 0) or 0),
            "auto_submit": bool(event.get("auto_submit", False)),
            "skipped_pools": skipped_pools,
        }

    if len(skipped_pools) == 1:
        return {
            "enabled": True,
            "pool_id": skipped_pools[0]["pool_id"],
            "ready_before": skipped_pools[0]["ready_before"],
            "threshold": threshold,
            "generated_count": 0,
            "reason": skipped_pools[0]["reason"],
            "pool_status": skipped_pools[0].get("pool_status"),
            "recommended_action_cn": skipped_pools[0].get("recommended_action_cn"),
            "skipped_pools": skipped_pools,
        }

    return {
        "enabled": True,
        "pool_id": "",
        "ready_before": 0,
        "threshold": threshold,
        "generated_count": 0,
        "reason": "no_unblocked_replenish_pool",
        "skipped_pools": skipped_pools,
    }


def maybe_profile_replenish(
    args: argparse.Namespace,
    run_id: str,
    blocked_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not getattr(args, "profile_replenish", True):
        return None
    strategy_payload = read_pool_strategy_payload()
    strategy = pool_strategy_from_payload(strategy_payload, PROFILE_REPLENISH_POOL_ID)
    ready_before = ready_probe_count(PROFILE_REPLENISH_POOL_ID)
    threshold = max(0, int(getattr(args, "replenish_min_ready", 0) or 0))
    if strategy and strategy.get("blocked_for_auto_replenish"):
        return {
            "enabled": True,
            "quota_strategy": "profile_fallback",
            "pool_id": PROFILE_REPLENISH_POOL_ID,
            "probe_pool_id": "",
            "ready_before": ready_before,
            "threshold": threshold,
            "generated_count": 0,
            "reason": "profile_pool_strategy_blocked",
            "pool_status": strategy.get("pool_status"),
            "recommended_action_cn": strategy.get("recommended_action_cn"),
            "blocked_quota_context": blocked_context or {},
        }
    if ready_before >= threshold:
        return {
            "enabled": True,
            "quota_strategy": "profile_fallback",
            "pool_id": PROFILE_REPLENISH_POOL_ID,
            "ready_before": ready_before,
            "threshold": threshold,
            "generated_count": 0,
            "reason": "profile_ready_inventory_above_threshold",
            "blocked_quota_context": blocked_context or {},
        }
    planned_count = replenish_limit(args, ready_before)
    if planned_count <= 0:
        return {
            "enabled": True,
            "quota_strategy": "profile_fallback",
            "pool_id": PROFILE_REPLENISH_POOL_ID,
            "ready_before": ready_before,
            "threshold": threshold,
            "generated_count": 0,
            "reason": "profile_replenish_no_missing_inventory",
            "blocked_quota_context": blocked_context or {},
        }
    if args.dry_run:
        return {
            "enabled": True,
            "quota_strategy": "profile_fallback",
            "pool_id": PROFILE_REPLENISH_POOL_ID,
            "ready_before": ready_before,
            "threshold": threshold,
            "planned_count": planned_count,
            "generated_count": 0,
            "planned_batch_id": profile_replenish_batch_id(run_id),
            "dry_run": True,
            "reason": "profile_replenish_planned",
            "blocked_quota_context": blocked_context or {},
        }
    try:
        event = run_json(profile_replenish_command(args, run_id, planned_count))
    except subprocess.CalledProcessError as error:
        return {
            "enabled": True,
            "quota_strategy": "profile_fallback",
            "pool_id": PROFILE_REPLENISH_POOL_ID,
            "ready_before": ready_before,
            "threshold": threshold,
            "planned_count": planned_count,
            "generated_count": 0,
            "reason": "profile_replenish_command_failed",
            "error": command_error_text(error),
            "blocked_quota_context": blocked_context or {},
        }
    maintenance()
    ready_after = ready_probe_count(PROFILE_REPLENISH_POOL_ID)
    return {
        "enabled": True,
        "quota_strategy": "profile_fallback",
        "pool_id": PROFILE_REPLENISH_POOL_ID,
        "ready_before": ready_before,
        "ready_after": ready_after,
        "threshold": threshold,
        "planned_count": planned_count,
        "batch_id": profile_replenish_batch_id(run_id),
        "generated_count": int(event.get("generated_count", 0) or 0),
        "skipped_count": int(event.get("skipped_count", 0) or 0),
        "auto_submit": bool(event.get("auto_submit", False)),
        "reason": "profile_replenish_generated",
        "blocked_quota_context": blocked_context or {},
    }


def probe_batch_command(
    args: argparse.Namespace,
    limit: int,
    run_id: str,
    pool_id_override: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "run_probe_pool_batch.py"),
        "--limit",
        str(max(0, limit)),
    ]
    append_optional(command, "--pool-id", pool_id_override or args.pool_id)
    append_optional(command, "--batch-id", args.batch_id)
    append_optional(command, "--target-id", args.target_id)
    command.extend(["--run-id", f"{run_id}-probe"])
    if args.dry_run:
        command.append("--dry-run")
    return command


def hide_archives_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "hide_official_archives.py"),
        "--run-id",
        f"{run_id}-archive-hide",
    ]
    append_optional(command, "--target-id", args.target_id)
    if args.dry_run:
        command.append("--dry-run")
    return command


def stop_from(payload: dict[str, Any], stage: str) -> dict[str, str] | None:
    stopped = payload.get("stopped")
    if isinstance(stopped, dict) and stopped.get("reason"):
        return {"stage": stage, "reason": str(stopped["reason"])}
    return None


def classify_session_watchdog_response(response: dict[str, Any]) -> dict[str, Any]:
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


def apply_session_watchdog_to_cycle(cycle: dict[str, Any], session_event: dict[str, Any]) -> dict[str, Any]:
    classification = str(session_event.get("classification") or "unknown")
    reason = f"session_{classification}"
    cycle["session_watchdog"] = session_event
    cycle["session_state"] = classification
    cycle["sync_submitted"] = {"skipped": True, "reason": reason}
    cycle["pending_before"] = pending_count()
    cycle["pending_refresh"] = {"skipped": True, "selected_count": 0, "reason": reason}
    cycle["pending_after_refresh"] = cycle["pending_before"]
    cycle["waiting_refresh"] = {"skipped": True, "selected_count": 0, "reason": reason}
    cycle["submit_ready"] = {"skipped": True, "selected_count": 0, "reason": reason}
    cycle["submission_quota_after_submit"] = submission_quota_status()
    cycle["open_slots"] = 0
    cycle["probe_launch_limit"] = 0
    cycle["replenish"] = {
        "enabled": False,
        "generated_count": 0,
        "reason": reason,
    }
    cycle["probe_pool_id"] = ""
    cycle["probe_batch"] = {
        "skipped": True,
        "selected_count": 0,
        "launched_count": 0,
        "reason": reason,
    }
    cycle["archive_hide"] = {
        "skipped": True,
        "selected_unique_alpha_count": 0,
        "patched_count": 0,
        "reason": reason,
    }
    return cycle


def parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def probe_cooldown_active(probe_cooldown: dict[str, Any] | None, now: datetime | None = None) -> bool:
    if not isinstance(probe_cooldown, dict):
        return False
    cooldown_until = parse_iso_datetime(probe_cooldown.get("cooldown_until"))
    if cooldown_until is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now < cooldown_until


def apply_stage_cooldown(
    payload: dict[str, Any],
    *,
    cycle_index: int,
    stopped: dict[str, Any],
    cooldown_seconds: int,
    created_at: datetime | None = None,
) -> bool:
    if stopped.get("stage") != "probe_batch" or stopped.get("reason") not in {"rate_limited", "subprocess_error"}:
        return False
    created_at = created_at or datetime.now(timezone.utc)
    cooldown_seconds = max(0, int(cooldown_seconds))
    payload["probe_cooldown"] = {
        "cycle": cycle_index,
        "stage": stopped.get("stage"),
        "reason": stopped.get("reason"),
        "cooldown_seconds": cooldown_seconds,
        "created_at": created_at.isoformat(),
        "cooldown_until": (created_at + timedelta(seconds=cooldown_seconds)).isoformat(),
    }
    return True


def finalize_cycle(args: argparse.Namespace, run_id: str, cycle: dict[str, Any]) -> dict[str, Any]:
    cycle["maintenance_after"] = maintenance()
    cycle["archive_hide"] = run_json(hide_archives_command(args, run_id))
    cycle["maintenance_after_archive_hide"] = maintenance()
    cycle["pending_after_cycle"] = pending_count()
    cycle["finished_at"] = datetime.now(timezone.utc).isoformat()
    return cycle


def run_cycle(args: argparse.Namespace, run_id: str, probe_cooldown: dict[str, Any] | None = None) -> dict[str, Any]:
    if getattr(args, "offline_plan", False):
        return run_offline_plan_cycle(args, run_id)

    cycle: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "auto_probe": True,
        "auto_submit": True,
        "max_running": args.max_running,
        "submit_ready_limit": max(0, int(getattr(args, "submit_ready_limit", 1) or 0)),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    cycle["maintenance_before"] = maintenance()
    session_event = run_json(session_watchdog_command(args, run_id))
    cycle["session_watchdog"] = session_event
    cycle["session_state"] = session_event.get("classification", "unknown")
    if not session_event.get("authenticated"):
        apply_session_watchdog_to_cycle(cycle, session_event)
        cycle["maintenance_after_archive_hide"] = maintenance()
        cycle["pending_after_cycle"] = pending_count()
        cycle["finished_at"] = datetime.now(timezone.utc).isoformat()
        return cycle

    sync_submitted_event = run_json(sync_submitted_command(args, run_id))
    cycle["sync_submitted"] = sync_submitted_event
    pending_before = pending_count()
    cycle["pending_before"] = pending_before

    pending_event = run_json(refresh_pending_command(args, run_id))
    cycle["pending_refresh"] = pending_event
    stopped = stop_from(pending_event, "pending_refresh")
    if stopped:
        cycle["stopped"] = stopped
        return finalize_cycle(args, run_id, cycle)

    pending_after_refresh = pending_count()
    cycle["pending_after_refresh"] = pending_after_refresh

    waiting_event = run_json(refresh_waiting_command(args))
    cycle["waiting_refresh"] = waiting_event
    stopped = stop_from(waiting_event, "waiting_refresh")
    if stopped:
        cycle["stopped"] = stopped
        return finalize_cycle(args, run_id, cycle)

    submit_event = run_json(submit_ready_command(args, run_id))
    cycle["submit_ready"] = submit_event
    stopped = stop_from(submit_event, "submit_ready")
    if stopped:
        cycle["stopped"] = stopped
        return finalize_cycle(args, run_id, cycle)

    selected_for_submit = optional_int(submit_event.get("selected_count")) or 0
    if selected_for_submit > 0:
        sync_after_submit_event = run_json(sync_submitted_command(args, f"{run_id}-post-submit"))
        cycle["sync_submitted_after_submit"] = sync_after_submit_event

    cycle["submission_quota_after_submit"] = submission_quota_status()
    stopped = submission_quota_stop(cycle["submission_quota_after_submit"]) or submission_quota_reserved_stop(
        cycle["submission_quota_after_submit"],
        submit_event,
    )
    if stopped:
        cycle["stopped"] = stopped
        return finalize_cycle(args, run_id, cycle)

    open_slots = max(0, int(args.max_running) - int(pending_after_refresh))
    probe_cooldown_is_active = probe_cooldown_active(probe_cooldown)
    cycle["probe_cooldown"] = probe_cooldown if probe_cooldown_is_active else None
    cycle["replenish"] = maybe_replenish(args, run_id)
    launch_limit = 0 if probe_cooldown_is_active else min(max(0, int(args.probe_batch_limit)), open_slots)
    cycle["open_slots"] = open_slots
    cycle["probe_launch_limit"] = launch_limit

    replenish_probe_pool_id = cycle["replenish"].get("probe_pool_id") if isinstance(cycle.get("replenish"), dict) else None
    replenish_pool_id = cycle["replenish"].get("pool_id") if isinstance(cycle.get("replenish"), dict) else None
    probe_pool_id = str(replenish_probe_pool_id if replenish_probe_pool_id is not None else replenish_pool_id) if not args.pool_id else None
    cycle["probe_pool_id"] = probe_pool_id or args.pool_id or ""

    if launch_limit > 0:
        command = probe_batch_command(args, launch_limit, run_id, probe_pool_id)
        try:
            probe_event = run_json(command)
        except subprocess.CalledProcessError as error:
            probe_event = subprocess_error_event(command, error)
    else:
        probe_event = {
            "selected_count": 0,
            "launched_count": 0,
            "reason": "probe_rate_limit_cooldown_active" if probe_cooldown_is_active else "no_open_simulation_slots",
            "auto_submit": False,
        }
    cycle["probe_batch"] = probe_event

    stopped = stop_from(probe_event, "probe_batch")
    if stopped:
        cycle["stopped"] = stopped

    return finalize_cycle(args, run_id, cycle)


def run_offline_plan_cycle(args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    cycle: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": True,
        "offline_plan": True,
        "auto_probe": True,
        "auto_submit": False,
        "max_running": args.max_running,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    cycle["maintenance_before"] = maintenance()
    cycle["pending_before"] = pending_count()
    cycle["pending_refresh"] = {
        "dry_run": True,
        "offline_plan": True,
        "selected_count": min(max(0, int(args.pending_refresh_limit)), cycle["pending_before"]),
        "reason": "offline_plan_skips_live_pending_refresh",
    }
    cycle["pending_after_refresh"] = cycle["pending_before"]
    cycle["waiting_refresh"] = {
        "dry_run": True,
        "offline_plan": True,
        "limit": max(0, int(args.waiting_refresh_limit)),
        "reason": "offline_plan_skips_live_alpha_check_refresh",
    }
    cycle["submit_ready"] = {
        "dry_run": True,
        "offline_plan": True,
        "selected_count": 0,
        "selected_candidates": [],
        "reason": "offline_plan_skips_submit_ready_alphas",
    }
    cycle["submission_quota_after_submit"] = submission_quota_status()
    ready_rows = ready_probe_rows(args.pool_id, args.batch_id)
    open_slots = max(0, int(args.max_running) - int(cycle["pending_after_refresh"]))
    launch_limit = min(max(0, int(args.probe_batch_limit)), open_slots, len(ready_rows))
    cycle["replenish"] = {
        "enabled": bool(getattr(args, "auto_replenish", False)),
        "dry_run": True,
        "offline_plan": True,
        "generated_count": 0,
        "ready_before": len(ready_rows),
        "reason": "offline_plan_skips_generation",
    }
    cycle["open_slots"] = open_slots
    cycle["probe_launch_limit"] = launch_limit
    cycle["probe_pool_id"] = args.pool_id or ""
    cycle["probe_batch"] = {
        "run_id": f"{run_id}-probe",
        "dry_run": True,
        "offline_plan": True,
        "auto_probe": True,
        "auto_submit": False,
        "pool_id": args.pool_id or "",
        "batch_id": args.batch_id or "",
        "limit": launch_limit,
        "selected_count": launch_limit,
        "selected_candidates": ready_rows[:launch_limit],
        "reason": "offline_plan_selected_from_local_probe_pool",
    }
    cycle["archive_hide"] = {
        "dry_run": True,
        "offline_plan": True,
        "selected_unique_alpha_count": 0,
        "patched_count": 0,
        "reason": "offline_plan_skips_official_archive_hide",
    }
    cycle["maintenance_after_archive_hide"] = maintenance()
    cycle["pending_after_cycle"] = pending_count()
    cycle["finished_at"] = datetime.now(timezone.utc).isoformat()
    return cycle


def run_loop(args: argparse.Namespace) -> dict[str, Any]:
    base_run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    continuous = args.max_cycles == 0
    max_cycles = float("inf") if continuous else max(0, int(args.max_cycles))
    payload: dict[str, Any] = {
        "run_id": base_run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "offline_plan": bool(getattr(args, "offline_plan", False)),
        "continuous": continuous,
        "auto_probe": True,
        "auto_submit": True,
        "cycles": [],
    }

    cycle_index = 0
    while cycle_index < max_cycles:
        cycle_index += 1
        cycle_run_id = f"{base_run_id}-c{cycle_index:03d}"
        cycle = run_cycle(args, cycle_run_id, payload.get("probe_cooldown"))
        payload["cycles"].append(cycle)
        payload["cycles_completed"] = cycle_index
        write_json(AUDIT / f"wq-sync-loop-{base_run_id}.json", payload)

        stopped = cycle.get("stopped")
        if isinstance(stopped, dict):
            if continuous and continuous_should_continue_after_stop(stopped):
                payload["last_submission_quota_stop"] = {
                    "cycle": cycle_index,
                    "stage": stopped.get("stage"),
                    "reason": stopped.get("reason"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                write_json(AUDIT / f"wq-sync-loop-{base_run_id}.json", payload)
                if not args.no_sleep:
                    time.sleep(max(0, int(args.interval_seconds)))
                continue
            if continuous and apply_stage_cooldown(
                payload,
                cycle_index=cycle_index,
                stopped=stopped,
                cooldown_seconds=int(getattr(args, "probe_rate_limit_cooldown_seconds", 180) or 0),
            ):
                write_json(AUDIT / f"wq-sync-loop-{base_run_id}.json", payload)
                if not args.no_sleep:
                    time.sleep(max(0, int(args.interval_seconds)))
                continue
            if continuous and stopped.get("reason") == "rate_limited" and not args.dry_run:
                cooldown_seconds = max(
                    int(getattr(args, "interval_seconds", 0) or 0),
                    int(getattr(args, "rate_limit_cooldown_seconds", 0) or 0),
                )
                payload["last_rate_limit"] = {
                    "cycle": cycle_index,
                    "stage": stopped.get("stage"),
                    "cooldown_seconds": cooldown_seconds,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                write_json(AUDIT / f"wq-sync-loop-{base_run_id}.json", payload)
                if not args.no_sleep:
                    time.sleep(cooldown_seconds)
                continue
            payload["stopped"] = stopped
            break

        if args.dry_run or not continuous and cycle_index >= max_cycles:
            break

        if not args.no_sleep:
            time.sleep(max(0, int(args.interval_seconds)))

    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(AUDIT / f"wq-sync-loop-{base_run_id}.json", payload)
    return payload


def main() -> int:
    print(json.dumps(run_loop(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
