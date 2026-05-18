#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import pathlib
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
LEDGER = STATE / "ledger"
VISUAL = STATE / "visual"
RETRO = STATE / "retro"

CORE_LEDGER_FILES = [
    LEDGER / "candidate-ledger.json",
    LEDGER / "result-ledger.json",
    LEDGER / "iteration-ledger.json",
]


FIELDS = [
    "candidate_id",
    "human_status",
    "can_submit_now",
    "core_metrics_passed",
    "self_correlation_status",
    "review_lane",
    "task_pool_id",
    "task_pool_objective",
    "target_returns_min",
    "wq_neutralization",
    "stage",
    "template_id",
    "grade",
    "quality_signal_cn",
    "alpha_id",
    "simulation_id",
    "simulation_progress",
    "expression",
    "sharpe",
    "fitness",
    "turnover",
    "returns",
    "drawdown",
    "margin",
    "test_sharpe",
    "train_test_sharpe_gap",
    "test_stability_cn",
    "failed_checks",
    "pending_checks",
    "block_reason_cn",
    "next_action_cn",
    "source_level",
]

ARCHIVE_STATUSES = {"高相关归档", "不可提交归档"}


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def index_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["candidate_id"]): row for row in rows}


def ensure_core_ledgers() -> None:
    if all(path.exists() for path in CORE_LEDGER_FILES):
        return
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_ledgers.py")], cwd=ROOT, check=True)


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def fmt_percent(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f"{value * 100:.0f}%"


def self_correlation_status(result: dict[str, Any] | None) -> str:
    if not result:
        return ""
    if result.get("self_correlation_value") is not None:
        return fmt(result["self_correlation_value"])
    metrics = result.get("metrics", {})
    if metrics.get("self_correlation") is not None:
        return fmt(metrics["self_correlation"])
    checks = result.get("pending_checks", []) + result.get("failed_checks", [])
    if "SELF_CORRELATION" in checks:
        return "PENDING" if "SELF_CORRELATION" in result.get("pending_checks", []) else "FAIL"
    return "PASS_OR_NOT_REPORTED"


def quality_signal_cn(result: dict[str, Any] | None) -> str:
    if not result:
        return "尚未获得官方评级"
    grade = str(result.get("grade") or "").upper()
    if grade == "GOOD":
        return "官方评级 GOOD：质量较强，优先复盘；仍需通过平台检查"
    if grade == "EXCELLENT":
        return "官方评级 EXCELLENT：质量很强，优先进入人工审核池"
    if grade == "SPECTACULAR":
        return "官方评级 SPECTACULAR：质量极强，优先级最高但仍需检查相关性"
    if grade == "AVERAGE":
        return "官方评级 AVERAGE：核心可用但需要看稳定性和相关性"
    if grade == "INFERIOR":
        return "官方评级 INFERIOR：质量偏弱，通常作为失败样本复盘"
    return f"官方评级 {grade or 'UNKNOWN'}：需要人工解释"


def test_stability_cn(result: dict[str, Any] | None) -> str:
    if not result:
        return "尚未测试"
    metrics = result.get("metrics", {})
    test_sharpe = metrics.get("test_sharpe")
    gap = metrics.get("train_test_sharpe_gap")
    if not isinstance(test_sharpe, (int, float)):
        return "缺少测试期 Sharpe"
    if test_sharpe < 0:
        return "测试期转负，泛化弱"
    if isinstance(gap, (int, float)) and gap > 1.0:
        return "测试期为正但 train-test gap 偏大"
    if test_sharpe >= 0.7:
        return "测试期较强，优先复盘"
    return "测试期为正但仍偏弱"


def human_status(
    candidate: dict[str, Any],
    result: dict[str, Any] | None,
    iteration: dict[str, Any] | None,
    pending: dict[str, Any] | None,
) -> str:
    if pending:
        return "官网测试中"
    if not result:
        if candidate.get("status") in {"manual_result_pending", "ready_for_platform_probe"}:
            return "准备官方测试"
        return "未测试/本地候选"
    if result.get("submitted"):
        return "已提交"
    if result.get("archived"):
        return "高相关归档"
    if result.get("non_submittable_archive_reason"):
        return "不可提交归档"
    if result.get("submit_ready"):
        return "可人工提交"
    if result.get("core_metrics_passed") and not result.get("failed_checks") and result.get("pending_checks"):
        return "等自相关/人工闸门"
    if result.get("failed_checks"):
        return "不可提交"
    if iteration and iteration.get("review_lane") == "revise":
        return "需加工"
    return "人工复核"


def next_action_cn(status: str, iteration: dict[str, Any] | None) -> str:
    if status == "已提交":
        return "等待官方积分/OS 检查；作为同族 winner 和生成种子"
    if status == "高相关归档":
        return "不删除；排除提交池，停止同族参数挖掘，切换数据集/中性化/结构"
    if status == "不可提交归档":
        return "不提交；收进归档层，仅用于失败统计和参数修正"
    if status == "可人工提交":
        return "进入人工提交审核；不要自动提交"
    if status == "等自相关/人工闸门":
        return "等待 SELF_CORRELATION；通过后再人工提交审核"
    if status == "不可提交":
        return "淘汰或作为失败样本复盘"
    if status == "需加工":
        return "按失败原因做二阶/三阶加工"
    if status == "官网测试中":
        return "低频续查 simulation 结果"
    if iteration and iteration.get("next_actions"):
        return ", ".join(iteration["next_actions"])
    return "先做本地人工 gate"


def block_reason_cn(status: str, result: dict[str, Any] | None) -> str:
    if status == "已提交":
        return "官网已记录 ACTIVE/dateSubmitted；不再进入提交池"
    if status == "高相关归档":
        alpha = result.get("self_correlated_alpha_id") if result else None
        value = result.get("self_correlation_value") if result else None
        return f"SELF_CORRELATION 失败：{fmt(value)}；相关对象：{alpha or 'UNKNOWN'}"
    if status == "不可提交归档":
        failed = result.get("failed_checks", []) if result else []
        reason = result.get("non_submittable_archive_reason") if result else ""
        if failed:
            return f"不可提交归档：{','.join(failed)}"
        return f"不可提交归档：{reason or '核心指标未过线'}"
    if status == "可人工提交":
        return "已进入 submit-ready 池；等待人工按每日额度提交"
    if status == "官网测试中":
        return "simulation 仍在运行；等待完整结果"
    if not result:
        return "尚未获得官网 simulation 结果"
    failed = result.get("failed_checks", [])
    pending = result.get("pending_checks", [])
    if failed:
        return f"核心指标未过线：{','.join(failed)}"
    if "SELF_CORRELATION" in pending and result.get("core_metrics_passed"):
        return "核心指标已过线，但 SELF_CORRELATION 仍在等待；不能提交"
    if pending:
        return f"仍有平台检查等待中：{','.join(pending)}"
    if not result.get("core_metrics_passed"):
        return "核心指标未过线"
    return "需要人工复核"


def build_rows() -> list[dict[str, str]]:
    ensure_core_ledgers()
    candidates = read_json(LEDGER / "candidate-ledger.json")
    results = index_by_id(read_json(LEDGER / "result-ledger.json"))
    iterations = index_by_id(read_json(LEDGER / "iteration-ledger.json"))
    pending_path = LEDGER / "pending-runs.json"
    pending_runs = index_by_id(read_json(pending_path)) if pending_path.exists() else {}
    rows = []
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        result = results.get(candidate_id)
        iteration = iterations.get(candidate_id)
        pending = pending_runs.get(candidate_id)
        metrics = result.get("metrics", {}) if result else {}
        status = human_status(candidate, result, iteration, pending)
        rows.append(
            {
                "candidate_id": candidate_id,
                "human_status": status,
                "can_submit_now": "是" if result and result.get("submit_ready") else "否",
                "core_metrics_passed": "是" if result and result.get("core_metrics_passed") else "否",
                "self_correlation_status": self_correlation_status(result),
                "review_lane": str(iteration.get("review_lane", "")) if iteration else "",
                "task_pool_id": str(candidate.get("params", {}).get("task_pool_id", "")),
                "task_pool_objective": str(candidate.get("params", {}).get("task_pool_objective", "")),
                "target_returns_min": fmt(candidate.get("params", {}).get("target_returns_min")),
                "wq_neutralization": str(candidate.get("params", {}).get("wq_neutralization", "")),
                "stage": fmt(candidate.get("stage")),
                "template_id": str(candidate.get("template_id", "")),
                "grade": str(result.get("grade", "")) if result else "",
                "quality_signal_cn": quality_signal_cn(result),
                "alpha_id": str(candidate.get("latest_alpha_id") or result.get("alpha_id", "") if result else candidate.get("latest_alpha_id") or ""),
                "simulation_id": str((pending or {}).get("simulation_id") or candidate.get("latest_simulation_id") or (result or {}).get("simulation_id") or ""),
                "simulation_progress": fmt_percent((pending or {}).get("progress")),
                "expression": str(candidate.get("expression", "")),
                "sharpe": fmt(metrics.get("sharpe")),
                "fitness": fmt(metrics.get("fitness")),
                "turnover": fmt(metrics.get("turnover")),
                "returns": fmt(metrics.get("returns")),
                "drawdown": fmt(metrics.get("drawdown")),
                "margin": fmt(metrics.get("margin")),
                "test_sharpe": fmt(metrics.get("test_sharpe")),
                "train_test_sharpe_gap": fmt(metrics.get("train_test_sharpe_gap")),
                "test_stability_cn": test_stability_cn(result),
                "failed_checks": ",".join(result.get("failed_checks", [])) if result else "",
                "pending_checks": ",".join(result.get("pending_checks", [])) if result else "",
                "block_reason_cn": block_reason_cn(status, result),
                "next_action_cn": next_action_cn(status, iteration),
                "source_level": str(result.get("source_level", "")) if result else "",
            }
        )
    return rows


def build_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    submitted_alpha_ids = {
        row["alpha_id"]
        for row in rows
        if row["human_status"] == "已提交" and row["alpha_id"]
    }
    return {
        "rows": len(rows),
        "simulated": sum(1 for row in rows if row["alpha_id"]),
        "submitted": len(submitted_alpha_ids),
        "core_passed": sum(1 for row in rows if row["core_metrics_passed"] == "是"),
        "submit_ready": sum(1 for row in rows if row["can_submit_now"] == "是"),
        "archived": sum(1 for row in rows if row["human_status"] == "高相关归档"),
        "non_submittable_archived": sum(1 for row in rows if row["human_status"] == "不可提交归档"),
        "waiting_checks": sum(1 for row in rows if row["human_status"] == "等自相关/人工闸门"),
        "running": sum(1 for row in rows if row["human_status"] == "官网测试中"),
        "not_simulated": sum(1 for row in rows if row["human_status"] == "未测试/本地候选"),
    }


def write_csv(path: pathlib.Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def status_class(status: str) -> str:
    return {
        "可人工提交": "ready",
        "已提交": "submitted",
        "高相关归档": "archived",
        "不可提交归档": "non-submittable-archived",
        "等自相关/人工闸门": "wait",
        "不可提交": "blocked",
        "需加工": "revise",
        "官网测试中": "running",
        "准备官方测试": "draft",
        "未测试/本地候选": "draft",
    }.get(status, "review")


def render_table_body(rows: list[dict[str, str]]) -> str:
    body = ""
    for row in rows:
        cells = "".join(f"<td>{html.escape(row[field])}</td>" for field in FIELDS)
        body += f"<tr class='{status_class(row['human_status'])}'>{cells}</tr>\n"
    return body


def load_datafield_profile_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    profile_path = LEDGER / "datafield-profile-ledger.json"
    probe_path = LEDGER / "datafield-profile-probe-ledger.json"
    profile_payload = read_json(profile_path) if profile_path.exists() else {
        "summary": {
            "profile_count": 0,
            "mainline_ready_count": 0,
            "exploratory_count": 0,
            "sparse_event_count": 0,
            "avoid_for_now_count": 0,
            "dataset_counts": {},
        },
        "profiles": [],
    }
    probe_payload = read_json(probe_path) if probe_path.exists() else {
        "summary": {"probe_count": 0, "field_count": 0},
        "probes": [],
    }
    return profile_payload, probe_payload


def datafield_profile_cards(profile_payload: dict[str, Any], probe_payload: dict[str, Any]) -> str:
    summary = profile_payload.get("summary", {})
    probe_summary = probe_payload.get("summary", {})
    return "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("profile_count", summary.get("profile_count", 0)),
            ("mainline_ready", summary.get("mainline_ready_count", 0)),
            ("exploratory", summary.get("exploratory_count", 0)),
            ("sparse_event", summary.get("sparse_event_count", 0)),
            ("avoid_for_now", summary.get("avoid_for_now_count", 0)),
            ("profile_probes", probe_summary.get("probe_count", 0)),
            ("profiled_fields", probe_summary.get("field_count", 0)),
        ]
    )


