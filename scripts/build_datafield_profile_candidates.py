#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from datafield_profiler import (
    PROFILE_PROBE_DIR,
    build_profile_probes,
    load_profiles,
    safe_slug,
    write_probe_assets,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build non-submittable datafield profile probe candidates.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--field-id", action="append", default=[], help="Profiled field id to build probes for. Can be repeated.")
    parser.add_argument("--limit-per-field", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = safe_slug(args.run_id)
    profiles = load_profiles(args.field_id)
    missing = sorted(set(args.field_id) - {str(profile.get("field_id")) for profile in profiles})
    if missing:
        raise FileNotFoundError(f"Missing datafield profile(s): {', '.join(missing)}")
    probes = build_profile_probes(profiles, run_id, args.limit_per_field)
    write_probe_assets(probes, run_id)
    payload = {
        "run_id": run_id,
        "probe_dir": str(PROFILE_PROBE_DIR),
        "profile_count": len(profiles),
        "probe_count": len(probes),
        "submit_allowed": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
