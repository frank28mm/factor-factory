from __future__ import annotations

from typing import Any


def _nested_metric_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [payload]
    for key in ("is", "isMetrics", "metrics", "results", "result", "trainMetrics", "osMetrics"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _first_number(sources: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def normalize_turnover(value: float | None) -> float | None:
    if value is None:
        return None
    if value > 1.0:
        return value / 100.0
    return value


def extract_metrics(payload: dict[str, Any]) -> dict[str, float | None]:
    sources = _nested_metric_sources(payload)
    turnover = _first_number(sources, ("turnover", "is_turnover", "turnoverPct", "turnoverPercent"))
    return {
        "sharpe": _first_number(sources, ("sharpe", "is_sharpe", "sharpeRatio")),
        "fitness": _first_number(sources, ("fitness", "is_fitness")),
        "turnover": normalize_turnover(turnover),
        "returns": _first_number(sources, ("returns", "is_returns", "return", "annualReturn")),
        "drawdown": _first_number(sources, ("drawdown", "is_drawdown", "maxDrawdown")),
        "margin": _first_number(sources, ("margin", "is_margin")),
        "self_correlation": _first_number(
            sources,
            ("self_correlation", "selfCorrelation", "self_corr", "pnlCorrelation"),
        ),
    }


def _section_metric(payload: dict[str, Any], section: str, key: str) -> float | None:
    value = payload.get(section)
    if not isinstance(value, dict):
        return None
    metric = value.get(key)
    if isinstance(metric, (int, float)):
        return float(metric)
    return None


def _rounded_gap(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 6)


def extract_alpha_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    checks = payload.get("checks")
    if isinstance(checks, list):
        return [check for check in checks if isinstance(check, dict)]
    is_metrics = payload.get("is")
    if isinstance(is_metrics, dict) and isinstance(is_metrics.get("checks"), list):
        return [check for check in is_metrics["checks"] if isinstance(check, dict)]
    return []


def extract_self_correlation_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    is_metrics = payload.get("is")
    if not isinstance(is_metrics, dict):
        return []
    self_correlated = is_metrics.get("selfCorrelated")
    if not isinstance(self_correlated, dict):
        return []
    records = self_correlated.get("records")
    if not isinstance(records, list):
        return []

    matches = []
    for record in records:
        if not isinstance(record, list) or len(record) < 6:
            continue
        matches.append(
            {
                "name": "SELF_CORRELATION_MATCH",
                "result": "INFO",
                "alpha_id": record[0],
                "correlation": record[5],
                "sharpe": record[6] if len(record) > 6 else None,
                "returns": record[7] if len(record) > 7 else None,
                "turnover": record[8] if len(record) > 8 else None,
                "fitness": record[9] if len(record) > 9 else None,
                "margin": record[10] if len(record) > 10 else None,
            }
        )
    return matches


def merge_alpha_check_payload(alpha_payload: dict[str, Any], check_payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(alpha_payload)
    checks = extract_alpha_checks(check_payload)
    matches = extract_self_correlation_matches(check_payload)
    if matches:
        checks = checks + matches
    if not checks:
        return merged
    is_metrics = merged.get("is")
    if isinstance(is_metrics, dict):
        merged_is = dict(is_metrics)
        merged_is["checks"] = checks
        merged["is"] = merged_is
    else:
        merged["checks"] = checks
    return merged


def build_result_import_from_live_payload(
    *,
    candidate_id: str,
    simulation_settings: dict[str, Any],
    payload: dict[str, Any],
    simulation_id: str | None = None,
    alpha_id: str | None = None,
    source_type: str = "simulation_result",
    notes: str | None = None,
) -> dict[str, Any]:
    metrics = extract_metrics(payload)
    if metrics["sharpe"] is None or metrics["fitness"] is None or metrics["turnover"] is None:
        raise ValueError("Live simulation payload is missing one or more required metrics: sharpe, fitness, turnover.")

    if source_type == "alpha_detail":
        train_sharpe = _section_metric(payload, "train", "sharpe")
        train_fitness = _section_metric(payload, "train", "fitness")
        test_sharpe = _section_metric(payload, "test", "sharpe")
        test_fitness = _section_metric(payload, "test", "fitness")
        metrics.update(
            {
                "train_sharpe": train_sharpe,
                "train_fitness": train_fitness,
                "test_sharpe": test_sharpe,
                "test_fitness": test_fitness,
                "train_test_sharpe_gap": _rounded_gap(train_sharpe, test_sharpe),
                "train_test_fitness_gap": _rounded_gap(train_fitness, test_fitness),
            }
        )

    record: dict[str, Any] = {
        "candidate_id": candidate_id,
        "import_source": "live_alpha_detail" if source_type == "alpha_detail" else "live_simulation_audit",
        "platform": "worldquant_brain",
        "simulation_settings": simulation_settings,
        "metrics": {key: value for key, value in metrics.items() if value is not None},
    }
    if simulation_id:
        record["simulation_id"] = simulation_id
    resolved_alpha_id = alpha_id or str(payload.get("id") or "")
    if source_type == "alpha_detail" and resolved_alpha_id:
        record["alpha_id"] = resolved_alpha_id
    if source_type == "alpha_detail":
        if payload.get("grade") is not None:
            record["grade"] = str(payload["grade"])
        if payload.get("status") is not None:
            record["alpha_status"] = str(payload["status"])
        if payload.get("stage") is not None:
            record["alpha_stage"] = str(payload["stage"])
        if payload.get("dateSubmitted") is not None:
            record["date_submitted"] = str(payload["dateSubmitted"])
        checks = extract_alpha_checks(payload)
        if checks:
            record["checks"] = checks
    if notes:
        record["notes"] = notes
    return record
