#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from datafield_profiler import select_fields, safe_slug


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select profiled datafields for task-pool generation.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset-id", help="Only select fields from this WorldQuant dataset id.")
    parser.add_argument("--lane", default="mainline_ready", help="Profile lane required by the generation gate.")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = select_fields(args.dataset_id, args.lane, args.limit, safe_slug(args.run_id))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
