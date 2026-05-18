#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
import shutil
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "distribution" / "package_manifest.json"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


def excluded(relative_path: str, manifest: dict[str, Any]) -> bool:
    excluded_dirs = tuple(manifest.get("exclude_dirs", []))
    if relative_path.startswith(excluded_dirs):
        return True
    parts = relative_path.split("/")
    if "__pycache__" in parts:
        return True
    for pattern in manifest.get("exclude_globs", []):
        if fnmatch.fnmatch(relative_path, pattern) or pathlib.PurePosixPath(relative_path).match(pattern):
            return True
    return False


def selected_files(manifest: dict[str, Any]) -> list[pathlib.Path]:
    files: set[pathlib.Path] = set()
    for pattern in manifest.get("include_files", []):
        for path in ROOT.glob(pattern):
            if path.is_file():
                files.add(path)
    for directory in manifest.get("include_dirs", []):
        base = ROOT / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                files.add(path)
    return [path for path in sorted(files) if not excluded(rel(path), manifest)]


def assert_clean_text(paths: list[pathlib.Path], manifest: dict[str, Any]) -> None:
    suffixes = {".py", ".sh", ".ps1", ".md", ".yaml", ".yml", ".json", ".example", ".txt"}
    allowlist = set(manifest.get("blocked_marker_allowlist", []))
    offenders = []
    for path in paths:
        relative = rel(path)
        if relative in allowlist:
            continue
        if path.suffix not in suffixes and path.name != "env.example":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in manifest.get("blocked_text_markers", []):
            if marker in text:
                offenders.append(f"{relative} contains {marker}")
    if offenders:
        raise SystemExit("Refusing to build package with personal markers:\n" + "\n".join(offenders[:50]))


def write_release_root_files(output: pathlib.Path) -> None:
    readme = output / "README.md"
    source_readme = output / "distribution" / "README.md"
    if source_readme.exists():
        readme.write_text(source_readme.read_text(encoding="utf-8"), encoding="utf-8")
    gitignore = output / ".gitignore"
    gitignore.write_text(
        "\n".join(
            [
                "# Runtime state and user account/session artifacts",
                "state/**",
                "!state/**/",
                "!state/**/.gitkeep",
                "state.*/",
                "launchd/",
                "",
                "# Python/cache/editor artifacts",
                "__pycache__/",
                "*.py[cod]",
                ".pytest_cache/",
                ".mypy_cache/",
                ".DS_Store",
                "*.log",
                "*.tmp",
                ".env",
                ".env.*",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a clean Factor Factory distribution tree.")
    parser.add_argument("--output", required=True, help="Output directory. Existing directory is replaced.")
    args = parser.parse_args()

    manifest = read_json(MANIFEST)
    paths = selected_files(manifest)
    assert_clean_text(paths, manifest)

    output = pathlib.Path(args.output).expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for path in paths:
        target = output / rel(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    for directory in manifest.get("state_dirs", []):
        state_dir = output / directory
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / ".gitkeep").write_text("", encoding="utf-8")

    write_release_root_files(output)

    print(json.dumps({"output": str(output), "files": len(paths), "state_dirs": len(manifest.get("state_dirs", []))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
