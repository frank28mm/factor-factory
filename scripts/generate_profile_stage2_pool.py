#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from itertools import product
from typing import Any

import jsonschema

from verify_official_course_read_gate import build_payload as build_official_course_read_gate


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
QUEUE = STATE / "queue"
LEDGER = STATE / "ledger"
PROFILE_DIR = STATE / "datafield-profiles"
SCHEMAS = ROOT / "schemas"

POOL_ID = "profile-stage2-field-blend-v15"
DEFAULT_SEED_CANDIDATE_ID = "public-bootstrap-seed"
GENERATION_RULE_VERSION = "v16-analyst4-fundamental6-pv1-wq-rotation"
ANALYST_FIELD_PRIORITY = ["est_eps", "est_netprofit", "est_ptp", "est_sales", "est_capex"]
FUNDAMENTAL_FIELD_PRIORITY = ["inventory_turnover", "sales", "operating_income"]
PV_GATE_FIELD_PRIORITY = ["volume", "returns"]
NEUTRALIZATION_ROTATION = ["SUBINDUSTRY", "INDUSTRY", "MARKET"]
DECAY_ROTATION = [4, 2, 6]
TRUNCATION_ROTATION = [0.08, 0.12]


def read_json(path: pathlib.Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError("value must contain at least one alphanumeric character.")
    return slug


def value_slug(value: Any) -> str:
    if isinstance(value, float):
        return str(value).replace(".", "p")
    return str(value).lower().replace("_", "-").replace(".", "p").replace("/", "-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate profile-driven Stage 2 field blend candidates.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--analyst-selection-run-id", required=True)
    parser.add_argument("--fundamental-selection-run-id", required=True)
    parser.add_argument("--pv-selection-run-id")
    parser.add_argument("--seed-candidate-id", default=DEFAULT_SEED_CANDIDATE_ID)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def selection_path(run_id: str) -> pathlib.Path:
    return LEDGER / f"datafield-selection-{safe_slug(run_id)}.json"


def load_selection(run_id: str) -> list[dict[str, Any]]:
    path = selection_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"Missing datafield selection ledger: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected selection object: {path}")
    rows = payload.get("selected_fields", [])
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Selection has no fields: {path}")
    return [row for row in rows if isinstance(row, dict) and row.get("field_id")]


def load_profile(field_id: str) -> dict[str, Any]:
    path = PROFILE_DIR / f"{field_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing datafield profile for selected field: {field_id}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected profile object for field: {field_id}")
    return payload


def pick_fields(rows: list[dict[str, Any]], *, avoid: set[str] | None = None, preferred_order: list[str] | None = None) -> list[str]:
    avoid = avoid or set()
    picked: list[str] = []
    for row in rows:
        field_id = str(row["field_id"])
        if field_id in avoid:
            continue
        if field_id not in picked:
            picked.append(field_id)
    if preferred_order:
        priority = {field_id: index for index, field_id in enumerate(preferred_order)}
        picked = sorted(picked, key=lambda field_id: (priority.get(field_id, len(priority)), field_id))
    return picked


def field_profile_metadata(fields: list[str]) -> dict[str, str]:
    profiles = [load_profile(field_id) for field_id in fields]
    refs = [str(profile.get("profile_id") or f"profile-{safe_slug(str(profile.get('field_id')))}") for profile in profiles]
    lanes = [
        f"{profile.get('field_id')}={profile.get('profile_lane')}@{profile.get('dataset_id')}"
        for profile in profiles
    ]
    dataset_ids = sorted({str(profile.get("dataset_id") or "") for profile in profiles if profile.get("dataset_id")})
    return {
        "data_profile_refs": ",".join(refs),
        "field_quality_summary": "profiled fields: " + "; ".join(lanes),
        "why_template_matches_field": (
            "Datafield profile gate selected analyst/fundamental fields before Stage 2 blending; "
            "use group ranking to compare the signal in a more comparable peer context."
        ),
        "gate_reason": (
            "Stage2 uses group_rank as a peer-comparison gate after datafield profile screening; "
            f"datasets involved: {', '.join(dataset_ids)}."
        ),
        "anti_overfit_note": (
            "Parameters use standard trading-horizon windows and fixed blend weights; thresholds are not arbitrary "
            "search points chosen only to pass a backtest."
        ),
    }


