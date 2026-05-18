#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import jsonschema


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
QUEUE = STATE / "queue"
LEDGER = STATE / "ledger"
SCHEMAS = ROOT / "schemas"


def read_json(path: pathlib.Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local-only variants from a positive seed candidate.")
    parser.add_argument("--seed-candidate-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--variant-set",
        choices=["first_pass", "stability"],
        default="first_pass",
        help="first_pass keeps the original group/time variants; stability generates event-gated and bounded variants.",
    )
    return parser.parse_args()


def ensure_positive_seed(seed_candidate_id: str) -> dict[str, Any]:
    iteration_ledger = read_json(LEDGER / "iteration-ledger.json")
    if not isinstance(iteration_ledger, list):
        raise RuntimeError("iteration-ledger.json must contain a list.")
    for row in iteration_ledger:
        if row.get("candidate_id") == seed_candidate_id:
            if not row.get("success_retro_required"):
                raise RuntimeError(f"Seed is not marked for success retro: {seed_candidate_id}")
            return row
    raise FileNotFoundError(f"Seed not found in iteration ledger: {seed_candidate_id}")


def base_candidate(seed_candidate_id: str) -> dict[str, Any]:
    path = QUEUE / f"{seed_candidate_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Seed candidate not found: {path}")
    candidate = read_json(path)
    if not isinstance(candidate, dict):
        raise RuntimeError("Seed candidate must be an object.")
    return candidate


