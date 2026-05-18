from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILER_PATH = ROOT / "scripts" / "datafield_profiler.py"
COURSE_GATE_PATH = ROOT / "scripts" / "verify_official_course_read_gate.py"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PublicBootstrapAssetsTest(unittest.TestCase):
    def test_starter_fields_load_when_official_raw_file_is_absent(self) -> None:
        module = load_module("datafield_profiler_public_test", PROFILER_PATH)

        fields = module.fields_by_id(path=pathlib.Path("/tmp/factor-factory-missing-fields.json"))

        self.assertIn("est_eps", fields)
        self.assertEqual(fields["est_eps"]["dataset"]["id"], "analyst4")
        self.assertEqual(fields["sales"]["dataset"]["id"], "fundamental6")
        self.assertEqual(fields["volume"]["dataset"]["id"], "pv1")

    def test_public_course_gate_does_not_require_private_raw_course_bundle(self) -> None:
        module = load_module("course_gate_public_test", COURSE_GATE_PATH)

        payload = module.build_payload()

        self.assertTrue(payload["confirmed"])
        self.assertEqual(payload["summary"]["full_read_status"], "PUBLIC_BOOTSTRAP_CONFIRMED")
        self.assertEqual(payload["status"], "PASS")

    def test_generate_profile_stage2_pool_cli_works_from_generated_selections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "factor-factory-clean"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "distribution" / "build_package.py"),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    "scripts/run_v15_local_cycle.py",
                    "--run-id",
                    "cli-smoke",
                    "--candidate-limit",
                    "1",
                ],
                cwd=output,
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_profile_stage2_pool.py",
                    "--run-id",
                    "cli-smoke-extra",
                    "--analyst-selection-run-id",
                    "cli-smoke-select-analyst4",
                    "--fundamental-selection-run-id",
                    "cli-smoke-select-fundamental6",
                    "--pv-selection-run-id",
                    "cli-smoke-select-pv1",
                    "--limit",
                    "2",
                    "--dry-run",
                ],
                cwd=output,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)

            self.assertEqual(payload["planned_count"], 2)
            self.assertEqual(payload["generated_count"], 0)
            self.assertTrue(payload["manual_gate_required"])


if __name__ == "__main__":
    unittest.main()
