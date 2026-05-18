#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "distribution" / "package_manifest.json"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def status(ok: bool, name: str, detail: str = "") -> dict[str, Any]:
    return {"ok": ok, "name": name, "detail": detail}


def check_python() -> dict[str, Any]:
    version = sys.version_info
    ok = version >= (3, 10)
    return status(ok, "python>=3.10", f"{version.major}.{version.minor}.{version.micro}")


def check_module(module_name: str, import_name: str | None = None) -> dict[str, Any]:
    name = import_name or module_name
    found = importlib.util.find_spec(name) is not None
    return status(found, f"module:{module_name}", "installed" if found else "missing")


def check_dirs(manifest: dict[str, Any]) -> dict[str, Any]:
    missing = []
    for rel in manifest.get("state_dirs", []):
        path = ROOT / rel
        try:
            path.mkdir(parents=True, exist_ok=True)
            marker = path / ".write-test"
            marker.write_text("ok", encoding="utf-8")
            marker.unlink()
        except OSError as exc:
            missing.append(f"{rel}: {exc}")
    return status(not missing, "state_dirs_writable", "; ".join(missing))


def check_blocked_markers(manifest: dict[str, Any]) -> dict[str, Any]:
    markers = list(manifest.get("blocked_text_markers", []))
    allowlist = set(manifest.get("blocked_marker_allowlist", []))
    suffixes = {".py", ".sh", ".ps1", ".md", ".yaml", ".yml", ".json", ".example", ".txt"}
    offenders = []
    paths: list[pathlib.Path] = []
    for pattern in manifest.get("include_files", []):
        paths.extend(path for path in ROOT.glob(pattern) if path.is_file())
    for directory in manifest.get("include_dirs", []):
        base = ROOT / directory
        if base.exists():
            paths.extend(path for path in base.rglob("*") if path.is_file())
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowlist:
            continue
        if rel.startswith(("state/", "state.", ".git/")) or "__pycache__" in rel:
            continue
        if path.suffix not in suffixes and path.name != "env.example":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in markers:
            if marker in text:
                offenders.append(f"{rel}: {marker}")
    return status(not offenders, "no_personal_path_markers", "\n".join(offenders[:20]))


def check_dashboard_dry() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "export_visual_ledger.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:  # pragma: no cover - defensive runtime branch
        return status(False, "dashboard_export", str(exc))
    return status(result.returncode == 0, "dashboard_export", result.stderr.strip() or result.stdout.strip())


def check_session(skip_live: bool) -> dict[str, Any]:
    if skip_live:
        return status(True, "wq_session", "skipped")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_wq_session.py"), "--run-id", "doctor-session"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return status(False, "wq_session", result.stderr.strip() or result.stdout.strip())
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return status(False, "wq_session", result.stdout.strip())
    return status(bool(payload.get("authenticated")), "wq_session", json.dumps(payload, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Factor Factory portable release doctor.")
    parser.add_argument("--skip-live-session", action="store_true", help="Do not call the logged-in browser/WQ session check.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    manifest = read_json(MANIFEST)
    checks = [
        check_python(),
        check_module("PyYAML", "yaml"),
        check_dirs(manifest),
        check_blocked_markers(manifest),
        check_dashboard_dry(),
        check_session(args.skip_live_session),
    ]
    payload = {"ok": all(item["ok"] for item in checks), "checks": checks}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            mark = "OK" if item["ok"] else "FAIL"
            print(f"[{mark}] {item['name']} {item['detail']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