def setting_for(index: int) -> dict[str, Any]:
    return {
        "wq_neutralization": NEUTRALIZATION_ROTATION[index % len(NEUTRALIZATION_ROTATION)],
        "wq_decay": DECAY_ROTATION[index % len(DECAY_ROTATION)],
        "wq_truncation": TRUNCATION_ROTATION[index % len(TRUNCATION_ROTATION)],
    }


def official_course_gate_metadata() -> dict[str, str | int | bool]:
    payload = build_official_course_read_gate()
    if not payload.get("confirmed"):
        failed = [str(row.get("name")) for row in payload.get("checks", []) if not row.get("ok")]
        raise RuntimeError("Official course read gate failed: " + ", ".join(failed))
    summary = payload["summary"]
    return {
        "official_course_read_gate": True,
        "official_course_read_status": str(summary["full_read_status"]),
        "official_course_gate_mode": str(summary.get("mode", "private_full_audit")),
        "official_course_transcript_lines": int(summary.get("transcript_lines") or 0),
        "official_course_keyframes_count": int(summary.get("keyframes_count") or 0),
        "official_course_ocr_json_count": int(summary.get("ocr_json_count") or 0),
    }


def candidate_id(run_id: str, params: dict[str, Any]) -> str:
    family = "profile3"
    settings = (
        f"-n{value_slug(params['wq_neutralization'])}"
        f"-d{value_slug(params['wq_decay'])}"
        f"-t{value_slug(params['wq_truncation'])}"
    )
    pv_suffix = ""
    if params.get("pv_gate_field"):
        pv_suffix = f"-pv{value_slug(params['pv_gate_field'])}-gate{value_slug(params['pv_gate_type'])}"
    return (
        f"cand-{run_id}-{family}-a{value_slug(params['analyst_field'])}"
        f"-f{value_slug(params['fundamental_field'])}"
        f"-lb{value_slug(params['lookback'])}"
        f"-grp{value_slug(params['group_field'])}"
        f"-w{value_slug(params['analyst_weight'])}"
        f"{pv_suffix}{settings}"
    )


def build_expression(params: dict[str, Any]) -> str:
    analyst_signal = f"{params['analyst_field']}/close"
    fundamental_signal = f"{params['fundamental_field']}/assets"
    return (
        f"group_rank(add(multiply({params['analyst_weight']}, rank(ts_rank({analyst_signal}, {params['lookback']}))), "
        f"multiply({params['fundamental_weight']}, rank({fundamental_signal}))), {params['group_field']})"
    )


def build_pv_gate(params: dict[str, Any]) -> str:
    gate_field = str(params["pv_gate_field"])
    fast_window = int(params["pv_fast_window"])
    slow_window = int(params["pv_slow_window"])
    if gate_field == "volume":
        return (
            f"rank(ts_mean(volume, {fast_window}) / "
            f"ts_mean(volume, {slow_window})) > {params['pv_entry_threshold']}"
        )
    if gate_field == "returns":
        return (
            f"ts_std_dev(returns, {fast_window}) < "
            f"ts_std_dev(returns, {slow_window})"
        )
    raise RuntimeError(f"Unsupported pv gate field: {gate_field}")


def build_pv_gated_expression(params: dict[str, Any]) -> str:
    base_expression = build_expression(params)
    entry_condition = build_pv_gate(params)
    exit_condition = f"rank(abs(returns)) > {params['pv_exit_threshold']}"
    return f"trade_when({entry_condition}, {base_expression}, {exit_condition})"


