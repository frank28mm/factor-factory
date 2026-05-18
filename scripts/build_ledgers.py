#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
QUEUE = STATE / "queue"
LEDGER = STATE / "ledger"
CONFIG = ROOT / "config"
DEFAULT_QUOTA_TIMEZONE = "Asia/Shanghai"
SUBMIT_ACCEPTED_CLASSIFICATIONS = {"accepted", "submitted", "already_submitted"}
CURRENT_GENERATION_RULE_VERSION = "v16-analyst4-fundamental6-pv1-wq-rotation"
CURRENT_PROFILE_POOL_ID = "profile-stage2-field-blend-v15"
CURRENT_VARIANT_FAMILY = "profile_stage3_pv_gated_blend"
CURRENT_ANALYST_FIELDS = {"est_eps", "est_netprofit", "est_ptp", "est_sales", "est_capex"}
CURRENT_FUNDAMENTAL_FIELDS = {"inventory_turnover", "sales", "operating_income"}
CURRENT_PV_GATE_FIELDS = {"volume", "returns"}
CURRENT_NEUTRALIZATIONS = {"SUBINDUSTRY", "INDUSTRY", "MARKET"}
CURRENT_DECAYS = {2, 4, 6}
CURRENT_TRUNCATIONS = {0.08, 0.12}


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: pathlib.Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidate_files() -> list[pathlib.Path]:
    return sorted(QUEUE.glob("cand-*.json"))


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


def check_by_name(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    for check in result.get("checks", []):
        if isinstance(check, dict) and str(check.get("name", "")).upper() == name:
            return check
    return None


def core_metrics_passed(result: dict[str, Any], scoring: dict[str, Any]) -> bool:
    metrics = result.get("metrics", {})
    gates = scoring["official_platform_gates"]["delay_1"]
    turnover_range = scoring["official_platform_gates"]["turnover_range"]
    self_corr_max = scoring["official_platform_gates"]["self_correlation"]["max"]

    sharpe = metrics.get("sharpe")
    fitness = metrics.get("fitness")
    turnover = metrics.get("turnover")
    if not isinstance(sharpe, (int, float)) or not isinstance(fitness, (int, float)) or not isinstance(turnover, (int, float)):
        return False
    if sharpe < gates["sharpe_min"] or fitness < gates["fitness_min"]:
        return False
    if not (turnover_range["min"] <= turnover <= turnover_range["max"]):
        return False
    self_correlation = metrics.get("self_correlation")
    if isinstance(self_correlation, (int, float)) and self_correlation >= self_corr_max:
        return False
    return True


def is_alpha_submitted(result: dict[str, Any]) -> bool:
    if result.get("date_submitted"):
        return True
    return str(result.get("alpha_status", "")).upper() == "ACTIVE"


def submitted_alpha_ids(result_ledger: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("alpha_id"))
        for row in result_ledger
        if row.get("alpha_id") and is_alpha_submitted(row)
    }


def submitted_alpha_ids_on_quota_date(
    result_ledger: list[dict[str, Any]],
    quota_date: str,
    timezone_name: str | None = None,
) -> set[str]:
    return {
        str(row.get("alpha_id"))
        for row in result_ledger
        if row.get("alpha_id") and submitted_on_quota_date(row, quota_date, timezone_name)
    }


def terminal_unsubmitted_alpha_ids(result_ledger: list[dict[str, Any]]) -> set[str]:
    terminal = set()
    for row in result_ledger:
        alpha_id = str(row.get("alpha_id") or "")
        if not alpha_id or is_alpha_submitted(row):
            continue
        if row.get("archived") or row.get("non_submittable_archive_reason") or row.get("failed_checks"):
            terminal.add(alpha_id)
    return terminal


def self_correlation_failed(result: dict[str, Any]) -> bool:
    return "SELF_CORRELATION" in check_names(result, "FAIL")


def self_correlation_match(result: dict[str, Any]) -> dict[str, Any]:
    match = check_by_name(result, "SELF_CORRELATION_MATCH") or {}
    self_check = check_by_name(result, "SELF_CORRELATION") or {}
    metrics = result.get("metrics", {})
    return {
        "self_correlation_value": self_check.get("value", metrics.get("self_correlation")),
        "self_correlation_limit": self_check.get("limit"),
        "self_correlated_alpha_id": match.get("alpha_id"),
        "self_correlated_sharpe": match.get("sharpe"),
        "self_correlated_fitness": match.get("fitness"),
    }


def archive_reason(result: dict[str, Any]) -> str | None:
    if self_correlation_failed(result):
        return "self_correlation_fail"
    return None


def non_submittable_archive_reason(result: dict[str, Any], scoring: dict[str, Any]) -> str | None:
    if is_alpha_submitted(result) or archive_reason(result):
        return None
    failed = check_names(result, "FAIL")
    if failed:
        return "official_failed_checks"
    if result.get("metrics") and not core_metrics_passed(result, scoring):
        return "core_metrics_failed"
    return None


def waiting_official_checks(result: dict[str, Any]) -> bool:
    return bool(
        result.get("core_metrics_passed")
        and not result.get("failed_checks")
        and result.get("pending_checks")
        and not result.get("submitted")
        and not result.get("archived")
    )


def is_submit_ready(result: dict[str, Any], scoring: dict[str, Any]) -> bool:
    if is_alpha_submitted(result):
        return False
    if not core_metrics_passed(result, scoring):
        return False
    if check_names(result, "FAIL"):
        return False
    return not check_names(result, "PENDING")


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


def task_pool_pre_probe_gate_failures(
    params: dict[str, Any],
    results_by_id: dict[str, dict[str, Any]] | None,
    scoring: dict[str, Any] | None,
) -> list[str]:
    if not params.get("task_pool_id"):
        return []
    if params.get("pre_probe_gate_passed") is False:
        return ["pre_probe_gate_marked_false"]
    seed_candidate_id = params.get("seed_candidate_id")
    if not seed_candidate_id:
        return ["missing_seed_candidate_id"]
    if not scoring:
        return ["missing_scoring_config"]
    return official_core_gate_failures((results_by_id or {}).get(str(seed_candidate_id)), scoring)


