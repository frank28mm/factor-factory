from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "distribution"
MANIFEST = DIST / "package_manifest.json"


class DistributionPackageTest(unittest.TestCase):
    def load_manifest(self) -> dict:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def expand_manifest_files(self, manifest: dict) -> list[pathlib.Path]:
        files: set[pathlib.Path] = set()
        for pattern in manifest["include_files"]:
            for path in ROOT.glob(pattern):
                if path.is_file():
                    files.add(path)
        for directory in manifest["include_dirs"]:
            base = ROOT / directory
            for path in base.rglob("*"):
                if path.is_file():
                    files.add(path)
        excluded_dirs = tuple(manifest["exclude_dirs"])
        excluded_globs = tuple(manifest["exclude_globs"])
        selected = []
        for path in sorted(files):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(excluded_dirs):
                continue
            if any(path.match(pattern) or rel.startswith(pattern.rstrip("/")) for pattern in excluded_globs):
                continue
            selected.append(path)
        return selected

    def marker_allowlist(self, manifest: dict) -> set[str]:
        return set(manifest.get("blocked_marker_allowlist", []))

    def test_distribution_entrypoints_exist(self) -> None:
        required = [
            "distribution/README.md",
            "distribution/env.example",
            "distribution/install_macos.sh",
            "distribution/install_windows.ps1",
            "distribution/run_macos.sh",
            "distribution/run_windows.ps1",
            "distribution/doctor.py",
            "distribution/build_package.py",
            "distribution/package_manifest.json",
        ]

        for rel in required:
            self.assertTrue((ROOT / rel).exists(), rel)

    def test_manifest_excludes_personal_state_and_generated_files(self) -> None:
        manifest = self.load_manifest()

        self.assertIn("state/", manifest["exclude_dirs"])
        self.assertIn("state.", manifest["exclude_dirs"])
        self.assertIn("__pycache__/", manifest["exclude_dirs"])
        self.assertIn("*.pyc", manifest["exclude_globs"])
        self.assertIn(".DS_Store", manifest["exclude_globs"])
        self.assertIn("launchd/", manifest["exclude_dirs"])

    def test_manifest_sources_do_not_contain_personal_paths(self) -> None:
        manifest = self.load_manifest()
        blocked = manifest["blocked_text_markers"]
        scanned_suffixes = {".py", ".sh", ".ps1", ".md", ".yaml", ".yml", ".json", ".example", ".txt"}
        allowlist = self.marker_allowlist(manifest)

        offenders: list[str] = []
        for path in self.expand_manifest_files(manifest):
            relative = path.relative_to(ROOT).as_posix()
            if relative in allowlist:
                continue
            if path.suffix not in scanned_suffixes and path.name != "env.example":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in blocked:
                if marker in text:
                    offenders.append(f"{relative} contains {marker}")
        self.assertEqual(offenders, [])

    def test_build_package_creates_clean_install_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "factor-factory-clean"
            subprocess.run(
                [
                    sys.executable,
                    str(DIST / "build_package.py"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue((output / "README.md").exists())
            self.assertTrue((output / ".gitignore").exists())
            self.assertIn("state/**", (output / ".gitignore").read_text(encoding="utf-8"))
            self.assertTrue((output / "distribution" / "doctor.py").exists())
            self.assertTrue((output / "scripts" / "run_wq_sync_loop.py").exists())
            self.assertEqual(sorted(path.name for path in (output / "state" / "audit").iterdir()), [".gitkeep"])
            self.assertEqual(sorted(path.name for path in (output / "state" / "ledger").iterdir()), [".gitkeep"])
            package_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in output.rglob("*")
                if path.is_file()
                and path.relative_to(output).as_posix() not in self.marker_allowlist(self.load_manifest())
                and path.suffix in {".py", ".sh", ".ps1", ".md", ".yaml", ".json", ".example"}
            )
            for marker in self.load_manifest()["blocked_text_markers"]:
                self.assertNotIn(marker, package_text)


if __name__ == "__main__":
    unittest.main()