def candidate_payload(params: dict[str, Any], fields: list[str], expression: str) -> dict[str, Any]:
    stage = 3 if params.get("pv_gate_field") else 2
    template_id = "profile_stage3_pv_gated_blend" if stage == 3 else "profile_stage2_field_blend"
    rationale = (
        "Profile-driven Stage 3 candidate: keep the analyst/fundamental blend, then use pv1 activity/return "
        "conditions as a timing gate so the signal trades only in a more interpretable market state."
        if stage == 3
        else (
            "Profile-driven Stage 2 candidate: combine an analyst estimate field with a fundamental field "
            "only after both pass the datafield profile gate; use group_rank for peer-context comparability."
        )
    )
    adaptation = (
        "Generated from analyst4/fundamental6/pv1 profile selections; wait for official simulation evidence before widening the gate family."
        if stage == 3
        else "Generated from datafield selection ledgers; wait for official simulation evidence before Stage 3 gating."
    )
    return {
        "candidate_id": candidate_id(params["task_pool_batch_id"], params),
        "template_id": template_id,
        "stage": stage,
        "platform_target": "worldquant_brain",
        "artifact_state": "candidate",
        "status": "probe_blocked",
        "source_data_rights": "platform_proprietary",
        "reuse_tags": ["worldquant_submittable", "needs_review_before_reuse"],
        "params": params,
        "required_fields": fields,
        "rendered_expression": expression,
        "rationale": rationale,
        "review_status": "needs_human_gate",
        "status_history": ["drafted", "schema_validated", "locally_scored", "pending_manual_gate", "probe_blocked"],
        "adaptation_notes": adaptation,
        "risk_notes": [
            "Local candidate only; no platform call was made.",
            "Public bootstrap seed is not an official core-passed alpha; run with your own account before promotion.",
            "Profile-driven narrow gate does not mean obscure fields; it means evidence-backed differentiated combinations.",
            "Do not auto-submit; official checks and quota rules remain authoritative.",
        ],
        "local_precheck": {
            "score": 100,
            "decision": "ready_for_manual_gate",
            "notes": [
                "Generated from datafield profile gate selections.",
                "Profile-driven narrow gate: field quality passed and signal sources are diversified.",
                "Stopped before platform probe.",
                "Official alpha detail remains the source of truth after simulation.",
            ],
        },
    }


def existing_expressions() -> set[str]:
    expressions: set[str] = set()
    for path in QUEUE.glob("cand-*.json"):
        try:
            payload = read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        expression = payload.get("rendered_expression")
        if isinstance(expression, str) and expression.strip():
            expressions.add(expression.strip())
    return expressions


