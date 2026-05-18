from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[0]
STATE = ROOT / "state"
LEDGER = STATE / "ledger"
PROFILE_DIR = STATE / "datafield-profiles"
PROFILE_PROBE_DIR = STATE / "datafield-profile-probes"
RAW_FIELDS = (
    PROJECT_ROOT
    / "knowledge-library"
    / "sources"
    / "worldquant-brain-official"
    / "raw"
    / "data-fields-usa-delay1-top3000.json"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def load_raw_fields(path: pathlib.Path = RAW_FIELDS) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected object in raw field file: {path}")
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise RuntimeError(f"Expected results list in raw field file: {path}")
    return [row for row in results if isinstance(row, dict) and row.get("id")]


def fields_by_id(path: pathlib.Path = RAW_FIELDS) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in load_raw_fields(path)}


def dataset_id(row: dict[str, Any]) -> str:
    dataset = row.get("dataset")
    if isinstance(dataset, dict):
        return str(dataset.get("id") or "")
    return ""


def dataset_name(row: dict[str, Any]) -> str:
    dataset = row.get("dataset")
    if isinstance(dataset, dict):
        return str(dataset.get("name") or "")
    return ""


def category_name(row: dict[str, Any]) -> str:
    category = row.get("category")
    if isinstance(category, dict):
        return str(category.get("name") or category.get("id") or "")
    return ""


def coverage_value(row: dict[str, Any]) -> float | None:
    value = row.get("coverage")
    return float(value) if isinstance(value, (int, float)) else None


def field_tags(row: dict[str, Any]) -> list[str]:
    tags = []
    ds = dataset_id(row)
    category = category_name(row).lower()
    if ds.startswith("fundamental") or category == "fundamental":
        tags.append("fundamental_signal")
    if ds.startswith("analyst") or category == "analyst":
        tags.append("analyst_signal")
    if category in {"price volume", "pv"}:
        tags.append("price_volume_signal")
    if category in {"sentiment", "social media"}:
        tags.append("sentiment_signal")
    if "event" in str(row.get("description", "")).lower():
        tags.append("event_like")
    return sorted(set(tags))


def quality_flags(row: dict[str, Any]) -> list[str]:
    flags = []
    coverage = coverage_value(row)
    if coverage is None:
        flags.append("coverage_unknown")
    elif coverage < 0.30:
        flags.append("low_coverage")
    elif coverage < 0.50:
        flags.append("coverage_watch")
    if (row.get("alphaCount") or 0) >= 100000:
        flags.append("crowded_field")
    if not str(row.get("description", "")).strip():
        flags.append("descriptor_unclear")
    return flags


def profile_lane(row: dict[str, Any]) -> str:
    flags = quality_flags(row)
    coverage = coverage_value(row)
    if "low_coverage" in flags or coverage is None:
        return "avoid_for_now"
    if "coverage_watch" in flags:
        return "exploratory"
    if "event_like" in field_tags(row):
        return "sparse_event"
    return "mainline_ready"


def recommended_templates(row: dict[str, Any]) -> list[str]:
    tags = field_tags(row)
    lane = profile_lane(row)
    if lane == "sparse_event":
        return ["event_gate", "trade_when_gate"]
    if "fundamental_signal" in tags:
        return ["time_series_rank", "group_rank", "financial_ratio"]
    if "analyst_signal" in tags:
        return ["time_series_rank", "group_rank", "analyst_revision"]
    if "price_volume_signal" in tags:
        return ["time_series_rank", "ts_corr", "liquidity_gate"]
    if "sentiment_signal" in tags:
        return ["nonzero_coverage", "trade_when_gate"]
    return ["time_series_rank", "group_rank"]


