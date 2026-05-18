#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from connectors.worldquant_brain.session_client import browser_fetch_json_response, find_worldquant_target_id  # noqa: E402
from connectors.worldquant_brain.session_probe import run_read_only_contract_check  # noqa: E402

SOURCE_ROOT = ROOT / "knowledge-library" / "sources" / "worldquant-brain-official"
RAW = SOURCE_ROOT / "raw"
MARKDOWN = SOURCE_ROOT / "markdown"
HEALTHCHECKS = SOURCE_ROOT / "healthchecks"
FIELD_QUERY = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "delay": "1",
    "universe": "TOP3000",
}
FIELD_PAGE_SIZE = 50


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: pathlib.Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def fetch_json(target_id: str, endpoint: str) -> Any:
    response = browser_fetch_json_response(target_id, endpoint)
    status = int(response.get("status", 0) or 0)
    if status != 200:
        text = str(response.get("text") or "")[:300]
        raise RuntimeError(f"WorldQuant request failed: {endpoint} HTTP {status} {text}")
    return response.get("payload")


def fetch_data_fields(target_id: str) -> int:
    partial_path = RAW / "data-fields-usa-delay1-top3000.partial.json"
    final_path = RAW / "data-fields-usa-delay1-top3000.json"
    field_results: list[dict[str, Any]] = []
    total_count = 0
    if partial_path.exists():
        partial_payload = json.loads(partial_path.read_text(encoding="utf-8"))
        field_results = partial_payload.get("results", [])
        total_count = int(partial_payload.get("count", 0))

    while True:
        offset = len(field_results)
        if total_count and offset >= total_count:
            break
        params = dict(FIELD_QUERY)
        params.update({"limit": str(FIELD_PAGE_SIZE), "offset": str(offset)})
        page = fetch_json(target_id, f"/data-fields?{urllib.parse.urlencode(params)}")
        if not isinstance(page, dict):
            raise RuntimeError("Unexpected data-fields payload.")
        total_count = int(page.get("count", 0))
        results = page.get("results", [])
        if not isinstance(results, list) or not results:
            break
        field_results.extend([row for row in results if isinstance(row, dict)])
        write_json(
            partial_path,
            {
                "syncedAt": utc_now(),
                "query": {**FIELD_QUERY, "limit": FIELD_PAGE_SIZE},
                "count": total_count,
                "results": field_results,
            },
        )
        time.sleep(0.4)

    write_json(
        final_path,
        {
            "syncedAt": utc_now(),
            "query": {**FIELD_QUERY, "limit": FIELD_PAGE_SIZE},
            "count": total_count,
            "results": field_results,
        },
    )
    if partial_path.exists():
        partial_path.unlink()
    return len(field_results)


def fetch_catalog(target_id: str, fields_only: bool) -> dict[str, int]:
    counts: dict[str, int] = {"data_fields": fetch_data_fields(target_id)}
    if fields_only:
        return counts
    operators = fetch_json(target_id, "/operators")
    categories = fetch_json(target_id, "/data-categories")
    write_json(RAW / "operators.json", operators)
    write_json(RAW / "data-categories.json", categories)
    counts["operators"] = len(operators) if isinstance(operators, list) else 0
    counts["data_categories"] = len(categories) if isinstance(categories, list) else 0
    return counts


