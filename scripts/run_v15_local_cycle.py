#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
from typing import Any

from build_ledgers import (
    build_candidate_row,
    build_correlation_archive,
    build_iteration_row,
    build_non_submittable_archive,
    build_pool_strategy,
    build_probe_pool,
    build_result_row,
    build_submission_pool,
    build_task_pool_ledger,
    candidate_files,
    read_yaml,
    write_json,
)
from datafield_profiler import (
    LEDGER,
    PROFILE_PROBE_DIR,
    build_profile_probes,
    build_profiles,
    profile_summary,
    safe_slug,
    select_fields,
    write_probe_assets,
    write_profile_assets,
)
from export_visual_ledger import build_rows as build_visual_rows
from export_visual_ledger import build_summary as build_visual_summary
from export_visual_ledger import write_csv, write_html
from generate_profile_stage2_pool import build_candidates, load_selection
from verify_official_course_read_gate import build_payload as build_official_course_read_gate


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[0]
STATE = ROOT / "state"
QUEUE = STATE / "queue"
VISUAL = STATE / "visual"
CONFIG = ROOT / "config"
DASHBOARD_ENTRY = PROJECT_ROOT / "Factor-Factory-Dashboard.html"
DEFAULT_PROFILE_FIELDS = [
    "assets",
    "sales",
    "operating_income",
    "cashflow_op",
    "inventory_turnover",
    "est_eps",
    "est_sales",
    "est_capex",
    "est_netprofit",
    "est_ptp",
    "cap",
    "close",
    "volume",
    "returns",
    "industry",
    "subindustry",
]


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the safe local-only Factor Factory V1.5 cycle.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--profile-probe-limit-per-field", type=int, default=6)
    parser.add_argument("--field-id", action="append", default=[], help="Override profiled field ids. Can be repeated.")
    return parser.parse_args()


def build_local_ledgers() -> dict[str, Any]:
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
    return {
        "candidate_rows": len(candidate_ledger),
        "result_rows": len(result_ledger),
        "iteration_rows": len(iteration_ledger),
        "task_pool_rows": len(task_pool_ledger["pools"]),
        "pool_strategy_blocked": pool_strategy["summary"]["blocked_for_auto_probe_count"],
        "submit_ready": submission_pool["summary"]["ready_count"],
    }


def export_visual_dashboard() -> dict[str, Any]:
    rows = build_visual_rows()
    summary = build_visual_summary(rows)
    csv_path = VISUAL / "factor-factory-dashboard.csv"
    html_path = VISUAL / "factor-factory-dashboard.html"
    summary_path = VISUAL / "factor-factory-summary.json"
    write_csv(csv_path, rows)
    write_html(html_path, rows, summary)
    write_json(summary_path, summary)
    if not DASHBOARD_ENTRY.exists():
        try:
            DASHBOARD_ENTRY.symlink_to(html_path.relative_to(PROJECT_ROOT))
        except OSError:
            shutil.copyfile(html_path, DASHBOARD_ENTRY)
    return {
        "rows": len(rows),
        "summary": summary,
        "csv": str(csv_path),
        "html": str(html_path),
    }


def main() -> int:
    args = parse_args()
    run_id = safe_slug(args.run_id)
    fields = args.field_id or DEFAULT_PROFILE_FIELDS
    official_course_gate = build_official_course_read_gate()
    if not official_course_gate["confirmed"]:
        raise RuntimeError("Official course read gate failed; refusing to run V1.5 local cycle.")

    profiles = build_profiles(fields, run_id)
    profile_ledger = write_profile_assets(profiles, run_id)
    probes = build_profile_probes(profiles, run_id, args.profile_probe_limit_per_field)
    profile_probe_ledger = write_probe_assets(probes, run_id)

    selections = {
        "analyst4": select_fields("analyst4", "mainline_ready", 5, f"{run_id}-select-analyst4"),
        "fundamental6": select_fields("fundamental6", "mainline_ready", 5, f"{run_id}-select-fundamental6"),
        "pv1": select_fields("pv1", "mainline_ready", 6, f"{run_id}-select-pv1"),
    }

    analyst_rows = load_selection(f"{run_id}-select-analyst4")
    fundamental_rows = load_selection(f"{run_id}-select-fundamental6")
    pv_rows = load_selection(f"{run_id}-select-pv1")
    stage2_candidates = build_candidates(
        run_id,
        analyst_rows,
        fundamental_rows,
        max(0, args.candidate_limit),
        pv_rows=pv_rows,
    )
    generated_ids = []
    skipped_ids = []
    for candidate in stage2_candidates:
        path = QUEUE / f"{candidate['candidate_id']}.json"
        if path.exists():
            skipped_ids.append(candidate["candidate_id"])
            continue
        write_json(path, candidate)
        generated_ids.append(candidate["candidate_id"])

    ledgers = build_local_ledgers()
    visual = export_visual_dashboard()
    payload = {
        "run_id": run_id,
        "mode": "local_only",
        "live_platform_actions": False,
        "official_course_gate": {
            "confirmed": official_course_gate["confirmed"],
            "summary": official_course_gate["summary"],
        },
        "profiles": profile_summary(profiles),
        "profile_ledger": str(LEDGER / "datafield-profile-ledger.json"),
        "profile_probes": profile_probe_ledger["summary"],
        "profile_probe_dir": str(PROFILE_PROBE_DIR),
        "selections": {
            key: {
                "run_id": value["run_id"],
                "selected_count": value["selected_count"],
                "fields": [row["field_id"] for row in value["selected_fields"]],
            }
            for key, value in selections.items()
        },
        "stage2_generation": {
            "pool_id": "profile-stage2-field-blend-v15",
            "planned_count": len(stage2_candidates),
            "generated_count": len(generated_ids),
            "skipped_count": len(skipped_ids),
            "generated_candidate_ids": generated_ids,
            "skipped_candidate_ids": skipped_ids,
        },
        "ledgers": ledgers,
        "visual": visual,
        "dashboard_entry": str(DASHBOARD_ENTRY),
        "next_live_gate": "explicit_user_confirmation_required",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