def diagnostic_expressions(field_id: str) -> list[dict[str, Any]]:
    return [
        {
            "kind": "raw_coverage",
            "expression": field_id,
            "observe": "coverage and long/short count relative to universe",
        },
        {
            "kind": "nonzero_coverage",
            "expression": f"{field_id} != 0 ? 1 : 0",
            "observe": "effective non-zero density",
        },
        {
            "kind": "update_frequency",
            "expression": f"ts_std_dev({field_id}, 60) != 0 ? 1 : 0",
            "observe": "whether the field changes often enough for short or medium windows",
        },
        {
            "kind": "extreme_value",
            "expression": f"abs({field_id}) > 1 ? 1 : 0",
            "observe": "rough outlier pressure before rank or winsorize",
        },
        {
            "kind": "long_median",
            "expression": f"ts_median({field_id}, 1000) > 1 ? 1 : 0",
            "observe": "five-year baseline level as a conditional long-count probe",
            "variants": [
                {"variant_key": "gt_1", "expression": f"ts_median({field_id}, 1000) > 1 ? 1 : 0"},
                {"variant_key": "gt_10", "expression": f"ts_median({field_id}, 1000) > 10 ? 1 : 0"},
            ],
        },
        {
            "kind": "scaled_distribution",
            "expression": f"scale_down({field_id}) > 0.25 ? 1 : 0",
            "observe": "scaled distribution threshold density",
            "variants": [
                {"variant_key": "gt_0p25", "threshold": 0.25, "expression": f"scale_down({field_id}) > 0.25 ? 1 : 0"},
                {"variant_key": "gt_0p5", "threshold": 0.5, "expression": f"scale_down({field_id}) > 0.5 ? 1 : 0"},
                {"variant_key": "gt_0p75", "threshold": 0.75, "expression": f"scale_down({field_id}) > 0.75 ? 1 : 0"},
                {"variant_key": "gt_1p0", "threshold": 1.0, "expression": f"scale_down({field_id}) > 1 ? 1 : 0"},
            ],
        },
    ]


def build_profile(row: dict[str, Any], run_id: str) -> dict[str, Any]:
    field_id = str(row["id"])
    coverage = coverage_value(row)
    return {
        "profile_id": f"profile-{safe_slug(field_id)}",
        "run_id": safe_slug(run_id),
        "created_at": utc_now(),
        "field_id": field_id,
        "dataset_id": dataset_id(row),
        "dataset_name": dataset_name(row),
        "region": row.get("region", "USA"),
        "delay": row.get("delay", 1),
        "universe": row.get("universe", "TOP3000"),
        "field_kind": str(row.get("type", "")).lower(),
        "category": category_name(row),
        "description": str(row.get("description", "")),
        "usage": {
            "user_count": int(row.get("userCount") or 0),
            "alpha_count": int(row.get("alphaCount") or 0),
        },
        "coverage": {
            "raw_coverage": coverage,
            "date_coverage": row.get("dateCoverage"),
            "nonzero_coverage": None,
        },
        "update_behavior": {
            "ts_std_nonzero_ratio": None,
            "inferred_frequency": "unknown",
        },
        "distribution": {
            "abs_threshold_hits": [],
            "long_median_flags": [],
            "scaled_range_flags": [],
        },
        "quality_flags": quality_flags(row),
        "field_tags": field_tags(row),
        "profile_lane": profile_lane(row),
        "recommended_templates": recommended_templates(row),
        "diagnostic_plan": {
            "settings": {
                "instrumentType": "EQUITY",
                "region": row.get("region", "USA"),
                "delay": row.get("delay", 1),
                "universe": row.get("universe", "TOP3000"),
                "neutralization": "NONE",
                "decay": 0,
                "truncation": 0,
                "pasteurization": "OFF",
                "language": "FASTEXPR",
                "unitHandling": "VERIFY",
                "nanHandling": "OFF",
                "visualization": False,
            },
            "expressions": diagnostic_expressions(field_id),
        },
        "notes": (
            "Static profile from official datafield metadata. "
            "Run profile probes to fill nonzero/update/distribution evidence before scaling task pools."
        ),
    }


def build_profiles(field_ids: list[str], run_id: str) -> list[dict[str, Any]]:
    raw = fields_by_id()
    selected_ids = field_ids or sorted(raw)
    missing = [field_id for field_id in selected_ids if field_id not in raw]
    if missing:
        raise KeyError(f"Unknown field id(s): {', '.join(missing)}")
    return [build_profile(raw[field_id], run_id) for field_id in selected_ids]


