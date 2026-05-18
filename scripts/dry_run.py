#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
from string import Formatter

import jsonschema
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SCHEMAS = ROOT / "schemas"
STATE = ROOT / "state"
QUEUE = STATE / "queue"
IMPORTS = STATE / "imports"
REVIEWS = STATE / "reviews"
LEDGER = STATE / "ledger"
EXAMPLES = ROOT / "examples" / "candidates"


def read_yaml(path: pathlib.Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_inputs():
    fields = read_yaml(CONFIG / "fields.yaml")["fields"]
    templates = read_yaml(CONFIG / "templates.yaml")["templates"]
    scoring = read_yaml(CONFIG / "scoring.yaml")
    lessons = read_yaml(CONFIG / "lessons.yaml")
    reuse_tags = read_yaml(CONFIG / "reuse-tags.yaml")["tags"]
    candidate_schema = read_json(SCHEMAS / "candidate.schema.json")
    result_schema = read_json(SCHEMAS / "result-import.schema.json")
    review_schema = read_json(SCHEMAS / "review-gate.schema.json")
    return fields, templates, scoring, lessons, reuse_tags, candidate_schema, result_schema, review_schema


def render_expression(template: dict, params: dict) -> str:
    return template["expression_template"].format(**params)


def compute_operator_count(expression: str) -> int:
    return expression.count("(")


def build_rationale(template_id: str, template: dict, lessons: dict) -> str:
    stage_key = f"stage_{template['stage']}"
    lesson_bits = []
    if stage_key in lessons["stages"]:
        lesson_bits.append(lessons["stages"][stage_key]["explanation"])
    for key in template.get("lesson_keys", []):
        if key in lessons.get("operator_families", {}):
            lesson_bits.append(lessons["operator_families"][key]["explanation"])
        if key in lessons.get("signal_families", {}):
            lesson_bits.append(lessons["signal_families"][key]["explanation"])
    label = template.get("label", template_id)
    merged = " ".join(dict.fromkeys(bit.strip() for bit in lesson_bits if bit.strip()))
    return f"{label}：{merged}".strip()


def precheck_candidate(candidate: dict, template: dict, fields: dict, scoring: dict) -> dict:
    weights = scoring["local_precheck"]["weights"]
    hard_blocks = []
    notes = []
    score = 0

    schema_ok = True
    if schema_ok:
        score += weights["schema_valid"]
        notes.append("Schema 完整。")

    missing_fields = [field for field in candidate["required_fields"] if field not in fields]
    if missing_fields:
        hard_blocks.append(f"缺少字段: {', '.join(missing_fields)}")
    else:
        score += weights["fields_valid"]
        notes.append("字段均在最小字段池内。")

    if candidate["template_id"] == template.get("_id"):
        score += weights["template_match"]
        notes.append("模板匹配成功。")

    stage_max = scoring["local_precheck"]["operator_budgets"][f"stage_{candidate['stage']}_max"]
    operator_count = compute_operator_count(candidate["rendered_expression"])
    if operator_count <= stage_max:
        score += weights["stage_fit"]
        notes.append(f"表达式复杂度在 stage {candidate['stage']} 预算内。")
    else:
        notes.append(f"表达式复杂度超预算：{operator_count}>{stage_max}")

    if candidate["source_data_rights"] == "platform_proprietary":
        score += weights["boundary_ok"]
        notes.append("数据权限被正确标记为平台专有，边界清晰。")
    else:
        notes.append("数据权限不是 platform_proprietary，需要人工复核。")

    if candidate["artifact_state"] in ["submitted_to_platform", "accepted_or_monetized"]:
        hard_blocks.append("exact artifact 已进入受限状态。")

    if hard_blocks:
        decision = "blocked"
    elif score >= scoring["local_precheck"]["thresholds"]["ready_for_manual_gate"]:
        decision = "ready_for_manual_gate"
    elif score >= scoring["local_precheck"]["thresholds"]["needs_revision"]:
        decision = "needs_revision"
    else:
        decision = "blocked"

    notes.extend(hard_blocks)
    return {"score": score, "decision": decision, "notes": notes}


def transition(candidate: dict, scoring: dict) -> list[str]:
    allowed = scoring["state_machine"]["allowed_transitions"]
    history = [candidate["status"]]
    for next_status in ["schema_validated", "locally_scored", scoring["default_decision"]["after_local_dry_run"]]:
        current = history[-1]
        if next_status in allowed.get(current, []):
            history.append(next_status)
    if history[-1] == "pending_manual_gate":
        blocked = scoring["default_decision"]["default_probe_decision"]
        if blocked in allowed.get(history[-1], []):
            history.append(blocked)
    return history


def build_candidates(templates: dict, fields: dict, scoring: dict, lessons: dict):
    sample_ids = [
        "operating_income_time_series_rank",
        "liabilities_appreciation_negative_rank",
        "leverage_ratio",
        "analyst_earnings_yield_momentum",
        "liquidity_gated_sentiment_entry",
    ]
    candidates = []
    for idx, template_id in enumerate(sample_ids, 1):
        template = dict(templates[template_id])
        template["_id"] = template_id
        params = dict(template["default_params"])
        expression = render_expression(template, params)
        candidate = {
            "candidate_id": f"cand-example-{idx:02d}",
            "template_id": template_id,
            "stage": template["stage"],
            "platform_target": "worldquant_brain",
            "artifact_state": "candidate",
            "status": "drafted",
            "source_data_rights": "platform_proprietary",
            "reuse_tags": template["reuse_tags"],
            "params": params,
            "required_fields": template["required_fields"],
            "rendered_expression": expression,
            "rationale": build_rationale(template_id, template, lessons),
            "review_status": "not_reviewed",
            "adaptation_notes": "Off-platform reuse requires field remapping and a fresh implementation.",
            "risk_notes": [
                "No platform call is allowed in dry-run mode.",
                "Any future connector use must pass a separate gate."
            ]
        }
        candidate["local_precheck"] = precheck_candidate(candidate, template, fields, scoring)
        candidate["status_history"] = transition(candidate, scoring)
        candidate["status"] = candidate["status_history"][-1]
        if candidate["status"] == "probe_blocked":
            candidate["review_status"] = "needs_human_gate"
        candidates.append(candidate)
    return candidates


def ensure_dirs():
    for path in [QUEUE, IMPORTS, REVIEWS, LEDGER, EXAMPLES]:
        path.mkdir(parents=True, exist_ok=True)


def validate_candidates(candidates: list[dict], candidate_schema: dict):
    validator = jsonschema.Draft202012Validator(candidate_schema)
    errors = []
    for candidate in candidates:
        for error in validator.iter_errors(candidate):
            errors.append(f"{candidate['candidate_id']}: {error.message}")
    return errors


def build_review_records(candidates: list[dict]):
    reviews = []
    for candidate in candidates:
        reviews.append(
            {
                "candidate_id": candidate["candidate_id"],
                "gate_level": "gate_0_local_only",
                "decision": "blocked",
                "reason": "P13 边界要求先停在本地 dry-run，不进入真实 connector 核验。",
                "notes": "如需 Gate 1 或 Gate 2，必须另行批准。"
            }
        )
    return reviews


def write_outputs(candidates: list[dict], reviews: list[dict]):
    write_json(LEDGER / "dry-run-summary.json", {
        "run_id": "dry-run-example",
        "candidate_count": len(candidates),
        "final_statuses": {candidate["candidate_id"]: candidate["status"] for candidate in candidates},
        "gate_required": True
    })
    for candidate in candidates:
        write_json(EXAMPLES / f"{candidate['candidate_id']}.json", candidate)
        write_json(QUEUE / f"{candidate['candidate_id']}.json", candidate)
    for review in reviews:
        write_json(REVIEWS / f"{review['candidate_id']}.json", review)


def main() -> int:
    ensure_dirs()
    fields, templates, scoring, lessons, reuse_tags, candidate_schema, result_schema, review_schema = load_inputs()
    candidates = build_candidates(templates, fields, scoring, lessons)
    errors = validate_candidates(candidates, candidate_schema)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    review_validator = jsonschema.Draft202012Validator(review_schema)
    reviews = build_review_records(candidates)
    for review in reviews:
        review_validator.validate(review)

    write_outputs(candidates, reviews)
    print(json.dumps({
        "run_id": "dry-run-example",
        "candidate_count": len(candidates),
        "final_statuses": {candidate["candidate_id"]: candidate["status"] for candidate in candidates},
        "gate_required": True
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
