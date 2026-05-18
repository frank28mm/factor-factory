#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
LEDGER = STATE / "ledger"
RETRO = STATE / "retro"


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def candidate_index() -> dict[str, dict[str, Any]]:
    return {row["candidate_id"]: row for row in read_json(LEDGER / "candidate-ledger.json")}


def result_index() -> dict[str, dict[str, Any]]:
    return {row["candidate_id"]: row for row in read_json(LEDGER / "result-ledger.json")}


def fmt_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, int):
        return str(value)
    return ""


def metric_slice(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "sharpe",
        "fitness",
        "returns",
        "turnover",
        "drawdown",
        "margin",
        "test_sharpe",
        "train_test_sharpe_gap",
    ]
    return {key: metrics.get(key) for key in keys if metrics.get(key) is not None}


def winner_teaching_summary(candidate: dict[str, Any], result: dict[str, Any]) -> str:
    metrics = result.get("metrics", {})
    expression = candidate.get("expression", "")
    return (
        "这条 winner 的核心价值在于：它不是裸因子，而是用交易门控限定了生效场景。"
        f"当前表达式 `{expression}` 的 Sharpe={fmt_metric(metrics.get('sharpe'))}、"
        f"Fitness={fmt_metric(metrics.get('fitness'))}、Returns={fmt_metric(metrics.get('returns'))}。"
        "下一轮应保留“门控 + 财务结构因子”的思想，但不要继续在同族参数上密集挖掘。"
    )


def archive_teaching_summary(candidate: dict[str, Any], archive: dict[str, Any]) -> str:
    return (
        "这条样本核心指标过线但自相关失败，说明它和已提交 winner 属于同一族群附近的参数变体。"
        f"自相关={fmt_metric(archive.get('self_correlation_value'))}，"
        f"相关对象={archive.get('self_correlated_alpha_id') or 'UNKNOWN'}。"
        "它的价值不是提交，而是证明该局部搜索空间已经被 winner 占住，应停止同族参数挖掘。"
    )


def pool_decision(status: str) -> str:
    if status in {"early_stopped_winner_submitted", "pause_after_winner_submitted", "demote_high_correlation"}:
        return "stop_same_family"
    if status == "low_yield_revise":
        return "revise_before_more_runs"
    if status == "wait_official_checks":
        return "wait_more_official_checks"
    return "continue_small_batch"


def pool_actions(decision: str) -> list[str]:
    if decision == "stop_same_family":
        return ["move_to_new_dataset_or_structure", "change_neutralization", "preserve_winner_as_seed"]
    if decision == "revise_before_more_runs":
        return ["review_failed_pool", "change_parameter_grid", "switch_dataset_or_neutralization"]
    if decision == "wait_more_official_checks":
        return ["continue_low_frequency_check_refresh", "do_not_scale_yet"]
    return ["continue_small_batch", "keep_manual_submit_gate"]