def profile_summary(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    lanes = Counter(str(profile.get("profile_lane", "")) for profile in profiles)
    datasets = Counter(str(profile.get("dataset_id", "")) for profile in profiles)
    return {
        "profile_count": len(profiles),
        "mainline_ready_count": lanes.get("mainline_ready", 0),
        "exploratory_count": lanes.get("exploratory", 0),
        "sparse_event_count": lanes.get("sparse_event", 0),
        "avoid_for_now_count": lanes.get("avoid_for_now", 0),
        "dataset_counts": dict(sorted(datasets.items())),
    }


def write_profile_assets(profiles: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    for profile in profiles:
        write_json(PROFILE_DIR / f"{profile['field_id']}.json", profile)
    payload = {
        "run_id": safe_slug(run_id),
        "created_at": utc_now(),
        "summary": profile_summary(profiles),
        "profiles": profiles,
    }
    write_json(LEDGER / "datafield-profile-ledger.json", payload)
    return payload


def load_profiles(field_ids: list[str] | None = None) -> list[dict[str, Any]]:
    paths = sorted(PROFILE_DIR.glob("*.json"))
    profiles = [read_json(path) for path in paths]
    rows = [profile for profile in profiles if isinstance(profile, dict)]
    if field_ids:
        wanted = set(field_ids)
        rows = [profile for profile in rows if str(profile.get("field_id")) in wanted]
    return rows


def build_probe(profile: dict[str, Any], expression_row: dict[str, Any], run_id: str) -> dict[str, Any]:
    field_id = str(profile["field_id"])
    kind = str(expression_row["kind"])
    return {
        "probe_id": f"probe-{safe_slug(run_id)}-{safe_slug(field_id)}-{safe_slug(kind)}",
        "probe_type": "datafield_profile",
        "run_id": safe_slug(run_id),
        "created_at": utc_now(),
        "field_id": field_id,
        "profile_id": profile.get("profile_id"),
        "dataset_id": profile.get("dataset_id"),
        "kind": kind,
        "expression": expression_row["expression"],
        "observe": expression_row.get("observe", ""),
        "simulation_settings": profile["diagnostic_plan"]["settings"],
        "submit_allowed": False,
        "profile_lane": profile.get("profile_lane", ""),
        "notes": "Profile probes are diagnostics only; they must not enter alpha submission.",
    }


def build_profile_probes(profiles: list[dict[str, Any]], run_id: str, limit_per_field: int | None = None) -> list[dict[str, Any]]:
    probes = []
    for profile in profiles:
        expressions = list(profile.get("diagnostic_plan", {}).get("expressions", []))
        if limit_per_field is not None:
            expressions = expressions[: max(0, int(limit_per_field))]
        for expression_row in expressions:
            probes.append(build_probe(profile, expression_row, run_id))
    return probes


def write_probe_assets(probes: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    for probe in probes:
        write_json(PROFILE_PROBE_DIR / f"{probe['probe_id']}.json", probe)
    payload = {
        "run_id": safe_slug(run_id),
        "created_at": utc_now(),
        "summary": {
            "probe_count": len(probes),
            "field_count": len({probe["field_id"] for probe in probes}),
        },
        "probes": probes,
    }
    write_json(LEDGER / "datafield-profile-probe-ledger.json", payload)
    return payload


def select_fields(dataset_id_value: str | None, lane: str, limit: int | None, run_id: str) -> dict[str, Any]:
    rows = []
    for profile in load_profiles():
        if dataset_id_value and profile.get("dataset_id") != dataset_id_value:
            continue
        if lane and profile.get("profile_lane") != lane:
            continue
        rows.append(
            {
                "field_id": profile["field_id"],
                "dataset_id": profile.get("dataset_id", ""),
                "profile_lane": profile.get("profile_lane", ""),
                "coverage": profile.get("coverage", {}).get("raw_coverage"),
                "quality_flags": profile.get("quality_flags", []),
                "recommended_templates": profile.get("recommended_templates", []),
                "why_selected": (
                    f"field profile lane {lane} matches task-pool gate"
                    + (f" for dataset {dataset_id_value}" if dataset_id_value else "")
                ),
            }
        )
    rows = sorted(rows, key=lambda row: (row["coverage"] is not None, row["coverage"] or 0, row["field_id"]), reverse=True)
    if limit is not None:
        rows = rows[: max(0, int(limit))]
    payload = {
        "run_id": safe_slug(run_id),
        "created_at": utc_now(),
        "dataset_id": dataset_id_value or "",
        "lane": lane,
        "selected_count": len(rows),
        "selected_fields": rows,
    }
    write_json(LEDGER / f"datafield-selection-{safe_slug(run_id)}.json", payload)
    return payload