def probe_rule_gate_failures(candidate: dict[str, Any]) -> list[str]:
    params = candidate.get("params", {})
    failures = []
    if params.get("generation_rule_version") != CURRENT_GENERATION_RULE_VERSION:
        failures.append("legacy_candidate_rule_version")
    if params.get("task_pool_id") != CURRENT_PROFILE_POOL_ID:
        failures.append("legacy_or_disabled_task_pool")
    if params.get("task_pool_variant_family") != CURRENT_VARIANT_FAMILY:
        failures.append("not_stage3_pv_gated_current_family")
    if params.get("analyst_field") not in CURRENT_ANALYST_FIELDS:
        failures.append("analyst_field_not_in_analyst4")
    if params.get("fundamental_field") not in CURRENT_FUNDAMENTAL_FIELDS:
        failures.append("fundamental_field_not_in_current_fundamental6_subset")
    if params.get("pv_gate_field") not in CURRENT_PV_GATE_FIELDS:
        failures.append("missing_or_invalid_pv1_gate")
    if str(params.get("wq_neutralization", "")).upper() not in CURRENT_NEUTRALIZATIONS:
        failures.append("invalid_wq_neutralization_rotation")
    try:
        if int(params.get("wq_decay")) not in CURRENT_DECAYS:
            failures.append("invalid_wq_decay_rotation")
    except (TypeError, ValueError):
        failures.append("invalid_wq_decay_rotation")
    try:
        if round(float(params.get("wq_truncation")), 2) not in CURRENT_TRUNCATIONS:
            failures.append("invalid_wq_truncation_rotation")
    except (TypeError, ValueError):
        failures.append("invalid_wq_truncation_rotation")
    return failures


def failure_reasons(result: dict[str, Any], scoring: dict[str, Any]) -> list[str]:
    metrics = result.get("metrics", {})
    gates = scoring["official_platform_gates"]["delay_1"]
    turnover_range = scoring["official_platform_gates"]["turnover_range"]
    reasons = []
    if metrics.get("sharpe") is not None and metrics["sharpe"] < gates["sharpe_min"]:
        reasons.append("low_sharpe")
    if metrics.get("fitness") is not None and metrics["fitness"] < gates["fitness_min"]:
        reasons.append("low_fitness")
    if metrics.get("turnover") is not None and not (turnover_range["min"] <= metrics["turnover"] <= turnover_range["max"]):
        reasons.append("turnover_out_of_range")
    if metrics.get("test_sharpe") is not None and metrics["test_sharpe"] < 0:
        reasons.append("weak_test_period")
    if metrics.get("train_test_sharpe_gap") is not None and metrics["train_test_sharpe_gap"] > 1.0:
        reasons.append("train_test_gap_too_wide")
    for failed in check_names(result, "FAIL"):
        normalized = failed.lower()
        if normalized not in reasons:
            reasons.append(normalized)
    return reasons


def optimization_focus(reasons: list[str]) -> list[str]:
    focus = []
    reason_set = set(reasons)
    if "low_sharpe" in reason_set or "low_sharpe" in reason_set:
        focus.append("improve_sharpe")
    if "low_fitness" in reason_set:
        focus.append("improve_fitness")
    if "turnover_out_of_range" in reason_set:
        focus.append("adjust_turnover")
    if "weak_test_period" in reason_set or "train_test_gap_too_wide" in reason_set:
        focus.append("improve_test_stability")
    if not focus:
        focus.append("extract_success_pattern")
    return focus


def source_level(result: dict[str, Any]) -> str:
    if result.get("import_source") == "live_alpha_detail":
        return "level_0_official_alpha_detail"
    if result.get("import_source") == "live_simulation_audit":
        return "level_0_official_simulation_result"
    return "level_1_manual_or_derived_import"


def review_lane(result: dict[str, Any], submit_ready: bool, reasons: list[str], scoring: dict[str, Any]) -> str:
    if result and is_alpha_submitted(result):
        return "submitted_or_active"
    if result and archive_reason(result):
        return "archive_high_correlation"
    if result and non_submittable_archive_reason(result, scoring):
        return "archive_not_submittable"
    if submit_ready:
        return "ready_for_submit_review"
    if not result:
        return "not_simulated"
    if core_metrics_passed(result, scoring) and not check_names(result, "FAIL") and check_names(result, "PENDING"):
        return "manual_gate_wait_checks"
    if reasons:
        return "revise"
    return "manual_review"


def success_retro_required(result: dict[str, Any], submit_ready: bool) -> bool:
    metrics = result.get("metrics", {})
    if archive_reason(result):
        return True
    if submit_ready:
        return True
    if metrics.get("sharpe") is not None and metrics["sharpe"] >= 1.15:
        return True
    if metrics.get("fitness") is not None and metrics["fitness"] >= 0.9:
        return True
    if metrics.get("test_sharpe") is not None and metrics["test_sharpe"] > 0:
        return True
    return False


def next_actions(lane: str, reasons: list[str], stage: int) -> list[str]:
    if lane == "submitted_or_active":
        return ["track_official_points", "post_submit_retro", "use_as_generation_seed"]
    if lane == "archive_high_correlation":
        return ["archive_not_delete", "stop_same_family_generation", "switch_dataset_or_neutralization_family"]
    if lane == "archive_not_submittable":
        return ["archive_not_delete", "exclude_from_submission_pool", "use_only_for_failure_statistics"]
    if lane == "ready_for_submit_review":
        return ["manual_submit_gate", "use_as_generation_seed", "increase_template_family_weight"]
    if lane == "manual_gate_wait_checks":
        return [
            "wait_self_correlation_check",
            "manual_submit_gate_after_checks",
            "use_as_generation_seed",
            "test_stability_variant",
        ]
    actions = []
    reason_set = set(reasons)
    if "low_sharpe" in reason_set or "low_fitness" in reason_set:
        actions.append("try_stage_2_group_operator")
    if "weak_test_period" in reason_set or "train_test_gap_too_wide" in reason_set:
        actions.append("test_stability_variant")
    if stage < 3:
        actions.append(f"promote_stage_{stage + 1}_variant")
    if not actions:
        actions.append("manual_review_before_next_run")
    return actions


def metric_value(result: dict[str, Any], name: str) -> float:
    value = result.get("metrics", {}).get(name)
    return float(value) if isinstance(value, (int, float)) else 0.0