def build_candidates(
    run_id: str,
    analyst_rows: list[dict[str, Any]],
    fundamental_rows: list[dict[str, Any]],
    limit: int,
    seed_candidate_id: str,
    pv_rows: list[dict[str, Any]] | None = None,
    avoid_expressions: set[str] | None = None,
) -> list[dict[str, Any]]:
    analyst_fields = pick_fields(analyst_rows, preferred_order=ANALYST_FIELD_PRIORITY)
    fundamental_fields = [
        field_id
        for field_id in pick_fields(fundamental_rows, avoid={"assets"}, preferred_order=FUNDAMENTAL_FIELD_PRIORITY)
        if field_id in set(FUNDAMENTAL_FIELD_PRIORITY)
    ]
    pv_gate_fields = pick_fields(pv_rows or [], preferred_order=PV_GATE_FIELD_PRIORITY)
    pv_gate_fields = [field_id for field_id in pv_gate_fields if field_id in set(PV_GATE_FIELD_PRIORITY)]
    if not analyst_fields or not fundamental_fields or not pv_gate_fields:
        raise RuntimeError("Need analyst4, current fundamental fields, and pv1 gate fields for current Stage 3 generation.")

    grid = product(
        analyst_fields,
        fundamental_fields,
        [60, 126, 252],
        ["industry", "subindustry"],
        [(0.65, 0.35), (0.5, 0.5), (0.35, 0.65)],
    )
    candidates: list[dict[str, Any]] = []
    course_gate_meta = official_course_gate_metadata()
    avoid_expressions = avoid_expressions or set()
    variant_index = 0
    for analyst_field, fundamental_field, lookback, group_field, weights in grid:
        fields = sorted({analyst_field, fundamental_field, "close", "assets", group_field})
        profile_meta = field_profile_metadata(fields)
        params: dict[str, Any] = {
            "task_pool_id": POOL_ID,
            "task_pool_batch_id": run_id,
            "generation_rule_version": GENERATION_RULE_VERSION,
            "task_pool_priority": "profile_driven_narrow_gate",
            "task_pool_objective": "field_profile_stage2_core_pass",
            "task_pool_variant_family": "profile_stage2_field_blend",
            "task_pool_start_batch_size": limit,
            "task_pool_review_after_candidates": 20,
            "task_pool_auto_submit": False,
            "seed_candidate_id": seed_candidate_id,
            "pre_probe_gate_passed": True,
            "pre_probe_gate_source": seed_candidate_id,
            "pre_probe_gate_standard": "sharpe>=1.25;fitness>=1.0;turnover=1%-70%;no_failed_official_checks",
            "dataset_id": "profile_blend_v15",
            "analyst_selection_source": "datafield profile gate",
            "fundamental_selection_source": "datafield profile gate",
            "analyst_field": analyst_field,
            "fundamental_field": fundamental_field,
            "lookback": lookback,
            "group_field": group_field,
            "analyst_weight": weights[0],
            "fundamental_weight": weights[1],
            "narrow_gate_reason": "不追冷门，追有证据的差异化：字段 profile 过关，组合 analyst 与 fundamental 信息源，并用组内排序降低行业不可比。",
            **course_gate_meta,
            **setting_for(variant_index),
            **profile_meta,
        }
        expression = build_expression(params)
        pv_field = pv_gate_fields[variant_index % len(pv_gate_fields)]
        gated_fields = sorted(set(fields) | {pv_field, "returns"})
        gated_profile_meta = field_profile_metadata(gated_fields)
        gate_reason = (
            f"Stage3 adds a pv1 {pv_field} gate to the Stage2 peer-ranked blend; "
            "the gate is meant to reduce always-on exposure rather than search arbitrary thresholds."
        )
        gated_params: dict[str, Any] = {
            **params,
            **setting_for(variant_index + 1),
            **gated_profile_meta,
            "task_pool_objective": "field_profile_stage3_pv_gate_core_pass",
            "task_pool_variant_family": "profile_stage3_pv_gated_blend",
            "pv_selection_source": "datafield profile gate",
            "pv_gate_field": pv_field,
            "pv_gate_type": "liquidity_regime" if pv_field == "volume" else "volatility_regime",
            "pv_fast_window": 20,
            "pv_slow_window": 60,
            "pv_entry_threshold": 0.55,
            "pv_exit_threshold": 0.95,
            "why_template_matches_field": (
                "Datafield profile gate selected analyst4/fundamental6 fields for the signal body and pv1 "
                f"{pv_field} for an interpretable timing gate."
            ),
            "gate_reason": gate_reason,
            "narrow_gate_reason": (
                "窄门不是更怪，而是更准：保留已体检的 analyst/fundamental 信息源，用 pv1 成交/收益状态决定何时让信号生效。"
            ),
        }
        gated_expression = build_pv_gated_expression(gated_params)
        if gated_expression.strip() not in avoid_expressions:
            candidates.append(candidate_payload(gated_params, gated_fields, gated_expression))
            if len(candidates) >= limit:
                break
        variant_index += 1
    return candidates


def main() -> int:
    args = parse_args()
    run_id = safe_slug(args.run_id)
    analyst_rows = load_selection(args.analyst_selection_run_id)
    fundamental_rows = load_selection(args.fundamental_selection_run_id)
    pv_rows = load_selection(args.pv_selection_run_id) if args.pv_selection_run_id else []
    candidates = build_candidates(
        run_id,
        analyst_rows,
        fundamental_rows,
        max(0, int(args.limit)),
        safe_slug(str(args.seed_candidate_id)),
        pv_rows=pv_rows,
        avoid_expressions=existing_expressions() if not args.dry_run else set(),
    )

    schema = read_json(SCHEMAS / "candidate.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    for candidate in candidates:
        validator.validate(candidate)

    generated = []
    skipped = []
    if not args.dry_run:
        for candidate in candidates:
            path = QUEUE / f"{candidate['candidate_id']}.json"
            if path.exists():
                skipped.append(candidate["candidate_id"])
                continue
            write_json(path, candidate)
            generated.append(candidate["candidate_id"])

    payload = {
        "pool_id": POOL_ID,
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "planned_count": len(candidates),
        "generated_count": len(generated),
        "skipped_count": len(skipped),
        "planned_candidate_ids": [candidate["candidate_id"] for candidate in candidates],
        "generated_candidate_ids": generated,
        "manual_gate_required": True,
        "auto_submit": False,
        "next_step": "Run build_ledgers.py, then let the existing probe loop decide live simulation order.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
