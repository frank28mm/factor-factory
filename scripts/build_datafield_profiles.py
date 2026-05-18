#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from datafield_profiler import PROFILE_DIR, build_profiles, profile_summary, safe_slug, write_profile_assets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build static WorldQuant datafield profiles for Factor Factory.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--field-id", action="append", default=[], help="Field id to profile. Can be repeated.")
    parser.add_argument("--limit", type=int, help="Optional cap when profiling all known fields.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = safe_slug(args.run_id)
    field_ids = list(args.field_id)
    profiles = build_profiles(field_ids, run_id)
    if args.limit is not None:
        profiles = profiles[: max(0, int(args.limit))]
    write_profile_assets(profiles, run_id)
    summary = profile_summary(profiles)
    payload = {
        "run_id": run_id,
        "profile_dir": str(PROFILE_DIR),
        **summary,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