def build_winners(candidates: dict[str, dict[str, Any]], results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_alpha: dict[str, dict[str, Any]] = {}
    for candidate_id, result in results.items():
        if not result.get("submitted"):
            continue
        candidate = candidates.get(candidate_id, {})
        alpha_id = str(result.get("alpha_id") or candidate_id)
        row = {
            "candidate_id": candidate_id,
            "related_candidate_ids": [candidate_id],
            "alpha_id": result.get("alpha_id"),
            "lesson_type": "submitted_winner",
            "template_id": candidate.get("template_id"),
            "stage": candidate.get("stage"),
            "expression": candidate.get("expression"),
            "task_pool_id": candidate.get("params", {}).get("task_pool_id"),
            "date_submitted": result.get("date_submitted"),
            "grade": result.get("grade"),
            "metrics": metric_slice(result.get("metrics", {})),
            "teaching_summary_cn": winner_teaching_summary(candidate, result),
            "recommended_next_actions": [
                "preserve_gate_structure",
                "avoid_same_family_parameter_mining",
                "switch_dataset_or_neutralization",
                "use_as_generation_seed_with_stronger_decorrelation",
            ],
        }
        current = rows_by_alpha.get(alpha_id)
        if current:
            current.setdefault("related_candidate_ids", []).append(candidate_id)
            current_score = (
                current.get("metrics", {}).get("fitness") or 0,
                current.get("metrics", {}).get("sharpe") or 0,
            )
            row_score = (
                row.get("metrics", {}).get("fitness") or 0,
                row.get("metrics", {}).get("sharpe") or 0,
            )
            if row_score <= current_score:
                continue
            row["related_candidate_ids"] = current["related_candidate_ids"]
        rows_by_alpha[alpha_id] = row
    rows = list(rows_by_alpha.values())
    return sorted(rows, key=lambda row: (row.get("metrics", {}).get("fitness") or 0, row.get("metrics", {}).get("sharpe") or 0), reverse=True)


def build_archives(candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    archive_payload = read_json(LEDGER / "correlation-archive.json")
    rows = []
    for archive in archive_payload.get("archived_pool", []):
        candidate_id = archive.get("candidate_id")
        candidate = candidates.get(str(candidate_id), {})
        rows.append(
            {
                "candidate_id": candidate_id,
                "alpha_id": archive.get("alpha_id"),
                "lesson_type": "high_correlation_archive",
                "blocked_by_alpha_id": archive.get("self_correlated_alpha_id"),
                "self_correlation_value": archive.get("self_correlation_value"),
                "template_id": archive.get("template_id"),
                "stage": archive.get("stage"),
                "expression": archive.get("expression"),
                "task_pool_id": candidate.get("params", {}).get("task_pool_id"),
                "metrics": metric_slice(archive),
                "teaching_summary_cn": archive_teaching_summary(candidate, archive),
                "recommended_next_actions": [
                    "do_not_delete",
                    "exclude_from_submission_pool",
                    "stop_same_family_generation",
                    "switch_dataset_or_neutralization",
                ],
            }
        )
    return rows


def build_pool_retros() -> list[dict[str, Any]]:
    task_pools = {row["pool_id"]: row for row in read_json(LEDGER / "task-pool-ledger.json").get("pools", [])}
    rows = []
    for strategy in read_json(LEDGER / "pool-strategy.json").get("pools", []):
        pool_id = strategy.get("pool_id")
        task_pool = task_pools.get(pool_id, {})
        decision = pool_decision(str(strategy.get("pool_status", "")))
        rows.append(
            {
                "pool_id": pool_id,
                "lesson_type": "task_pool_retro",
                "pool_status": strategy.get("pool_status"),
                "decision": decision,
                "stage": strategy.get("stage"),
                "dataset_id": strategy.get("dataset_id"),
                "neutralization": strategy.get("neutralization"),
                "candidate_count": task_pool.get("candidate_count", 0),
                "simulated_count": strategy.get("simulated_count", 0),
                "core_passed_count": strategy.get("core_passed_count", 0),
                "submitted_count": strategy.get("submitted_count", 0),
                "archived_count": strategy.get("archived_count", 0),
                "waiting_checks_count": strategy.get("waiting_checks_count", 0),
                "teaching_summary_cn": strategy.get("recommended_action_cn"),
                "recommended_next_actions": pool_actions(decision),
            }
        )
    return rows


def next_strategy(winners: list[dict[str, Any]], archives: list[dict[str, Any]], pools: list[dict[str, Any]]) -> dict[str, Any]:
    blocked_pool_ids = [row["pool_id"] for row in pools if row["decision"] in {"stop_same_family", "revise_before_more_runs"}]
    return {
        "mode": "switch_family_after_winner_or_low_yield",
        "blocked_pool_ids": blocked_pool_ids,
        "recommended_focus_cn": [
            "不继续在已提交 winner 的同族参数附近密集搜索。",
            "下一轮优先换数据集、字段族、中性化或表达式结构，而不是只调窗口和阈值。",
            "高相关归档样本保留为证据，用于解释为什么该族群停止。",
            "仍保留人工 Submit gate，脚本只负责 simulation、check、回填和复盘。",
        ],
        "winner_count": len(winners),
        "archive_count": len(archives),
    }


def build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Factor Factory 复盘报告",
        "",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- Submitted winner：`{payload['summary']['winner_count']}`",
        f"- 高相关归档：`{payload['summary']['archive_count']}`",
        f"- 任务池复盘：`{payload['summary']['pool_count']}`",
        "",
        "## Winner 复盘",
    ]
    for row in payload["winners"][:8]:
        lines.extend(
            [
                "",
                f"### {row.get('alpha_id') or row['candidate_id']}",
                f"- Candidate：`{row['candidate_id']}`",
                f"- 表达式：`{row.get('expression') or ''}`",
                f"- 指标：Sharpe `{fmt_metric(row['metrics'].get('sharpe'))}` / Fitness `{fmt_metric(row['metrics'].get('fitness'))}` / Returns `{fmt_metric(row['metrics'].get('returns'))}` / Test Sharpe `{fmt_metric(row['metrics'].get('test_sharpe'))}`",
                f"- 复盘：{row['teaching_summary_cn']}",
                f"- 下一步：{', '.join(row['recommended_next_actions'])}",
            ]
        )
    lines.extend(["", "## 高相关归档"])
    for row in payload["correlation_archives"][:12]:
        lines.extend(
            [
                "",
                f"### {row.get('alpha_id') or row['candidate_id']}",
                f"- Candidate：`{row['candidate_id']}`",
                f"- 被谁挡住：`{row.get('blocked_by_alpha_id') or 'UNKNOWN'}`",
                f"- 自相关：`{fmt_metric(row.get('self_correlation_value'))}`",
                f"- 复盘：{row['teaching_summary_cn']}",
                f"- 下一步：{', '.join(row['recommended_next_actions'])}",
            ]
        )
    lines.extend(["", "## 任务池复盘"])
    for row in payload["pool_retros"]:
        lines.extend(
            [
                "",
                f"### {row['pool_id']}",
                f"- 状态：`{row['pool_status']}`",
                f"- 决策：`{row['decision']}`",
                f"- 数据：已测 `{row['simulated_count']}` / 核心过线 `{row['core_passed_count']}` / 已提交 `{row['submitted_count']}` / 高相关归档 `{row['archived_count']}`",
                f"- 复盘：{row.get('teaching_summary_cn') or ''}",
                f"- 下一步：{', '.join(row['recommended_next_actions'])}",
            ]
        )
    lines.extend(
        [
            "",
            "## 下一轮策略",
            "",
            "- 停止同族参数挖掘：遇到 submitted winner + 高相关归档后，不再继续只调窗口/阈值。",
            "- 切换数据集、字段族、中性化或结构：优先做真正不同的信息来源和表达式结构。",
            "- 保留官方真源：所有评分、归档、submit-ready 仍以 WorldQuant BRAIN 回填结果为准。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    candidates = candidate_index()
    results = result_index()
    winners = build_winners(candidates, results)
    archives = build_archives(candidates)
    pools = build_pool_retros()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "winner_count": len(winners),
            "archive_count": len(archives),
            "pool_count": len(pools),
        },
        "winners": winners,
        "correlation_archives": archives,
        "pool_retros": pools,
        "next_strategy": next_strategy(winners, archives, pools),
    }
    write_json(RETRO / "retrospective-ledger.json", payload)
    write_text(RETRO / "retrospective-report.md", build_markdown(payload))
    print(
        json.dumps(
            {
                "winner_count": len(winners),
                "archive_count": len(archives),
                "pool_count": len(pools),
                "json": str(RETRO / "retrospective-ledger.json"),
                "markdown": str(RETRO / "retrospective-report.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