def leverage_ratio_variants(seed: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    expression = seed["rendered_expression"]
    template_id = seed["template_id"]
    source_id = seed["candidate_id"]
    variants = [
        {
            "suffix": "group-industry",
            "stage": 2,
            "params": {**seed.get("params", {}), "group_field": "industry"},
            "required_fields": sorted(set(seed.get("required_fields", []) + ["industry"])),
            "expression": f"group_rank({expression}, industry)",
            "rationale": "基于正向 seed 加入行业内比较，验证结构性财务比率是否主要来自同业排序而不是粗行业暴露。",
        },
        {
            "suffix": "group-subindustry",
            "stage": 2,
            "params": {**seed.get("params", {}), "group_field": "subindustry"},
            "required_fields": sorted(set(seed.get("required_fields", []) + ["subindustry"])),
            "expression": f"group_rank({expression}, subindustry)",
            "rationale": "基于正向 seed 加入子行业内比较，用更细分组测试去相关和稳定性。",
        },
        {
            "suffix": "ts-rank-126",
            "stage": 1,
            "params": {**seed.get("params", {}), "lookback": 126},
            "required_fields": list(seed.get("required_fields", [])),
            "expression": f"ts_rank({expression}, 126)",
            "rationale": "基于正向 seed 加入中期时间序列排名，测试财务结构关系在自身历史位置中的稳定性。",
        },
        {
            "suffix": "ts-rank-252",
            "stage": 1,
            "params": {**seed.get("params", {}), "lookback": 252},
            "required_fields": list(seed.get("required_fields", [])),
            "expression": f"ts_rank({expression}, 252)",
            "rationale": "基于正向 seed 加入长期时间序列排名，观察窗口拉长后是否降低噪音。",
        },
    ]
    candidates = []
    for variant in variants:
        candidates.append(
            {
                "candidate_id": f"cand-{run_id}-{variant['suffix']}",
                "template_id": f"{template_id}_variant",
                "stage": variant["stage"],
                "platform_target": "worldquant_brain",
                "artifact_state": "candidate",
                "status": "probe_blocked",
                "source_data_rights": "platform_proprietary",
                "reuse_tags": list(seed.get("reuse_tags", ["worldquant_submittable"])),
                "params": variant["params"],
                "required_fields": variant["required_fields"],
                "rendered_expression": variant["expression"],
                "rationale": f"{variant['rationale']} Derived from seed {source_id}.",
                "review_status": "needs_human_gate",
                "status_history": ["drafted", "schema_validated", "locally_scored", "pending_manual_gate", "probe_blocked"],
                "adaptation_notes": f"Derived from seed {source_id}; local-only candidate, not submitted or simulated yet.",
                "risk_notes": [
                    "Generated from a positive seed but still requires manual gate.",
                    "Do not auto-submit; verify self-correlation and test-period stability first.",
                ],
                "local_precheck": {
                    "score": 100,
                    "decision": "ready_for_manual_gate",
                    "notes": [
                        "Seed variant generated from positive retro.",
                        "Expression is distinct from the seed.",
                        "Stopped before platform probe.",
                    ],
                },
            }
        )
    return candidates


def leverage_ratio_stability_variants(seed: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    expression = seed["rendered_expression"]
    template_id = seed["template_id"]
    source_id = seed["candidate_id"]
    base_fields = list(seed.get("required_fields", []))
    variants = [
        {
            "suffix": "liquidity-ratio-gated",
            "stage": 3,
            "params": {
                **seed.get("params", {}),
                "entry": "volume_10_over_60_gt_1",
                "exit_threshold": 0.1,
                "empirical_priority": "mainline",
                "review_label": "stability_mainline",
            },
            "required_fields": sorted(set(base_fields + ["volume", "returns"])),
            "expression": f"trade_when(ts_mean(volume, 10) / ts_mean(volume, 60) > 1, {expression}, abs(returns) > 0.1)",
            "rationale": "用成交量短长均线比值构造无量纲流动性门控，保留上一条 liquidity-gated 的正向信号，同时减少 UNITS warning 风险。",
            "adaptation_label": "unit-clean stability repair",
            "risk_notes": ["Mainline because liquidity gates showed the best test stability so far."],
        },
        {
            "suffix": "liquidity-gated",
            "stage": 3,
            "params": {
                **seed.get("params", {}),
                "entry": "volume_10_gt_60",
                "exit_threshold": 0.1,
                "empirical_priority": "mainline",
                "review_label": "stability_mainline",
            },
            "required_fields": sorted(set(base_fields + ["volume", "returns"])),
            "expression": f"trade_when(ts_mean(volume, 10) > ts_mean(volume, 60), {expression}, abs(returns) > 0.1)",
            "rationale": "在成交活跃时才启用财务杠杆信号，并在极端日收益时退出，测试事件门是否改善测试期稳定性。",
            "adaptation_label": "stability repair",
            "risk_notes": ["Mainline structure, but direct volume comparison can still trigger UNITS warning."],
        },
        {
            "suffix": "calm-market-gated",
            "stage": 3,
            "params": {
                **seed.get("params", {}),
                "entry": "returns_vol_5_lt_20",
                "exit_threshold": 0.1,
                "empirical_priority": "limited_contrast",
                "review_label": "good_but_unstable_contrast",
            },
            "required_fields": sorted(set(base_fields + ["returns"])),
            "expression": f"trade_when(ts_std_dev(returns, 5) < ts_std_dev(returns, 20), {expression}, abs(returns) > 0.1)",
            "rationale": "只在短期波动低于中期波动时持有财务杠杆信号，测试避开高噪音阶段是否改善泛化。",
            "adaptation_label": "stability repair",
            "risk_notes": [
                "GOOD sample had weak test stability; use this branch as a limited contrast, not a high-volume mainline.",
            ],
        },
        {
            "suffix": "winsor-rank",
            "stage": 2,
            "params": {
                **seed.get("params", {}),
                "winsorize_std": 4,
                "empirical_priority": "demoted",
                "review_label": "bounded_rank_unstable",
            },
            "required_fields": list(base_fields),
            "expression": f"rank(winsorize({expression}, std=4))",
            "rationale": "用 winsorize 限制极端值，再做横截面 rank，测试温和去极值是否保留核心收益并降低不稳定性。",
            "adaptation_label": "stability repair",
            "risk_notes": ["Demoted because the tested winsor-rank branch turned test Sharpe negative."],
        },
        {
            "suffix": "winsor-scale",
            "stage": 2,
            "params": {
                **seed.get("params", {}),
                "winsorize_std": 4,
                "empirical_priority": "limited_contrast",
                "review_label": "bounded_scale_contrast",
            },
            "required_fields": list(base_fields),
            "expression": f"scale(winsorize({expression}, std=4))",
            "rationale": "用 winsorize 限制极端值，再做 scale 控制权重尺度，测试更平滑的权重分布是否改善稳定性。",
            "adaptation_label": "stability repair",
            "risk_notes": ["Keep as a small contrast until bounded transforms show positive test stability."],
        },
        {
            "suffix": "liquidity-ranked-exit",
            "stage": 3,
            "params": {
                **seed.get("params", {}),
                "entry": "ranked_volume_ratio_gt_0_5",
                "exit": "rank_abs_returns_gt_0_95",
                "empirical_priority": "demoted",
                "review_label": "units_clean_but_unstable",
            },
            "required_fields": sorted(set(base_fields + ["volume", "returns"])),
            "expression": (
                f"trade_when(rank(ts_mean(volume, 10) / ts_mean(volume, 60)) > 0.5, "
                f"{expression}, rank(abs(returns)) > 0.95)"
            ),
            "rationale": (
                "将流动性入场条件和极端收益退出条件都转为横截面 rank 后再比较常数。"
                "S06 已证明该分支能清除 UNITS warning，但会让测试期转负，因此只保留为低优先级对照，不作为主线放大。"
            ),
            "adaptation_label": "UNITS warning repair demoted after weak test stability",
            "risk_notes": ["Demoted because S06 cleared UNITS warning but made test Sharpe negative."],
        },
    ]
    candidates = []
    for variant in variants:
        candidates.append(
            {
                "candidate_id": f"cand-{run_id}-{variant['suffix']}",
                "template_id": f"{template_id}_stability",
                "stage": variant["stage"],
                "platform_target": "worldquant_brain",
                "artifact_state": "candidate",
                "status": "probe_blocked",
                "source_data_rights": "platform_proprietary",
                "reuse_tags": list(seed.get("reuse_tags", ["worldquant_submittable"])),
                "params": variant["params"],
                "required_fields": variant["required_fields"],
                "rendered_expression": variant["expression"],
                "rationale": f"{variant['rationale']} Derived from seed {source_id}.",
                "review_status": "needs_human_gate",
                "status_history": ["drafted", "schema_validated", "locally_scored", "pending_manual_gate", "probe_blocked"],
                "adaptation_notes": (
                    f"{variant['adaptation_label']} derived from seed {source_id}; "
                    "local-only candidate, not submitted or simulated yet."
                ),
                "risk_notes": [
                    "Generated after group_rank variants failed to improve test-period stability.",
                    "Do not auto-submit; verify self-correlation and test-period stability first.",
                ]
                + variant.get("risk_notes", []),
                "local_precheck": {
                    "score": 100,
                    "decision": "ready_for_manual_gate",
                    "notes": [
                        "Stability repair candidate generated from positive retro.",
                        "Expression is distinct from tested group-rank and raw ts-rank variants.",
                        "Stopped before platform probe.",
                    ],
                },
            }
        )
    return candidates


def generate(seed: dict[str, Any], run_id: str, variant_set: str) -> list[dict[str, Any]]:
    if seed.get("template_id") == "leverage_ratio":
        if variant_set == "stability":
            return leverage_ratio_stability_variants(seed, run_id)
        return leverage_ratio_variants(seed, run_id)
    raise RuntimeError(f"No seed-variant rule exists for template: {seed.get('template_id')}")


def main() -> int:
    args = parse_args()
    ensure_positive_seed(args.seed_candidate_id)
    seed = base_candidate(args.seed_candidate_id)
    candidate_schema = read_json(SCHEMAS / "candidate.schema.json")
    validator = jsonschema.Draft202012Validator(candidate_schema)

    generated = []
    skipped = []
    for candidate in generate(seed, args.run_id, args.variant_set)[: args.limit]:
        path = QUEUE / f"{candidate['candidate_id']}.json"
        if path.exists():
            skipped.append(candidate["candidate_id"])
            continue
        validator.validate(candidate)
        write_json(path, candidate)
        generated.append(candidate["candidate_id"])

    print(
        json.dumps(
            {
                "seed_candidate_id": args.seed_candidate_id,
                "variant_set": args.variant_set,
                "generated_count": len(generated),
                "skipped_count": len(skipped),
                "generated_candidate_ids": generated,
                "skipped_candidate_ids": skipped,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