def render_data_fields() -> int:
    path = RAW / "data-fields-usa-delay1-top3000.json"
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    category_counts = Counter(str(item.get("category", {}).get("name", "Unknown")) for item in results if isinstance(item, dict))
    lines = ["# WorldQuant BRAIN Data Fields (USA / Delay 1 / TOP3000)", ""]
    lines.append(f"- `count`: `{payload.get('count', len(results))}`")
    lines.append(f"- `syncedAt`: `{payload.get('syncedAt', 'unknown')}`")
    lines.append("")
    lines.append("## Category Counts")
    lines.append("")
    lines.append("| Category | Fields |")
    lines.append("| --- | ---: |")
    for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {category} | {count} |")
    lines.append("")
    lines.append("## Top Fields By Alpha Count")
    lines.append("")
    lines.append("| Field | Dataset | Category | Alpha Count | Users | Coverage |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: |")
    top_by_alpha = sorted(
        [item for item in results if isinstance(item, dict)],
        key=lambda item: item.get("alphaCount", 0),
        reverse=True,
    )[:40]
    for item in top_by_alpha:
        lines.append(
            f"| `{item.get('id', '')}` | {item.get('dataset', {}).get('name', '')} | "
            f"{item.get('category', {}).get('name', '')} | {item.get('alphaCount', 0)} | "
            f"{item.get('userCount', 0)} | {item.get('coverage', 0)} |"
        )
    write_text(MARKDOWN / "data-fields-usa-delay1-top3000.md", "\n".join(lines))
    return len(results)


def render_optional_catalogs() -> dict[str, int]:
    counts = {"data_fields": render_data_fields(), "operators": 0, "data_categories": 0}
    operators_path = RAW / "operators.json"
    if operators_path.exists():
        operators = json.loads(operators_path.read_text(encoding="utf-8"))
        counts["operators"] = len(operators) if isinstance(operators, list) else 0
    categories_path = RAW / "data-categories.json"
    if categories_path.exists():
        categories = json.loads(categories_path.read_text(encoding="utf-8"))
        counts["data_categories"] = len(categories) if isinstance(categories, list) else 0
    return counts


def write_manifest(mode: str, raw_counts: dict[str, int], markdown_counts: dict[str, int], contract_check: dict[str, Any] | None) -> None:
    payload: dict[str, Any] = {
        "syncedAt": utc_now(),
        "mode": mode,
        "raw": raw_counts,
        "markdown": markdown_counts,
    }
    if contract_check is not None:
        payload["contractCheck"] = {
            "all_ok": contract_check.get("all_ok"),
            "path": str(HEALTHCHECKS / "contract-check.json"),
        }
    write_json(SOURCE_ROOT / "sync-manifest.json", payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync public Factor Factory metadata from a user's WorldQuant BRAIN session.")
    parser.add_argument("--fields-only", action="store_true", help="Only fetch the USA/D1/TOP3000 data-field catalog.")
    parser.add_argument("--skip-fetch", action="store_true", help="Only render markdown from existing raw files.")
    parser.add_argument("--contract-check-only", action="store_true", help="Only verify the read-only WorldQuant API contract.")
    parser.add_argument("--target-id", help="Explicit browser target id. Defaults to auto-detecting an open WorldQuant tab.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    MARKDOWN.mkdir(parents=True, exist_ok=True)
    HEALTHCHECKS.mkdir(parents=True, exist_ok=True)

    if args.contract_check_only:
        target_id = args.target_id or find_worldquant_target_id()
        report = run_read_only_contract_check(target_id)
        write_json(HEALTHCHECKS / "contract-check.json", report)
        write_manifest("contract_check_only", {}, render_optional_catalogs(), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("all_ok") else 1

    contract_check = None
    if args.skip_fetch:
        raw_counts = {
            "data_fields": len(json.loads((RAW / "data-fields-usa-delay1-top3000.json").read_text(encoding="utf-8")).get("results", []))
            if (RAW / "data-fields-usa-delay1-top3000.json").exists()
            else 0
        }
    else:
        target_id = args.target_id or find_worldquant_target_id()
        contract_check = run_read_only_contract_check(target_id)
        write_json(HEALTHCHECKS / "contract-check.json", contract_check)
        raw_counts = fetch_catalog(target_id, fields_only=args.fields_only)

    markdown_counts = render_optional_catalogs()
    mode = "render_only" if args.skip_fetch else ("fields_only" if args.fields_only else "full_sync")
    write_manifest(mode, raw_counts, markdown_counts, contract_check)
    print(json.dumps({"raw": raw_counts, "markdown": markdown_counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
