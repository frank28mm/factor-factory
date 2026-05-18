from __future__ import annotations

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "refresh_pending_runs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("refresh_pending_runs", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RefreshPendingRunsStopReasonTest(unittest.TestCase):
    def test_failed_alpha_detail_does_not_stop_batch(self) -> None:
        module = load_module()
        event = {
            "steps": [
                {"name": "result", "classification": "fetched"},
                {"name": "alpha_detail", "classification": "failed"},
            ]
        }

        self.assertIsNone(module.stop_reason(event))

    def test_rate_limited_still_stops_batch(self) -> None:
        module = load_module()
        event = {
            "steps": [
                {"name": "result", "classification": "rate_limited"},
            ]
        }

        self.assertEqual(module.stop_reason(event), "rate_limited")


if __name__ == "__main__":
    unittest.main()
