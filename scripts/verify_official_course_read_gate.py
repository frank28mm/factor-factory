#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[0]
RAW_COURSE = PROJECT_ROOT / "research" / "raw" / "wq-official-public-course-2026-05-17"
TRANSCRIPT = RAW_COURSE / "transcript_whisper_small" / "course_timestamped.md"
KEY_EXCERPTS = RAW_COURSE / "transcript_whisper_small" / "key_excerpts.md"
OCR_MD = RAW_COURSE / "ocr" / "keyframes_ocr.md"
OCR_JSON = RAW_COURSE / "ocr" / "keyframes_ocr.json"
KEYFRAMES_DIR = RAW_COURSE / "keyframes"
AUDIT_DOC = ROOT / "docs" / "wq-official-course-full-read-audit-2026-05-17.md"
PUBLIC_GATE_DOC = ROOT / "docs" / "wq-official-course-public-gate.md"

EXPECTED = {
    "transcript_lines": 2798,
    "key_excerpts_lines": 92,
    "keyframes_ocr_lines": 1395,
    "keyframes_count": 62,
    "ocr_json_count": 62,
    "full_read_status": "FULL_READ_CONFIRMED",
}


def line_count(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_status(text: str) -> str:
    match = re.search(r"状态：([A-Z_]+)", text)
    return match.group(1) if match else ""


def check(name: str, actual: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "actual": actual,
        "expected": expected,
        "ok": actual == expected,
    }


def build_public_payload() -> dict[str, Any]:
    gate_text = PUBLIC_GATE_DOC.read_text(encoding="utf-8")
    status = audit_status(gate_text)
    summary = {
        "full_read_status": status,
        "mode": "public_bootstrap",
        "audit_doc": str(PUBLIC_GATE_DOC),
        "private_raw_course_bundle_included": False,
        "live_platform_actions": False,
    }
    checks = [
        check("public_gate_status", summary["full_read_status"], "PUBLIC_BOOTSTRAP_CONFIRMED"),
        check("private_raw_course_bundle_included", summary["private_raw_course_bundle_included"], False),
        check("live_platform_actions", summary["live_platform_actions"], False),
    ]
    confirmed = all(row["ok"] for row in checks)
    return {
        "status": "PASS" if confirmed else "FAIL",
        "confirmed": confirmed,
        "summary": summary,
        "checks": checks,
    }


def build_payload() -> dict[str, Any]:
    if not AUDIT_DOC.exists() or not RAW_COURSE.exists():
        if not PUBLIC_GATE_DOC.exists():
            raise FileNotFoundError(
                f"Missing private audit doc {AUDIT_DOC} and public gate doc {PUBLIC_GATE_DOC}"
            )
        return build_public_payload()
    audit_text = AUDIT_DOC.read_text(encoding="utf-8")
    ocr_payload = read_json(OCR_JSON)
    keyframes_count = len(list(KEYFRAMES_DIR.glob("*.jpg")))
    contact_sheets_verified = all(
        token in audit_text
        for token in (
            "/tmp/wq-course-contact-sheets/keyframes_01_32.jpg",
            "/tmp/wq-course-contact-sheets/keyframes_33_62.jpg",
        )
    )
    summary = {
        "full_read_status": audit_status(audit_text),
        "transcript_lines": line_count(TRANSCRIPT),
        "key_excerpts_lines": line_count(KEY_EXCERPTS),
        "keyframes_ocr_lines": line_count(OCR_MD),
        "keyframes_count": keyframes_count,
        "ocr_json_count": len(ocr_payload) if isinstance(ocr_payload, list) else None,
        "contact_sheets_verified": contact_sheets_verified,
        "audit_doc": str(AUDIT_DOC),
    }
    checks = [
        check("full_read_status", summary["full_read_status"], EXPECTED["full_read_status"]),
        check("transcript_lines", summary["transcript_lines"], EXPECTED["transcript_lines"]),
        check("key_excerpts_lines", summary["key_excerpts_lines"], EXPECTED["key_excerpts_lines"]),
        check("keyframes_ocr_lines", summary["keyframes_ocr_lines"], EXPECTED["keyframes_ocr_lines"]),
        check("keyframes_count", summary["keyframes_count"], EXPECTED["keyframes_count"]),
        check("ocr_json_count", summary["ocr_json_count"], EXPECTED["ocr_json_count"]),
        check("contact_sheets_verified", summary["contact_sheets_verified"], True),
    ]
    confirmed = all(row["ok"] for row in checks)
    return {
        "status": "PASS" if confirmed else "FAIL",
        "confirmed": confirmed,
        "summary": summary,
        "checks": checks,
    }


def main() -> int:
    payload = build_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["confirmed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