def render_datafield_profile_rows(profile_payload: dict[str, Any]) -> str:
    profiles = profile_payload.get("profiles", [])
    if not isinstance(profiles, list) or not profiles:
        return "<tr><td colspan='7' class='empty'>暂无字段体检账；先运行 build_datafield_profiles.py。</td></tr>"
    rows = ""
    for profile in profiles[:24]:
        coverage = profile.get("coverage", {}).get("raw_coverage") if isinstance(profile.get("coverage"), dict) else None
        rows += (
            "<tr>"
            f"<td>{html.escape(str(profile.get('field_id', '')))}</td>"
            f"<td>{html.escape(str(profile.get('dataset_id', '')))}</td>"
            f"<td>{html.escape(str(profile.get('profile_lane', '')))}</td>"
            f"<td>{fmt(coverage)}</td>"
            f"<td>{html.escape(','.join(profile.get('quality_flags', [])))}</td>"
            f"<td>{html.escape(','.join(profile.get('recommended_templates', [])))}</td>"
            f"<td>{html.escape(str(profile.get('description', ''))[:160])}</td>"
            "</tr>"
        )
    return rows


def write_html(path: pathlib.Path, rows: list[dict[str, str]], summary: dict[str, Any]) -> None:
    submission_pool_path = LEDGER / "submission-pool.json"
    submission_pool = read_json(submission_pool_path) if submission_pool_path.exists() else {
        "policy": {"daily_submission_limit": 4, "rule_cn": ""},
        "summary": {"ready_count": 0, "today_quota_count": 0, "held_for_later_count": 0},
        "today_quota": [],
        "ready_pool": [],
    }
    probe_pool_path = LEDGER / "probe-pool.json"
    probe_pool = read_json(probe_pool_path) if probe_pool_path.exists() else {
        "policy": {"rule_cn": ""},
        "summary": {
            "local_candidate_count": 0,
            "probe_ready_count": 0,
            "official_tested_count": 0,
            "official_core_passed_count": 0,
            "waiting_checks_count": 0,
            "submit_ready_count": 0,
        },
        "ready_pool": [],
    }
    task_pool_path = LEDGER / "task-pool-ledger.json"
    task_pool = read_json(task_pool_path) if task_pool_path.exists() else {"pools": []}
    pool_strategy_path = LEDGER / "pool-strategy.json"
    pool_strategy = read_json(pool_strategy_path) if pool_strategy_path.exists() else {
        "policy": {"rule_cn": ""},
        "summary": {"blocked_for_auto_probe_count": 0, "blocked_for_auto_replenish_count": 0},
        "pools": [],
    }
    correlation_archive_path = LEDGER / "correlation-archive.json"
    correlation_archive = read_json(correlation_archive_path) if correlation_archive_path.exists() else {
        "summary": {"archived_count": 0, "unique_blocking_alpha_count": 0},
        "archived_pool": [],
    }
    non_submittable_archive_path = LEDGER / "non-submittable-archive.json"
    non_submittable_archive = read_json(non_submittable_archive_path) if non_submittable_archive_path.exists() else {
        "policy": {"rule_cn": ""},
        "summary": {"archived_count": 0},
        "archived_pool": [],
    }
    retro_path = RETRO / "retrospective-ledger.json"
    retro_payload = read_json(retro_path) if retro_path.exists() else {
        "summary": {"winner_count": 0, "archive_count": 0, "pool_count": 0},
        "next_strategy": {"blocked_pool_ids": [], "recommended_focus_cn": []},
    }
    datafield_profile_payload, datafield_probe_payload = load_datafield_profile_payload()
    cards = "".join(
        f"<div class='card'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("总候选", summary["rows"]),
            ("已测", summary["simulated"]),
            ("已提交", summary["submitted"]),
            ("官网测试中", summary["running"]),
            ("核心过线", summary["core_passed"]),
            ("高相关归档", summary["archived"]),
            ("不可提交归档", summary.get("non_submittable_archived", 0)),
        ]
    )
    strategy_cards = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("任务池总数", pool_strategy["summary"].get("pool_count", 0)),
            ("停止自动送测", pool_strategy["summary"].get("blocked_for_auto_probe_count", 0)),
            ("停止自动补池", pool_strategy["summary"].get("blocked_for_auto_replenish_count", 0)),
        ]
    )
    retro_cards = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("Submitted winner", retro_payload["summary"].get("winner_count", 0)),
            ("高相关归档", retro_payload["summary"].get("archive_count", 0)),
            ("任务池复盘", retro_payload["summary"].get("pool_count", 0)),
            ("停止/改线池", len(retro_payload.get("next_strategy", {}).get("blocked_pool_ids", []))),
        ]
    )
    retro_focus = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in retro_payload.get("next_strategy", {}).get("recommended_focus_cn", [])[:4]
    )
    retro_report_rel = "../retro/retrospective-report.md"
    datafield_profile_metric_cards = datafield_profile_cards(datafield_profile_payload, datafield_probe_payload)
    datafield_profile_rows = render_datafield_profile_rows(datafield_profile_payload)
    quota_cards = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("窗口提交额度", submission_pool["policy"].get("daily_submission_limit", 4)),
            ("额度窗口", submission_pool["summary"].get("quota_date", "")),
            ("窗口已提交", submission_pool["summary"].get("submitted_window_count", submission_pool["summary"].get("submitted_today_count", 0))),
            ("窗口剩余额度", submission_pool["summary"].get("remaining_submission_quota", 0)),
            ("已请求占用", submission_pool["summary"].get("reserved_submit_request_count", 0)),
            ("可用提交额度", submission_pool["summary"].get("available_submission_quota", submission_pool["summary"].get("remaining_submission_quota", 0))),
            ("提交门", "已锁住" if submission_pool["summary"].get("submission_gate_locked") else "开放"),
            ("Submit-ready 池", submission_pool["summary"].get("ready_count", 0)),
            ("本窗口优先提交", submission_pool["summary"].get("today_quota_count", 0)),
            ("留待以后", submission_pool["summary"].get("held_for_later_count", 0)),
        ]
    )
    probe_cards = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("本地候选", probe_pool["summary"].get("local_candidate_count", 0)),
            ("准备官方测试", probe_pool["summary"].get("probe_ready_count", 0)),
            ("已获官网结果", probe_pool["summary"].get("official_tested_count", 0)),
            ("官网核心过线", probe_pool["summary"].get("official_core_passed_count", 0)),
            ("核心过线率", fmt(probe_pool["summary"].get("official_core_pass_rate", 0))),
            ("等待自相关", probe_pool["summary"].get("waiting_checks_count", 0)),
            ("最终可提交", probe_pool["summary"].get("submit_ready_count", 0)),
            ("可提交率", fmt(probe_pool["summary"].get("submit_ready_rate", 0))),
            ("已提交率", fmt(probe_pool["summary"].get("submitted_rate", 0))),
            ("高相关归档", probe_pool["summary"].get("archived_count", 0)),
            ("不可提交归档", probe_pool["summary"].get("non_submittable_archived_count", summary.get("non_submittable_archived", 0))),
        ]
    )
    status_order = [
        ("已提交", "submitted"),
        ("可人工提交", "ready"),
        ("高相关归档", "archived"),
        ("不可提交归档", "non-submittable-archived"),
        ("等自相关/人工闸门", "wait"),
        ("官网测试中", "running"),
        ("准备官方测试", "draft"),
        ("不可提交", "blocked"),
        ("未测试/本地候选", "draft"),
    ]
    status_counts = {label: sum(1 for row in rows if row["human_status"] == label) for label, _ in status_order}
    max_status = max(status_counts.values()) if status_counts else 1
    status_bars = "".join(
        f"<div class='bar-row'><span>{html.escape(label)}</span><div class='bar'><i class='{klass}' style='width:{(count / max(max_status, 1)) * 100:.0f}%'></i></div><b>{count}</b></div>"
        for label, klass in status_order
        for count in [status_counts[label]]
    )
    top_quota = submission_pool.get("today_quota", [])
    if top_quota:
        quota_rows = "".join(
            "<tr>"
            f"<td>#{item.get('submission_rank')}</td>"
            f"<td>{html.escape(str(item.get('candidate_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('alpha_id', '')))}</td>"
            f"<td>{fmt(item.get('fitness'))}</td>"
            f"<td>{fmt(item.get('sharpe'))}</td>"
            f"<td>{fmt(item.get('test_sharpe'))}</td>"
            f"<td>{html.escape(str(item.get('expression', '')))}</td>"
            "</tr>"
            for item in top_quota
        )
    else:
        quota_rows = "<tr><td colspan='7' class='empty'>当前没有 submit-ready。核心过线但自相关 pending 的样本先留在等待池。</td></tr>"
    archived_rows = ""
    for item in correlation_archive.get("archived_pool", []):
        archived_rows += (
            "<tr>"
            f"<td>{html.escape(str(item.get('candidate_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('alpha_id', '')))}</td>"
            f"<td>{fmt(item.get('self_correlation_value'))}</td>"
            f"<td>{html.escape(str(item.get('self_correlated_alpha_id', '')))}</td>"
            f"<td>{fmt(item.get('fitness'))}</td>"
            f"<td>{fmt(item.get('sharpe'))}</td>"
            f"<td>{fmt(item.get('test_sharpe'))}</td>"
            f"<td>{html.escape(str(item.get('expression', '')))}</td>"
            "</tr>"
        )
    if not archived_rows:
        archived_rows = "<tr><td colspan='8' class='empty'>当前没有高相关归档样本。</td></tr>"
    non_submittable_rows = ""
    for item in non_submittable_archive.get("archived_pool", []):
        non_submittable_rows += (
            "<tr>"
            f"<td>{html.escape(str(item.get('candidate_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('alpha_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('archive_reason', '')))}</td>"
            f"<td>{html.escape(str(item.get('grade', '')))}</td>"
            f"<td>{fmt(item.get('fitness'))}</td>"
            f"<td>{fmt(item.get('sharpe'))}</td>"
            f"<td>{fmt(item.get('test_sharpe'))}</td>"
            f"<td>{html.escape(','.join(item.get('failed_checks', [])))}</td>"
            f"<td>{html.escape(str(item.get('expression', '')))}</td>"
            "</tr>"
        )
    if not non_submittable_rows:
        non_submittable_rows = "<tr><td colspan='9' class='empty'>当前没有不可提交归档样本。</td></tr>"
    strategy_rows = ""
    for item in pool_strategy.get("pools", [])[:12]:
        strategy_rows += (
            f"<tr class='{html.escape(str(item.get('pool_status', '')))}'>"
            f"<td>{html.escape(str(item.get('pool_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('pool_status', '')))}</td>"
            f"<td>{'是' if item.get('blocked_for_auto_probe') else '否'}</td>"
            f"<td>{'是' if item.get('blocked_for_auto_replenish') else '否'}</td>"
            f"<td>{item.get('submitted_count', 0)}</td>"
            f"<td>{item.get('archived_count', 0)}</td>"
            f"<td>{item.get('simulated_count', 0)}</td>"
            f"<td>{item.get('core_passed_count', 0)}</td>"
            f"<td>{fmt(item.get('core_pass_rate'))}</td>"
            f"<td>{item.get('waiting_checks_count', 0)}</td>"
            f"<td>{html.escape(str(item.get('recommended_action_cn', '')))}</td>"
            "</tr>"
        )
    if not strategy_rows:
        strategy_rows = "<tr><td colspan='10' class='empty'>暂无任务池策略；先运行 build_ledgers.py。</td></tr>"
    pool_rows = ""
    for pool in task_pool.get("pools", [])[:12]:
        pool_rows += (
            "<tr>"
            f"<td>{html.escape(str(pool.get('pool_id', '')))}</td>"
            f"<td>{html.escape(str(pool.get('pool_source', '')))}</td>"
            f"<td>{html.escape(str(pool.get('task_pool_priority', '')))}</td>"
            f"<td>{html.escape(str(pool.get('stage', '')))}</td>"
            f"<td>{html.escape(str(pool.get('dataset_id', '')))}</td>"
            f"<td>{html.escape(str(pool.get('neutralization', '')))}</td>"
            f"<td>{pool.get('candidate_count', 0)}</td>"
            f"<td>{pool.get('simulated_count', 0)}</td>"
            f"<td>{pool.get('core_passed_count', 0)}</td>"
            f"<td>{pool.get('submit_ready_count', 0)}</td>"
            f"<td>{pool.get('submitted_count', 0)}</td>"
            f"<td>{pool.get('archived_count', 0)}</td>"
            f"<td>{fmt(pool.get('core_pass_rate'))}</td>"
            f"<td>{fmt(pool.get('submit_ready_rate'))}</td>"
            f"<td>{fmt(pool.get('submitted_rate'))}</td>"
            f"<td>{html.escape(str(pool.get('best_grade', '')))}</td>"
            f"<td>{fmt(pool.get('best_fitness'))}</td>"
            f"<td>{fmt(pool.get('best_sharpe'))}</td>"
            f"<td>{fmt(pool.get('best_test_sharpe'))}</td>"
            f"<td>{html.escape(str(pool.get('recommended_action_cn', '')))}</td>"
            "</tr>"
        )
    if not pool_rows:
        pool_rows = "<tr><td colspan='20' class='empty'>暂无任务池批次统计；先运行 build_ledgers.py。</td></tr>"
    header = "".join(f"<th>{html.escape(field)}</th>" for field in FIELDS)
    working_rows = [row for row in rows if row["human_status"] not in ARCHIVE_STATUSES]
    working_body = render_table_body(working_rows)
    if not working_body:
        working_body = f"<tr><td colspan='{len(FIELDS)}' class='empty'>当前没有可行动项；归档样本已移到下方归档区。</td></tr>"
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Factor Factory Dashboard</title>
  <style>
    :root {{ --bg:#f6f7f9; --ink:#172033; --muted:#697386; --line:#dfe4ec; --panel:#ffffff; --green:#0f9f6e; --amber:#d98c00; --red:#d94b4b; --blue:#2673d9; --violet:#6457d8; --teal:#0891b2; --slate:#64748b; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: var(--bg); color: var(--ink); }}
    main {{ padding: 28px; max-width: 1680px; margin: 0 auto; }}
    h1 {{ margin: 0 0 4px; font-size: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 17px; }}
    .sub {{ color: var(--muted); margin-bottom: 22px; }}
    .cards {{ display: grid; grid-template-columns: repeat(7, minmax(120px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .card span {{ display: block; color: #6b7280; font-size: 13px; }}
    .card strong {{ display: block; font-size: 26px; margin-top: 6px; }}
    .grid {{ display: grid; grid-template-columns: 1.15fr 1fr; gap: 14px; margin-bottom: 16px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }}
    .metric {{ border: 1px solid #edf0f5; border-radius: 8px; padding: 12px; background: #fbfcfe; }}
    .metric span {{ display:block; color: var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:4px; font-size:22px; }}
    .bar-row {{ display:grid; grid-template-columns: 120px 1fr 32px; align-items:center; gap:10px; margin:10px 0; font-size:13px; }}
    .bar {{ height:10px; background:#eef1f5; border-radius:999px; overflow:hidden; }}
    .bar i {{ display:block; height:100%; min-width:3px; border-radius:999px; }}
    .bar i.submitted {{ background:var(--teal); }} .bar i.archived {{ background:var(--slate); }} .bar i.non-submittable-archived {{ background:var(--red); }} .bar i.ready {{ background:var(--green); }} .bar i.wait {{ background:var(--amber); }} .bar i.running {{ background:var(--blue); }} .bar i.blocked {{ background:var(--red); }} .bar i.draft {{ background:#9ca3af; }}
    .quota-table {{ margin-top:10px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); }}
    th, td {{ padding: 10px; border-bottom: 1px solid #edf0f5; text-align: left; font-size: 13px; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #f3f4f6; z-index: 1; }}
    tr.wait td:first-child {{ border-left: 5px solid var(--amber); }}
    tr.ready td:first-child {{ border-left: 5px solid var(--green); }}
    tr.submitted td:first-child {{ border-left: 5px solid var(--teal); }}
    tr.archived td:first-child {{ border-left: 5px solid var(--slate); }}
    tr.non-submittable-archived td:first-child {{ border-left: 5px solid var(--red); }}
    tr.blocked td:first-child {{ border-left: 5px solid var(--red); }}
    tr.revise td:first-child {{ border-left: 5px solid var(--violet); }}
    tr.draft td:first-child {{ border-left: 5px solid #9ca3af; }}
    tr.running td:first-child {{ border-left: 5px solid var(--blue); }}
    tr.early_stopped_winner_submitted td:first-child, tr.pause_after_winner_submitted td:first-child, tr.demote_high_correlation td:first-child, tr.low_yield_revise td:first-child {{ border-left: 5px solid var(--red); }}
    tr.wait_official_checks td:first-child {{ border-left: 5px solid var(--amber); }}
    tr.active td:first-child {{ border-left: 5px solid var(--green); }}
    td:nth-child(12), .quota-table td:nth-child(7) {{ max-width: 420px; overflow-wrap: anywhere; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 900px) {{ main {{ padding:16px; }} .cards {{ grid-template-columns: repeat(2, 1fr); }} .grid {{ grid-template-columns:1fr; }} .metrics {{ grid-template-columns: repeat(2, 1fr); }} }}
  </style>
</head>
<body>
  <main>
    <h1>Factor Factory Dashboard</h1>
    <div class="sub">官网结果是唯一真源；本页是由本地候选账、结果账、迭代账、pending 账和提交池派生的人类驾驶舱。</div>
    <div class="cards">{cards}</div>
    <div class="grid">
      <section class="panel">
        <h2>本地筛选漏斗</h2>
        <div class="metrics">{probe_cards}</div>
        <p class="sub">{html.escape(str(probe_pool['policy'].get('rule_cn', '')))}</p>
      </section>
      <section class="panel">
        <h2>提交池</h2>
        <div class="metrics">{quota_cards}</div>
        <p class="sub">{html.escape(str(submission_pool['policy'].get('rule_cn', '')))}</p>
      </section>
    </div>
    <div class="grid">
      <section class="panel">
        <h2>状态分布</h2>
        {status_bars}
      </section>
      <section class="panel">
        <h2>任务池策略</h2>
        <div class="metrics">{strategy_cards}</div>
        <p class="sub">{html.escape(str(pool_strategy.get('policy', {}).get('rule_cn', '')))}</p>
      </section>
    </div>
    <section class="panel">
      <h2>字段体检 gate</h2>
      <div class="metrics">{datafield_profile_metric_cards}</div>
      <p class="sub">datafield profile gate 是官方公开课沉淀出的前置闸门：先看字段覆盖率、非零覆盖、更新频率、极端值、长期中位数和缩放分布，再决定字段是否进入 task-pool。Profile probes 是诊断表达式，不进入提交池。</p>
      <table>
        <thead><tr><th>field</th><th>dataset</th><th>lane</th><th>coverage</th><th>flags</th><th>recommended templates</th><th>description</th></tr></thead>
        <tbody>{datafield_profile_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>复盘报告</h2>
      <div class="metrics">{retro_cards}</div>
      <p class="sub"><a href="{html.escape(retro_report_rel)}">打开 retrospective-report.md</a>。复盘只解释官网真源结果，不参与自动提交。</p>
      <ul>{retro_focus}</ul>
    </section>
    <section class="panel">
      <h2>今日优先人工提交</h2>
      <table class="quota-table">
        <thead><tr><th>rank</th><th>candidate_id</th><th>alpha_id</th><th>fitness</th><th>sharpe</th><th>test_sharpe</th><th>expression</th></tr></thead>
        <tbody>{quota_rows}</tbody>
      </table>
    </section>
    <section class="panel" style="margin-top:16px;">
      <h2>高相关归档</h2>
      <p class="sub">{html.escape(str(correlation_archive.get('policy', {}).get('rule_cn', '')))}</p>
      <details>
      <summary>查看全部高相关归档样本</summary>
      <table>
        <thead><tr><th>candidate_id</th><th>alpha_id</th><th>self corr</th><th>blocked by</th><th>fitness</th><th>sharpe</th><th>test sharpe</th><th>expression</th></tr></thead>
        <tbody>{archived_rows}</tbody>
      </table>
      </details>
    </section>
    <section class="panel" style="margin-top:16px;">
      <h2>不可提交归档</h2>
      <p class="sub">{html.escape(str(non_submittable_archive.get('policy', {}).get('rule_cn', '')))}</p>
      <details>
      <summary>查看全部不可提交归档样本</summary>
      <table>
        <thead><tr><th>candidate_id</th><th>alpha_id</th><th>reason</th><th>grade</th><th>fitness</th><th>sharpe</th><th>test sharpe</th><th>failed checks</th><th>expression</th></tr></thead>
        <tbody>{non_submittable_rows}</tbody>
      </table>
      </details>
    </section>
    <section class="panel" style="margin-top:16px;">
      <h2>任务池早停与切换</h2>
      <table>
        <thead><tr><th>pool_id</th><th>status</th><th>禁自动送测</th><th>禁自动补池</th><th>已提交</th><th>归档</th><th>已测</th><th>核心过线</th><th>核心过线率</th><th>等待检查</th><th>策略动作</th></tr></thead>
        <tbody>{strategy_rows}</tbody>
      </table>
    </section>
    <section class="panel" style="margin-top:16px;">
      <h2>任务池批次表现</h2>
      <table>
        <thead><tr><th>pool_id</th><th>pool_source</th><th>priority</th><th>stage</th><th>dataset</th><th>neutralization</th><th>候选</th><th>已测</th><th>核心过线</th><th>可提交</th><th>已提交</th><th>归档</th><th>核心过线率</th><th>可提交率</th><th>已提交率</th><th>best grade</th><th>best fitness</th><th>best sharpe</th><th>best test sharpe</th><th>下一步</th></tr></thead>
        <tbody>{pool_rows}</tbody>
      </table>
    </section>
    <section class="panel" style="margin-top:16px;">
      <h2>工作台账（默认隐藏归档）</h2>
      <p class="sub">这里默认只显示仍可行动的候选、待查、可提交和已提交记录；高相关和不可提交样本已移到上方归档区，CSV 仍保留全量证据。</p>
      <table>
        <thead><tr>{header}</tr></thead>
        <tbody>{working_body}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")


def main() -> int:
    rows = build_rows()
    summary = build_summary(rows)
    csv_path = VISUAL / "factor-factory-dashboard.csv"
    html_path = VISUAL / "factor-factory-dashboard.html"
    summary_path = VISUAL / "factor-factory-summary.json"
    write_csv(csv_path, rows)
    write_html(html_path, rows, summary)
    write_json(summary_path, summary)
    print(json.dumps({"rows": len(rows), "summary": summary, "csv": str(csv_path), "html": str(html_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