def submission_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    return (
        metric_value(row, "fitness"),
        metric_value(row, "sharpe"),
        metric_value(row, "test_sharpe"),
        metric_value(row, "returns"),
        -metric_value(row, "drawdown"),
        metric_value(row, "margin"),
    )


def result_by_candidate_id(result_ledger: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["candidate_id"]: row for row in result_ledger}


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def quota_timezone(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_QUOTA_TIMEZONE)


def official_quota_date(quota_now: str | None = None, timezone_name: str | None = None) -> str:
    now = parse_datetime(quota_now) if quota_now else datetime.now(timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    official_now = now.astimezone(quota_timezone(timezone_name))
    quota_day = official_now.date()
    if official_now.time() < time(12, 0):
        quota_day = quota_day - timedelta(days=1)
    return quota_day.isoformat()


def submitted_on_quota_date(result: dict[str, Any], quota_date: str, timezone_name: str | None = None) -> bool:
    if not result.get("submitted"):
        return False
    submitted_at = result.get("date_submitted") or result.get("dateSubmitted")
    if not isinstance(submitted_at, str) or not submitted_at:
        return False
    parsed = parse_datetime(submitted_at)
    if parsed is None:
        return submitted_at[:10] == quota_date
    zone = quota_timezone(timezone_name)
    if parsed.tzinfo is None:
        official_submitted_at = parsed.replace(tzinfo=zone)
    else:
        official_submitted_at = parsed.astimezone(zone)
    submitted_quota_day = official_submitted_at.date()
    if official_submitted_at.time() < time(12, 0):
        submitted_quota_day = submitted_quota_day - timedelta(days=1)
    return submitted_quota_day.isoformat() == quota_date


def accepted_submit_audit_rows() -> list[dict[str, Any]]:
    rows = []
    for path in sorted((STATE / "audit").glob("*-alpha-submit.json")):
        try:
            event = read_json(path)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if str(event.get("event_type") or "") != "alpha_submit":
            continue
        if str(event.get("classification") or "").lower() not in SUBMIT_ACCEPTED_CLASSIFICATIONS:
            continue
        rows.append(
            {
                "candidate_id": event.get("candidate_id"),
                "alpha_id": event.get("alpha_id"),
                "date_submitted": event.get("created_at"),
                "submitted": True,
                "source": "local_alpha_submit_audit",
                "audit_file": str(path),
            }
        )
    return rows


def accepted_submit_audit_count(
    result_ledger: list[dict[str, Any]],
    quota_date: str,
    timezone_name: str | None = None,
) -> int:
    return len(accepted_submit_alpha_ids(result_ledger, quota_date, timezone_name))


def accepted_submit_alpha_ids(
    result_ledger: list[dict[str, Any]],
    quota_date: str,
    timezone_name: str | None = None,
    now: datetime | None = None,
    reservation_minutes: int | None = None,
) -> set[str]:
    known_alpha_ids = {str(row.get("alpha_id")) for row in result_ledger if row.get("alpha_id")}
    officially_submitted_alpha_ids = submitted_alpha_ids_on_quota_date(result_ledger, quota_date, timezone_name)
    terminal_unsubmitted = terminal_unsubmitted_alpha_ids(result_ledger)
    alpha_ids = set()
    for row in accepted_submit_audit_rows():
        alpha_id = str(row.get("alpha_id") or "")
        if alpha_id not in known_alpha_ids:
            continue
        if alpha_id in officially_submitted_alpha_ids:
            continue
        if alpha_id in terminal_unsubmitted:
            continue
        if reservation_minutes is not None:
            submitted_at = parse_datetime(str(row.get("date_submitted") or ""))
            if submitted_at is None:
                continue
            reference_now = now or datetime.now(timezone.utc)
            if reference_now.tzinfo is None:
                reference_now = reference_now.replace(tzinfo=timezone.utc)
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=timezone.utc)
            if (reference_now.astimezone(timezone.utc) - submitted_at.astimezone(timezone.utc)).total_seconds() > reservation_minutes * 60:
                continue
        if submitted_on_quota_date(row, quota_date, timezone_name):
            alpha_ids.add(alpha_id)
    return alpha_ids


def candidate_dataset_id(candidate: dict[str, Any]) -> str:
    params = candidate.get("params", {})
    for key in ("dataset_id", "dataset", "data_category"):
        value = params.get(key)
        if value:
            return str(value)
    return "mixed"


def result_neutralization(result: dict[str, Any] | None) -> str:
    if not result:
        return "UNKNOWN"
    settings = result.get("simulation_settings", {})
    neutralization = settings.get("neutralization")
    if neutralization:
        return str(neutralization).upper()
    return "UNKNOWN"


def slug(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "-")
        .replace("_", "-")
        .replace("/", "-")
    )


def pool_recommended_action(
    simulated_count: int,
    core_passed_count: int,
    submit_ready_count: int,
    waiting_checks_count: int,
    submitted_count: int = 0,
    archived_count: int = 0,
) -> str:
    if submitted_count and archived_count:
        return "已有提交 winner 且出现高相关归档；停止同族参数挖掘，切换数据集/中性化/结构"
    if submitted_count:
        return "已有提交 winner；暂停同族继续送测，等待积分和复盘后再开新族群"
    if submit_ready_count:
        return "进入人工 submit gate；按每日额度挑最高质量"
    if archived_count and core_passed_count and archived_count >= core_passed_count:
        return "该族群已被自相关归档；停止同族参数挖掘，切换数据集/中性化/结构"
    if waiting_checks_count:
        return "继续复查自相关；通过后进入人工 submit gate"
    if simulated_count == 0:
        return "先跑小批量 simulation 建立基准"
    if core_passed_count == 0:
        return "降低权重；换数据集或中性化后再小批量验证"
    return "保留为候选池；继续补充结果样本"


def safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def candidate_pool_key(candidate: dict[str, Any], result: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    params = candidate.get("params", {})
    configured_pool_id = params.get("task_pool_id")
    if configured_pool_id:
        pool_id = str(configured_pool_id)
        stage = int(candidate.get("stage", 0))
        dataset_id = candidate_dataset_id(candidate)
        neutralization = result_neutralization(result)
        return pool_id, {
            "pool_id": pool_id,
            "pool_source": "configured_task_pool",
            "task_pool_priority": str(params.get("task_pool_priority", "")),
            "stage": stage,
            "dataset_id": dataset_id,
            "neutralization": neutralization,
        }
    stage = int(candidate.get("stage", 0))
    dataset_id = candidate_dataset_id(candidate)
    neutralization = result_neutralization(result)
    pool_id = f"stage-{stage}__dataset-{slug(dataset_id)}__neutralization-{slug(neutralization)}"
    return pool_id, {
        "pool_id": pool_id,
        "pool_source": "derived_batch",
        "task_pool_priority": "",
        "stage": stage,
        "dataset_id": dataset_id,
        "neutralization": neutralization,
    }


def build_task_pool_ledger(
    candidate_ledger: list[dict[str, Any]],
    result_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    results = result_by_candidate_id(result_ledger)
    configured_pools = read_yaml(CONFIG / "task-pools.yaml").get("task_pools", {})
    pools: dict[str, dict[str, Any]] = {}
    for pool_id, config in configured_pools.items():
        pools[str(pool_id)] = {
            "pool_id": str(pool_id),
            "pool_source": "configured_task_pool",
            "task_pool_priority": str(config.get("priority", "")),
            "stage": config.get("stage"),
            "dataset_id": "configured",
            "neutralization": "CONFIGURED",
            "seed_candidate_id": config.get("seed_candidate_id", ""),
            "candidate_count": 0,
            "simulated_count": 0,
            "core_passed_count": 0,
            "submit_ready_count": 0,
            "submitted_count": 0,
            "waiting_checks_count": 0,
            "failed_count": 0,
            "archived_count": 0,
            "best_grade": "",
            "best_fitness": None,
            "best_sharpe": None,
            "best_test_sharpe": None,
            "candidate_ids": [],
            "alpha_ids": [],
            "submitted_alpha_ids": [],
        }
    for candidate in candidate_ledger:
        result = results.get(candidate["candidate_id"])
        key, pool_meta = candidate_pool_key(candidate, result)
        if key not in pools:
            pools[key] = {
                **pool_meta,
                "candidate_count": 0,
                "simulated_count": 0,
                "core_passed_count": 0,
                "submit_ready_count": 0,
                "submitted_count": 0,
                "waiting_checks_count": 0,
                "failed_count": 0,
                "archived_count": 0,
                "best_grade": "",
                "best_fitness": None,
                "best_sharpe": None,
                "best_test_sharpe": None,
                "candidate_ids": [],
                "alpha_ids": [],
                "submitted_alpha_ids": [],
            }
        pool = pools[key]
        pool["candidate_count"] += 1
        pool["candidate_ids"].append(candidate["candidate_id"])
        if not result:
            continue
        metrics = result.get("metrics", {})
        pool["simulated_count"] += 1
        if result.get("alpha_id"):
            pool["alpha_ids"].append(result["alpha_id"])
        if result.get("core_metrics_passed"):
            pool["core_passed_count"] += 1
        if result.get("submit_ready"):
            pool["submit_ready_count"] += 1
        if result.get("submitted"):
            if result.get("alpha_id"):
                pool["submitted_alpha_ids"].append(result["alpha_id"])
        if waiting_official_checks(result):
            pool["waiting_checks_count"] += 1
        if result.get("archived"):
            pool["archived_count"] += 1
        if result.get("failed_checks") or not result.get("core_metrics_passed"):
            pool["failed_count"] += 1

        fitness = metrics.get("fitness")
        current_best = pool["best_fitness"]
        if isinstance(fitness, (int, float)) and (current_best is None or fitness > current_best):
            pool["best_fitness"] = fitness
            pool["best_sharpe"] = metrics.get("sharpe")
            pool["best_test_sharpe"] = metrics.get("test_sharpe")
            pool["best_grade"] = result.get("grade") or ""

    rows = []
    for pool in pools.values():
        pool["submitted_count"] = len(set(pool.pop("submitted_alpha_ids", [])))
        pool["core_pass_rate"] = safe_rate(pool["core_passed_count"], pool["simulated_count"])
        pool["yield_rate"] = pool["core_pass_rate"]
        pool["submit_ready_rate"] = safe_rate(pool["submit_ready_count"], pool["simulated_count"])
        pool["submitted_rate"] = safe_rate(pool["submitted_count"], pool["simulated_count"])
        pool["official_success_rate"] = pool["submitted_rate"]
        pool["recommended_action_cn"] = pool_recommended_action(
            pool["simulated_count"],
            pool["core_passed_count"],
            pool["submit_ready_count"],
            pool["waiting_checks_count"],
            pool["submitted_count"],
            pool["archived_count"],
        )
        rows.append(pool)
    rows = sorted(rows, key=lambda row: (row["submit_ready_count"], row["core_passed_count"], row["yield_rate"], row["simulated_count"]), reverse=True)
    total_simulated = len(result_ledger)
    total_core_passed = sum(1 for row in result_ledger if row.get("core_metrics_passed"))
    total_submit_ready = sum(1 for row in result_ledger if row.get("submit_ready"))
    total_submitted = len(submitted_alpha_ids(result_ledger))
    return {
        "summary": {
            "task_pool_count": len(rows),
            "total_candidates": len(candidate_ledger),
            "total_simulated": total_simulated,
            "total_core_passed": total_core_passed,
            "total_submit_ready": total_submit_ready,
            "total_submitted": total_submitted,
            "total_archived": sum(1 for row in result_ledger if row.get("archived")),
            "total_core_pass_rate": safe_rate(total_core_passed, total_simulated),
            "total_submit_ready_rate": safe_rate(total_submit_ready, total_simulated),
            "total_submitted_rate": safe_rate(total_submitted, total_simulated),
            "total_official_success_rate": safe_rate(total_submitted, total_simulated),
        },
        "pools": rows,
    }


def pool_strategy_status(pool: dict[str, Any]) -> tuple[str, bool, bool, str]:
    seed_failures = pool.get("source_seed_gate_failures") or []
    if seed_failures:
        return (
            "source_seed_core_gate_blocked",
            True,
            True,
            f"来源 seed 未过官方核心硬门槛：{', '.join(seed_failures)}；该配置池不再自动补货或送测。",
        )
    submitted_count = int(pool.get("submitted_count") or 0)
    archived_count = int(pool.get("archived_count") or 0)
    simulated_count = int(pool.get("simulated_count") or 0)
    core_passed_count = int(pool.get("core_passed_count") or 0)
    waiting_checks_count = int(pool.get("waiting_checks_count") or 0)
    review_after = 20 if str(pool.get("task_pool_priority")) == "high_return_exploration" else 50
    if str(pool.get("task_pool_priority")) == "profile_driven_narrow_gate" and submitted_count:
        return (
            "winner_migration_active",
            False,
            False,
            "已有提交 winner；不做同族参数堆量，但允许字段 profile 驱动的换字段/换结构候选继续自动送测。",
        )
    if submitted_count and archived_count:
        return (
            "early_stopped_winner_submitted",
            True,
            True,
            "已有提交 winner，且同族样本被 SELF_CORRELATION 归档；停止同族参数挖掘，切换数据集/中性化/结构。",
        )
    if submitted_count:
        return (
            "pause_after_winner_submitted",
            True,
            True,
            "已有提交 winner；暂停同族继续送测，等待积分和复盘。",
        )
    if archived_count >= 3 and not pool.get("submit_ready_count"):
        return (
            "demote_high_correlation",
            True,
            True,
            "高相关归档较多且没有可提交样本；停止自动送测，改做去相关或换族群。",
        )
    if simulated_count >= review_after and core_passed_count == 0:
        return (
            "low_yield_revise",
            True,
            True,
            "已达到复盘批量但核心过线为 0；停止自动补池，先复盘参数/数据集/中性化。",
        )
    if waiting_checks_count:
        return (
            "wait_official_checks",
            False,
            False,
            "仍有官方检查等待；保持低频复查，不扩大规模。",
        )
    return (
        "active",
        False,
        False,
        "可继续小批量送测；仍不自动提交。",
    )


def build_pool_strategy(
    task_pool_ledger: dict[str, Any],
    result_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operating_mode = read_yaml(CONFIG / "task-pools.yaml").get("operating_mode", {})
    results = result_by_candidate_id(result_ledger or [])
    scoring = read_yaml(CONFIG / "scoring.yaml")
    rows = []
    for pool in task_pool_ledger.get("pools", []):
        seed_id = str(pool.get("seed_candidate_id") or "")
        seed_failures = (
            task_pool_pre_probe_gate_failures(
                {"task_pool_id": pool.get("pool_id"), "seed_candidate_id": seed_id},
                results,
                scoring,
            )
            if seed_id
            else []
        )
        pool = {**pool, "source_seed_gate_failures": seed_failures}
        status, block_probe, block_replenish, reason = pool_strategy_status(pool)
        rows.append(
            {
                "pool_id": pool.get("pool_id"),
                "pool_source": pool.get("pool_source"),
                "task_pool_priority": pool.get("task_pool_priority", ""),
                "seed_candidate_id": seed_id,
                "stage": pool.get("stage"),
                "dataset_id": pool.get("dataset_id"),
                "neutralization": pool.get("neutralization"),
                "pool_status": status,
                "blocked_for_auto_probe": block_probe,
                "blocked_for_auto_replenish": block_replenish,
                "source_seed_gate_failures": seed_failures,
                "submitted_count": pool.get("submitted_count", 0),
                "archived_count": pool.get("archived_count", 0),
                "simulated_count": pool.get("simulated_count", 0),
                "core_passed_count": pool.get("core_passed_count", 0),
                "submit_ready_count": pool.get("submit_ready_count", 0),
                "waiting_checks_count": pool.get("waiting_checks_count", 0),
                "recommended_action_cn": reason,
            }
        )
    return {
        "policy": {
            "rule_cn": "跑到可提交并提交后，停止同族参数挖掘；高相关失败不删除，归档为证据；低产出池达到复盘批量后停止自动补池。",
            "generation_strategy": operating_mode.get("generation_strategy", "portfolio_quota"),
            "generation_strategy_cn": operating_mode.get(
                "generation_strategy_cn",
                "生成端多元探索，送测端继续保守 gate，最终提交端最严格。",
            ),
            "replenish_strategy": operating_mode.get("replenish_strategy", "weighted_rotation"),
            "replenish_strategy_cn": "生成端按组合配额多元探索，送测端继续保守 gate，最终提交端最严格。",
            "replenish_quotas": operating_mode.get("replenish_quotas", []),
        },
        "summary": {
            "pool_count": len(rows),
            "blocked_for_auto_probe_count": sum(1 for row in rows if row["blocked_for_auto_probe"]),
            "blocked_for_auto_replenish_count": sum(1 for row in rows if row["blocked_for_auto_replenish"]),
        },
        "pools": rows,
    }


def pool_strategy_by_id(pool_strategy: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not pool_strategy:
        return {}
    return {
        str(row.get("pool_id")): row
        for row in pool_strategy.get("pools", [])
        if row.get("pool_id")
    }


def build_correlation_archive(
    candidate_ledger: list[dict[str, Any]],
    result_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = {row["candidate_id"]: row for row in candidate_ledger}
    archived_rows = []
    for result in result_ledger:
        if result.get("archive_reason") != "self_correlation_fail":
            continue
        candidate = candidates.get(result["candidate_id"], {})
        metrics = result.get("metrics", {})
        archived_rows.append(
            {
                "candidate_id": result["candidate_id"],
                "alpha_id": result.get("alpha_id"),
                "template_id": candidate.get("template_id"),
                "stage": candidate.get("stage"),
                "expression": candidate.get("expression"),
                "grade": result.get("grade"),
                "sharpe": metrics.get("sharpe"),
                "fitness": metrics.get("fitness"),
                "returns": metrics.get("returns"),
                "test_sharpe": metrics.get("test_sharpe"),
                "self_correlation_value": result.get("self_correlation_value"),
                "self_correlation_limit": result.get("self_correlation_limit"),
                "self_correlated_alpha_id": result.get("self_correlated_alpha_id"),
                "self_correlated_sharpe": result.get("self_correlated_sharpe"),
                "self_correlated_fitness": result.get("self_correlated_fitness"),
                "archive_action": "exclude_from_submission_pool_keep_as_evidence",
                "next_family_action": "stop_same_family_generation_and_switch_dataset_or_neutralization",
            }
        )
    archived_rows = sorted(
        archived_rows,
        key=lambda row: (float(row.get("self_correlation_value") or 0), float(row.get("fitness") or 0)),
        reverse=True,
    )
    return {
        "policy": {
            "delete": False,
            "rule_cn": "自相关失败样本不删除，进入高相关归档池；不再进入提交池，也不再驱动同族参数挖掘。",
        },
        "summary": {
            "archived_count": len(archived_rows),
            "unique_blocking_alpha_count": len({row.get("self_correlated_alpha_id") for row in archived_rows if row.get("self_correlated_alpha_id")}),
        },
        "archived_pool": archived_rows,
    }


def build_non_submittable_archive(
    candidate_ledger: list[dict[str, Any]],
    result_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = {row["candidate_id"]: row for row in candidate_ledger}
    archived_rows = []
    for result in result_ledger:
        reason = result.get("non_submittable_archive_reason")
        if not reason:
            continue
        candidate = candidates.get(result["candidate_id"], {})
        metrics = result.get("metrics", {})
        archived_rows.append(
            {
                "candidate_id": result["candidate_id"],
                "alpha_id": result.get("alpha_id"),
                "template_id": candidate.get("template_id"),
                "stage": candidate.get("stage"),
                "expression": candidate.get("expression"),
                "grade": result.get("grade"),
                "sharpe": metrics.get("sharpe"),
                "fitness": metrics.get("fitness"),
                "returns": metrics.get("returns"),
                "test_sharpe": metrics.get("test_sharpe"),
                "failed_checks": result.get("failed_checks", []),
                "pending_checks": result.get("pending_checks", []),
                "archive_reason": reason,
                "archive_action": "hide_from_default_official_and_local_working_views_keep_as_evidence",
                "next_family_action": "do_not_submit_use_for_failure_rate_and_parameter_revision",
            }
        )
    archived_rows = sorted(
        archived_rows,
        key=lambda row: (float(row.get("fitness") or 0), float(row.get("sharpe") or 0), float(row.get("returns") or 0)),
        reverse=True,
    )
    return {
        "policy": {
            "delete": False,
            "rule_cn": "核心指标不过线或官方检查失败的 Alpha 不删除，只归入不可提交归档；默认工作台不再堆叠展示，也不进入提交池。",
        },
        "summary": {
            "archived_count": len(archived_rows),
            "official_failed_checks_count": sum(1 for row in archived_rows if row["archive_reason"] == "official_failed_checks"),
            "core_metrics_failed_count": sum(1 for row in archived_rows if row["archive_reason"] == "core_metrics_failed"),
        },
        "archived_pool": archived_rows,
    }


def build_submission_pool(
    candidate_ledger: list[dict[str, Any]],
    result_ledger: list[dict[str, Any]],
    scoring: dict[str, Any],
    quota_now: str | None = None,
) -> dict[str, Any]:
    policy = scoring.get("submission_policy", {})
    daily_limit = int(policy.get("daily_submission_limit", 4))
    timezone_name = str(policy.get("quota_timezone") or DEFAULT_QUOTA_TIMEZONE)
    quota_date = official_quota_date(quota_now, timezone_name)
    quota_now_dt = parse_datetime(quota_now) if quota_now else datetime.now(timezone.utc)
    reservation_minutes = int(policy.get("accepted_reservation_minutes", 10))
    official_submitted_window_count = len(submitted_alpha_ids_on_quota_date(result_ledger, quota_date, timezone_name))
    accepted_submit_holds = accepted_submit_alpha_ids(
        result_ledger,
        quota_date,
        timezone_name,
        now=quota_now_dt,
        reservation_minutes=reservation_minutes,
    )
    accepted_submit_requests = accepted_submit_alpha_ids(result_ledger, quota_date, timezone_name)
    accepted_submit_hold_count = len(accepted_submit_holds)
    accepted_submit_request_count = len(accepted_submit_requests)
    submitted_window_count = official_submitted_window_count
    remaining_quota = max(0, daily_limit - submitted_window_count)
    available_quota = max(0, remaining_quota - accepted_submit_hold_count)
    candidates = {row["candidate_id"]: row for row in candidate_ledger}
    ready_rows = [
        row
        for row in result_ledger
        if row.get("submit_ready") and str(row.get("alpha_id") or "") not in accepted_submit_requests
    ]
    ready_rows = sorted(ready_rows, key=submission_sort_key, reverse=True)
    ready_pool = []
    for index, result in enumerate(ready_rows, 1):
        candidate = candidates.get(result["candidate_id"], {})
        metrics = result.get("metrics", {})
        ready_pool.append(
            {
                "submission_rank": index,
                "candidate_id": result["candidate_id"],
                "alpha_id": result.get("alpha_id"),
                "template_id": candidate.get("template_id"),
                "stage": candidate.get("stage"),
                "expression": candidate.get("expression"),
                "fitness": metrics.get("fitness"),
                "sharpe": metrics.get("sharpe"),
                "test_sharpe": metrics.get("test_sharpe"),
                "returns": metrics.get("returns"),
                "drawdown": metrics.get("drawdown"),
                "turnover": metrics.get("turnover"),
                "grade": result.get("grade"),
                "source_level": result.get("source_level"),
                "manual_action": "系统自动提交候选；仍需记录并复盘提交结果"
                if policy.get("auto_submit", False)
                else "人工提交候选；不要自动提交",
            }
        )
    return {
        "policy": {
            "daily_submission_limit": daily_limit,
            "mode": policy.get("mode", "rank_then_manual_submit"),
            "auto_submit": bool(policy.get("auto_submit", False)),
            "source": policy.get("source", "unspecified"),
            "source_note": policy.get("source_note", ""),
            "quota_cutoff_time": policy.get("quota_cutoff_time", "12:00"),
            "quota_timezone": timezone_name,
            "accepted_reservation_minutes": reservation_minutes,
            "rule_cn": policy.get("rule_cn", ""),
            "ranking": policy.get("ranking", []),
            "accepted_submit_audit_source": "state/audit/*-alpha-submit.json",
        },
        "summary": {
            "ready_count": len(ready_pool),
            "available_submission_quota": available_quota,
            "reserved_submit_request_count": accepted_submit_hold_count,
            "today_quota_count": min(len(ready_pool), available_quota),
            "held_for_later_count": max(0, len(ready_pool) - available_quota),
            "accepted_hold_count": accepted_submit_hold_count,
            "quota_date": quota_date,
            "submitted_window_count": submitted_window_count,
            "submitted_today_count": submitted_window_count,
            "official_submitted_window_count": official_submitted_window_count,
            "accepted_submit_request_count": accepted_submit_request_count,
            "accepted_submit_hold_count": accepted_submit_hold_count,
            "remaining_submission_quota": remaining_quota,
            "submission_gate_locked": available_quota <= 0,
        },
        "ready_pool": ready_pool,
        "today_quota": ready_pool[:available_quota],
    }


def is_probe_ready(
    candidate: dict[str, Any],
    result: dict[str, Any] | None,
    strategies: dict[str, dict[str, Any]] | None = None,
    results_by_id: dict[str, dict[str, Any]] | None = None,
    scoring: dict[str, Any] | None = None,
) -> bool:
    if result:
        return False
    if candidate.get("status") != "probe_blocked":
        return False
    if candidate.get("review_status") != "needs_human_gate":
        return False
    precheck = candidate.get("local_precheck", {})
    if precheck.get("decision") != "ready_for_manual_gate":
        return False
    params = candidate.get("params", {})
    if probe_rule_gate_failures(candidate):
        return False
    if params.get("task_pool_auto_submit") is True:
        return False
    if params.get("task_pool_priority") == "demoted":
        return False
    if task_pool_pre_probe_gate_failures(params, results_by_id, scoring):
        return False
    pool_id = params.get("task_pool_id")
    strategy = (strategies or {}).get(str(pool_id)) if pool_id else None
    if strategy and strategy.get("blocked_for_auto_probe"):
        return False
    return True


def probe_sort_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    priority_score = {
        "quality_baseline": 4,
        "mainline": 3,
        "limited_contrast": 2,
        "": 1,
    }.get(str(row.get("priority", "")), 1)
    return (
        priority_score,
        int(row.get("stage") or 0),
        int(row.get("local_precheck_score") or 0),
        str(row.get("candidate_id", "")),
    )


def build_probe_pool(
    candidate_ledger: list[dict[str, Any]],
    result_ledger: list[dict[str, Any]],
    scoring: dict[str, Any],
    pool_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results = result_by_candidate_id(result_ledger)
    strategies = pool_strategy_by_id(pool_strategy)
    ready_pool = []
    blocked_pool = []
    for candidate in candidate_ledger:
        result = results.get(candidate["candidate_id"])
        params = candidate.get("params", {})
        precheck = candidate.get("local_precheck", {})
        pool_id = str(params.get("task_pool_id", ""))
        strategy = strategies.get(pool_id) if pool_id else None
        source_failures = task_pool_pre_probe_gate_failures(params, results, scoring)
        rule_failures = probe_rule_gate_failures(candidate)
        pre_probe_blocked = bool(pool_id and source_failures)
        base_ready = is_probe_ready(candidate, result, {}, results, scoring)
        strategy_blocked = base_ready and bool(strategy and strategy.get("blocked_for_auto_probe"))
        screen_decision = (
            "ready_for_system_probe"
            if is_probe_ready(candidate, result, strategies, results, scoring)
            else "hold_or_already_tested"
        )
        if strategy_blocked:
            screen_decision = "pool_strategy_blocked"
        if pre_probe_blocked:
            screen_decision = "source_seed_core_gate_blocked"
        if rule_failures:
            screen_decision = "legacy_candidate_quarantined"
        row = {
            "candidate_id": candidate["candidate_id"],
            "template_id": candidate.get("template_id"),
            "stage": candidate.get("stage"),
            "expression": candidate.get("expression"),
            "task_pool_id": pool_id,
            "task_pool_batch_id": params.get("task_pool_batch_id", ""),
            "priority": params.get("task_pool_priority", ""),
            "screen_decision": screen_decision,
            "pool_strategy_status": strategy.get("pool_status") if strategy else "",
            "local_precheck_score": precheck.get("score"),
            "official_alpha_id": result.get("alpha_id") if result else None,
            "official_core_passed": bool(result.get("core_metrics_passed")) if result else False,
            "submit_ready": bool(result.get("submit_ready")) if result else False,
            "pre_probe_gate_passed": params.get("pre_probe_gate_passed"),
            "pre_probe_gate_source": params.get("pre_probe_gate_source") or params.get("seed_candidate_id", ""),
            "pre_probe_gate_failures": source_failures,
            "probe_rule_gate_failures": rule_failures,
        }
        if is_probe_ready(candidate, result, strategies, results, scoring):
            row["manual_action"] = "系统可自动进入官方 simulation；过线后由 submit-ready 自动提交器处理"
            ready_pool.append(row)
        else:
            if strategy_blocked:
                row["manual_action"] = "任务池策略已早停；不再自动进入官方 simulation"
            elif pre_probe_blocked:
                row["manual_action"] = "来源 seed 未过官方核心硬门槛；不进入官方 simulation"
            elif rule_failures:
                row["manual_action"] = "旧候选或不符合当前 analyst4/fundamental6/pv1 规则；隔离，不再自动送测"
            else:
                row["manual_action"] = "暂不进入官方 simulation；等待复盘或已由官网结果接管"
            blocked_pool.append(row)

    ready_pool = sorted(ready_pool, key=probe_sort_key, reverse=True)
    policy = scoring.get("submission_policy", {})
    official_tested_count = len(result_ledger)
    official_core_passed_count = sum(1 for row in result_ledger if row.get("core_metrics_passed"))
    submit_ready_count = sum(1 for row in result_ledger if row.get("submit_ready"))
    submitted_count = len(submitted_alpha_ids(result_ledger))
    return {
        "policy": {
            "auto_probe": True,
            "auto_submit": bool(policy.get("auto_submit", False)),
            "source_of_truth": "WorldQuant BRAIN alpha detail and platform checks",
            "rule_cn": "本地先筛出值得官方 simulation 的候选；系统可按优先级自动送入官方 simulation；官方结果回填后再判断核心过线、自相关和可提交状态；最终 Submit 由 submit-ready 自动提交器按额度执行。",
        },
        "summary": {
            "local_candidate_count": len(candidate_ledger),
            "probe_ready_count": len(ready_pool),
            "official_tested_count": official_tested_count,
            "official_core_passed_count": official_core_passed_count,
            "official_core_pass_rate": safe_rate(official_core_passed_count, official_tested_count),
            "waiting_checks_count": sum(1 for row in result_ledger if waiting_official_checks(row)),
            "submit_ready_count": submit_ready_count,
            "submit_ready_rate": safe_rate(submit_ready_count, official_tested_count),
            "submitted_count": submitted_count,
            "submitted_rate": safe_rate(submitted_count, official_tested_count),
            "official_success_rate": safe_rate(submitted_count, official_tested_count),
            "archived_count": sum(1 for row in result_ledger if row.get("archived")),
            "non_submittable_archived_count": sum(1 for row in result_ledger if row.get("non_submittable_archive_reason")),
        },
        "ready_pool": ready_pool,
        "blocked_or_completed_pool": blocked_pool,
    }


def build_candidate_row(candidate: dict[str, Any]) -> dict[str, Any]:
    latest = candidate.get("latest_result_import") or {}
    return {
        "candidate_id": candidate["candidate_id"],
        "template_id": candidate["template_id"],
        "stage": candidate["stage"],
        "status": candidate["status"],
        "review_status": candidate.get("review_status"),
        "expression": candidate["rendered_expression"],
        "params": candidate.get("params", {}),
        "required_fields": candidate.get("required_fields", []),
        "latest_simulation_id": latest.get("simulation_id") or candidate.get("latest_simulation_id"),
        "latest_alpha_id": latest.get("alpha_id"),
        "rationale": candidate.get("rationale", ""),
        "local_precheck": candidate.get("local_precheck", {}),
    }


def build_result_row(candidate: dict[str, Any], scoring: dict[str, Any]) -> dict[str, Any] | None:
    result = candidate.get("latest_result_import")
    if not isinstance(result, dict):
        return None
    pending = check_names(result, "PENDING")
    submitted = is_alpha_submitted(result)
    archive = archive_reason(result)
    non_submittable_reason = non_submittable_archive_reason(result, scoring)
    correlation = self_correlation_match(result) if archive else {}
    ready = is_submit_ready(result, scoring)
    return {
        "candidate_id": candidate["candidate_id"],
        "simulation_id": result.get("simulation_id"),
        "alpha_id": result.get("alpha_id"),
        "import_source": result.get("import_source"),
        "source_level": source_level(result),
        "alpha_status": result.get("alpha_status"),
        "alpha_stage": result.get("alpha_stage"),
        "date_submitted": result.get("date_submitted"),
        "submitted": submitted,
        "archived": bool(archive),
        "archive_reason": archive,
        "non_submittable_archive_reason": non_submittable_reason,
        **correlation,
        "grade": result.get("grade"),
        "simulation_settings": result.get("simulation_settings", {}),
        "metrics": result.get("metrics", {}),
        "failed_checks": check_names(result, "FAIL"),
        "pending_checks": pending,
        "core_metrics_passed": core_metrics_passed(result, scoring),
        "manual_gate_required": (not submitted) and (bool(pending) or not ready),
        "submit_ready": ready,
        "source_notes": result.get("notes", ""),
    }


def build_iteration_row(candidate: dict[str, Any], scoring: dict[str, Any]) -> dict[str, Any]:
    result = candidate.get("latest_result_import") or {}
    ready = is_submit_ready(result, scoring) if result else False
    reasons = failure_reasons(result, scoring) if result else []
    lane = review_lane(result, ready, reasons, scoring)
    positive = success_retro_required(result, ready) if result else False
    row = {
        "candidate_id": candidate["candidate_id"],
        "template_id": candidate["template_id"],
        "stage": candidate["stage"],
        "review_lane": lane,
        "failure_reasons": reasons,
        "optimization_focus": optimization_focus(reasons) if result else ["run_first_simulation"],
        "success_retro_required": positive,
        "next_actions": next_actions(lane, reasons, int(candidate["stage"])),
    }
    if positive:
        row["success_retro_questions"] = [
            "which_field_or_operator_drove_the_gain",
            "which_metric_improved",
            "should_this_candidate_become_a_seed",
            "should_template_field_or_parameter_weight_increase",
        ]
    return row


def main() -> int:
    scoring = read_yaml(CONFIG / "scoring.yaml")
    candidates = [read_json(path) for path in candidate_files()]
    candidate_ledger = [build_candidate_row(candidate) for candidate in candidates]
    result_ledger = [
        row
        for row in (build_result_row(candidate, scoring) for candidate in candidates)
        if row is not None
    ]
    iteration_ledger = [build_iteration_row(candidate, scoring) for candidate in candidates]
    submission_pool = build_submission_pool(candidate_ledger, result_ledger, scoring)
    task_pool_ledger = build_task_pool_ledger(candidate_ledger, result_ledger)
    pool_strategy = build_pool_strategy(task_pool_ledger, result_ledger)
    probe_pool = build_probe_pool(candidate_ledger, result_ledger, scoring, pool_strategy)
    correlation_archive = build_correlation_archive(candidate_ledger, result_ledger)
    non_submittable_archive = build_non_submittable_archive(candidate_ledger, result_ledger)

    write_json(LEDGER / "candidate-ledger.json", candidate_ledger)
    write_json(LEDGER / "result-ledger.json", result_ledger)
    write_json(LEDGER / "iteration-ledger.json", iteration_ledger)
    write_json(LEDGER / "probe-pool.json", probe_pool)
    write_json(LEDGER / "submission-pool.json", submission_pool)
    write_json(LEDGER / "task-pool-ledger.json", task_pool_ledger)
    write_json(LEDGER / "pool-strategy.json", pool_strategy)
    write_json(LEDGER / "correlation-archive.json", correlation_archive)
    write_json(LEDGER / "non-submittable-archive.json", non_submittable_archive)
    print(
        json.dumps(
            {
                "candidate_rows": len(candidate_ledger),
                "result_rows": len(result_ledger),
                "iteration_rows": len(iteration_ledger),
                "task_pool_rows": len(task_pool_ledger["pools"]),
                "pool_strategy_blocked": pool_strategy["summary"]["blocked_for_auto_probe_count"],
                "positive_retro_required": sum(1 for row in iteration_ledger if row["success_retro_required"]),
                "submit_ready": submission_pool["summary"]["ready_count"],
                "daily_submission_limit": submission_pool["policy"]["daily_submission_limit"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
