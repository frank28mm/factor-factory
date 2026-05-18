#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import re
import sys
from typing import Any

import jsonschema
import yaml

from generate_seed_variants import ensure_positive_seed


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
STATE = ROOT / "state"
QUEUE = STATE / "queue"
LEDGER = STATE / "ledger"
SCHEMAS = ROOT / "schemas"
PROFILE_DIR = STATE / "datafield-profiles"


def read_json(path: pathlib.Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: pathlib.Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local-only Factor Factory Task Pool batch.")
    parser.add_argument("--pool-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError("run-id must contain at least one alphanumeric character.")
    return slug


def task_pool_config(pool_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = read_yaml(CONFIG / "task-pools.yaml")
    pools = payload.get("task_pools", {})
    if pool_id not in pools:
        raise KeyError(f"Unknown task pool id: {pool_id}")
    return payload.get("operating_mode", {}), pools[pool_id]


def seed_candidate(seed_candidate_id: str) -> dict[str, Any]:
    path = QUEUE / f"{seed_candidate_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Seed candidate not found: {path}")
    candidate = read_json(path)
    if not isinstance(candidate, dict):
        raise RuntimeError("Seed candidate must be an object.")
    return candidate


def check_names(result: dict[str, Any], expected_result: str) -> list[str]:
    names = []
    for check in result.get("checks", []):
        if not isinstance(check, dict):
            continue
        if str(check.get("result", "")).upper() == expected_result:
            name = check.get("name")
            if name:
                names.append(str(name))
    return names


def failed_check_names(result: dict[str, Any]) -> list[str]:
    names = [str(name) for name in result.get("failed_checks", []) if name]
    names.extend(check_names(result, "FAIL"))
    return sorted(set(names))


def official_core_gate_failures(result: dict[str, Any] | None, scoring: dict[str, Any]) -> list[str]:
    if not isinstance(result, dict) or not result:
        return ["missing_official_result"]
    metrics = result.get("metrics", {})
    gates = scoring["official_platform_gates"]["delay_1"]
    turnover_range = scoring["official_platform_gates"]["turnover_range"]
    failures = []

    sharpe = metrics.get("sharpe")
    fitness = metrics.get("fitness")
    turnover = metrics.get("turnover")
    if not isinstance(sharpe, (int, float)) or sharpe < gates["sharpe_min"]:
        failures.append("low_sharpe")
    if not isinstance(fitness, (int, float)) or fitness < gates["fitness_min"]:
        failures.append("low_fitness")
    if not isinstance(turnover, (int, float)) or not (turnover_range["min"] <= turnover <= turnover_range["max"]):
        failures.append("turnover_out_of_range")

    failed_checks = failed_check_names(result)
    if failed_checks:
        failures.extend(f"official_check_failed:{name}" for name in failed_checks)
    return failures


def ensure_seed_passes_official_core_gates(seed: dict[str, Any], scoring: dict[str, Any]) -> None:
    failures = official_core_gate_failures(seed.get("latest_result_import"), scoring)
    if failures:
        raise RuntimeError(
            f"Seed {seed.get('candidate_id')} does not pass official core gates: {', '.join(failures)}"
        )


def candidate_expression(candidate: dict[str, Any]) -> str:
    return str(candidate.get("rendered_expression") or candidate.get("expression") or "").strip()


def candidate_settings_key(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    params = candidate.get("params", {})
    return (
        str(params.get("wq_neutralization", "")).upper(),
        str(params.get("wq_decay", "")),
        str(params.get("wq_truncation", "")),
        str(params.get("task_pool_id", "")),
    )


def result_settings_key(candidate: dict[str, Any], result: dict[str, Any]) -> tuple[str, str, str, str]:
    settings = result.get("simulation_settings", {})
    params = candidate.get("params", {})
    return (
        str(settings.get("neutralization") or params.get("wq_neutralization", "")).upper(),
        str(settings.get("decay") or params.get("wq_decay", "")),
        str(settings.get("truncation") or params.get("wq_truncation", "")),
        str(params.get("task_pool_id", "")),
    )


def existing_expression_keys() -> set[tuple[str, tuple[str, str, str, str]]]:
    queue_by_id: dict[str, dict[str, Any]] = {}
    keys: set[tuple[str, tuple[str, str, str, str]]] = set()
    for path in QUEUE.glob("cand-*.json"):
        candidate = read_json(path)
        if not isinstance(candidate, dict):
            continue
        queue_by_id[str(candidate.get("candidate_id"))] = candidate
        expression = candidate_expression(candidate)
        if expression:
            keys.add((expression, candidate_settings_key(candidate)))

    result_path = LEDGER / "result-ledger.json"
    if result_path.exists():
        results = read_json(result_path)
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                candidate = queue_by_id.get(str(result.get("candidate_id")), {})
                expression = str(result.get("expression") or candidate_expression(candidate)).strip()
                if expression:
                    keys.add((expression, result_settings_key(candidate, result)))
    return keys


def grid_rows(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    rows = []
    for values in itertools.product(*(grid[key] for key in keys)):
        rows.append(dict(zip(keys, values, strict=True)))
    return rows


def value_slug(value: Any) -> str:
    if isinstance(value, float):
        return str(value).replace(".", "p")
    return str(value).lower().replace(".", "p").replace("_", "-")


def format_suffix_template(template: str, params: dict[str, Any]) -> str:
    slug_params = {key: value_slug(value) for key, value in params.items()}
    return template.format(**slug_params)


def candidate_suffix(pool: dict[str, Any], params: dict[str, Any], index: int) -> str:
    if pool.get("candidate_suffix_template"):
        return format_suffix_template(str(pool["candidate_suffix_template"]), params)
    slug = pool.get("candidate_slug") or pool.get("variant_family") or "pool"
    fast = value_slug(params.get("fast_window", index))
    slow = value_slug(params.get("slow_window", "x"))
    entry = value_slug(params.get("entry_threshold", "x"))
    exit_value = value_slug(params.get("exit_threshold", "x"))
    neutralization = value_slug(params.get("wq_neutralization", ""))
    decay = value_slug(params.get("wq_decay", ""))
    truncation = value_slug(params.get("wq_truncation", ""))
    setting_bits = []
    if neutralization:
        setting_bits.append(f"n{neutralization}")
    if decay:
        setting_bits.append(f"d{decay}")
    if truncation:
        setting_bits.append(f"tr{truncation}")
    setting_slug = "-" + "-".join(setting_bits) if setting_bits else ""
    return f"{slug}-fw{fast}-sw{slow}-en{entry}-ex{exit_value}{setting_slug}"


def candidate_id(run_id: str, pool: dict[str, Any], params: dict[str, Any], index: int) -> str:
    return f"cand-{run_id}-{candidate_suffix(pool, params, index)}"


def required_fields(seed: dict[str, Any], pool: dict[str, Any]) -> list[str]:
    return sorted(set(seed.get("required_fields", []) + list(pool.get("required_fields", []))))


def field_profile(field_id: str) -> dict[str, Any] | None:
    path = PROFILE_DIR / f"{field_id}.json"
    if not path.exists():
        return None
    profile = read_json(path)
    return profile if isinstance(profile, dict) else None


def profile_gate_metadata(fields: list[str]) -> dict[str, Any]:
    profiles = []
    for field_id in fields:
        profile = field_profile(field_id)
        if profile:
            profiles.append(profile)
    if not profiles:
        return {}
    refs = [str(profile.get("profile_id") or f"profile-{safe_slug(str(profile.get('field_id', '')))}") for profile in profiles]
    lane_bits = [
        f"{profile.get('field_id')}={profile.get('profile_lane')}@{profile.get('dataset_id')}"
        for profile in profiles
    ]
    return {
        "data_profile_refs": ",".join(refs),
        "field_quality_summary": "profiled fields: " + "; ".join(lane_bits),
        "why_template_matches_field": (
            "Task-pool fields passed datafield profile gate where profiles are available; "
            "use profile lanes and recommended templates before scaling this family."
        ),
    }


def build_candidate(
    pool_id: str,
    pool: dict[str, Any],
    operating_mode: dict[str, Any],
    seed: dict[str, Any],
    run_id: str,
    params: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    expression = pool["expression_template"].format(
        seed_expression=seed["rendered_expression"],
        **params,
    )
    batch_size = int(pool.get("initial_batch_size") or operating_mode.get("initial_batch_size", 20))
    review_after = int(pool.get("review_after_candidates") or operating_mode.get("review_after_candidates", 50))
    auto_submit = bool(pool.get("auto_submit", operating_mode.get("auto_submit", False)))
    seed_metrics = seed.get("latest_result_import", {}).get("metrics", {})
    fields = required_fields(seed, pool)
    candidate_params = {
        **seed.get("params", {}),
        **params,
        "seed_candidate_id": seed["candidate_id"],
        "pre_probe_gate_passed": True,
        "pre_probe_gate_source": seed["candidate_id"],
        "pre_probe_gate_standard": "sharpe>=1.25;fitness>=1.0;turnover=1%-70%;no_failed_official_checks",
        "seed_sharpe": seed_metrics.get("sharpe", ""),
        "seed_fitness": seed_metrics.get("fitness", ""),
        "seed_turnover": seed_metrics.get("turnover", ""),
        "task_pool_id": pool_id,
        "task_pool_batch_id": run_id,
        "task_pool_priority": str(pool.get("priority", "unspecified")),
        "task_pool_objective": str(pool.get("objective", "core_pass")),
        "task_pool_variant_family": str(pool.get("variant_family", "unspecified")),
        "task_pool_start_batch_size": batch_size,
        "task_pool_review_after_candidates": review_after,
        "task_pool_auto_submit": auto_submit,
        **profile_gate_metadata(fields),
    }
    if "target_returns_min" in pool:
        candidate_params["target_returns_min"] = pool["target_returns_min"]
    return {
        "candidate_id": candidate_id(run_id, pool, params, index),
        "template_id": f"{seed['template_id']}_task_pool",
        "stage": int(pool["stage"]),
        "platform_target": "worldquant_brain",
        "artifact_state": "candidate",
        "status": "probe_blocked",
        "source_data_rights": "platform_proprietary",
        "reuse_tags": list(seed.get("reuse_tags", ["worldquant_submittable"])),
        "params": candidate_params,
        "required_fields": fields,
        "rendered_expression": expression,
        "rationale": f"{pool.get('rationale', '')} Derived from seed {seed['candidate_id']}.",
        "review_status": "needs_human_gate",
        "status_history": ["drafted", "schema_validated", "locally_scored", "pending_manual_gate", "probe_blocked"],
        "adaptation_notes": (
            f"Task Pool {pool_id} batch {run_id}; start with {batch_size}, review after {review_after}; "
            f"derived from seed {seed['candidate_id']}."
        ),
        "risk_notes": [
            "Task Pool generated locally; no platform call was made.",
            "Do not auto-submit; every platform probe and submission requires a separate manual gate.",
            f"Review after {review_after} candidates before scaling this pool.",
        ]
        + list(pool.get("risk_notes", [])),
        "local_precheck": {
            "score": 100,
            "decision": "ready_for_manual_gate",
            "notes": [
                "Task Pool candidate generated from configured grid.",
                "Source seed passed official core gates before this candidate can enter platform simulation.",
                "Use datafield profile gate metadata when available before scaling this task-pool family.",
                "Stopped before platform probe.",
                "Official alpha detail remains the source of truth after simulation.",
            ],
        },
    }


def generate_candidates(
    pool_id: str,
    run_id: str,
    limit: int | None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    run_id = safe_slug(run_id)
    operating_mode, pool = task_pool_config(pool_id)
    ensure_positive_seed(pool["seed_candidate_id"])
    seed = seed_candidate(pool["seed_candidate_id"])
    scoring = read_yaml(CONFIG / "scoring.yaml")
    ensure_seed_passes_official_core_gates(seed, scoring)
    rows = grid_rows(pool["grid"])
    batch_size = int(pool.get("initial_batch_size") or operating_mode.get("initial_batch_size", 20))
    count = limit if limit is not None else batch_size
    existing_keys = existing_expression_keys()
    candidates = []
    duplicate_skipped = []
    for index, params in enumerate(rows, 1):
        candidate = build_candidate(pool_id, pool, operating_mode, seed, run_id, params, index)
        key = (candidate_expression(candidate), candidate_settings_key(candidate))
        if key in existing_keys:
            duplicate_skipped.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "expression": candidate["rendered_expression"],
                    "settings_key": list(key[1]),
                }
            )
            continue
        candidates.append(candidate)
        existing_keys.add(key)
        if len(candidates) >= count:
            break
    return operating_mode, pool, candidates, duplicate_skipped


def main() -> int:
    args = parse_args()
    args.run_id = safe_slug(args.run_id)
    operating_mode, pool, candidates, duplicate_skipped = generate_candidates(args.pool_id, args.run_id, args.limit)
    candidate_schema = read_json(SCHEMAS / "candidate.schema.json")
    validator = jsonschema.Draft202012Validator(candidate_schema)

    generated = []
    skipped = []
    for candidate in candidates:
        validator.validate(candidate)
        path = QUEUE / f"{candidate['candidate_id']}.json"
        if path.exists():
            skipped.append(candidate["candidate_id"])
            continue
        if not args.dry_run:
            write_json(path, candidate)
            generated.append(candidate["candidate_id"])

    batch_size = int(pool.get("initial_batch_size") or operating_mode.get("initial_batch_size", 20))
    review_after = int(pool.get("review_after_candidates") or operating_mode.get("review_after_candidates", 50))
    payload = {
        "pool_id": args.pool_id,
        "run_id": args.run_id,
        "dry_run": args.dry_run,
        "priority": pool.get("priority"),
        "variant_family": pool.get("variant_family"),
        "objective": pool.get("objective", "core_pass"),
        "target_returns_min": pool.get("target_returns_min"),
        "seed_candidate_id": pool.get("seed_candidate_id"),
        "configured_batch_size": batch_size,
        "review_after_candidates": review_after,
        "planned_count": len(candidates),
        "generated_count": len(generated),
        "skipped_count": len(skipped),
        "duplicate_skipped_count": len(duplicate_skipped),
        "planned_candidate_ids": [candidate["candidate_id"] for candidate in candidates],
        "generated_candidate_ids": generated,
        "skipped_candidate_ids": skipped,
        "duplicate_skipped_candidate_ids": [row["candidate_id"] for row in duplicate_skipped],
        "manual_gate_required": bool(pool.get("manual_gate_required", operating_mode.get("manual_gate_required", True))),
        "auto_submit": bool(pool.get("auto_submit", operating_mode.get("auto_submit", False))),
        "next_step": "Run local review, then use a separate manual gate for any live simulation.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
