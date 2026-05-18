from __future__ import annotations

import importlib.util
import pathlib
import unittest
from datetime import datetime, timedelta, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "run_wq_sync_loop.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_wq_sync_loop", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WqSyncLoopProbeCooldownTest(unittest.TestCase):
    def test_probe_rate_limit_sets_probe_cooldown_without_global_stop(self) -> None:
        module = load_module()
        payload: dict[str, object] = {}
        stopped = {"stage": "probe_batch", "reason": "rate_limited"}
        created_at = datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)

        handled = module.apply_stage_cooldown(
            payload,
            cycle_index=5,
            stopped=stopped,
            cooldown_seconds=120,
            created_at=created_at,
        )

        self.assertTrue(handled)
        self.assertNotIn("stopped", payload)
        self.assertEqual(payload["probe_cooldown"]["cycle"], 5)
        self.assertEqual(payload["probe_cooldown"]["stage"], "probe_batch")
        self.assertEqual(payload["probe_cooldown"]["cooldown_seconds"], 120)

    def test_probe_launch_is_suppressed_during_probe_cooldown(self) -> None:
        module = load_module()
        now = datetime(2026, 5, 18, 3, 1, tzinfo=timezone.utc)
        cooldown_until = (now + timedelta(seconds=60)).isoformat()
        probe_cooldown = {"cooldown_until": cooldown_until}

        self.assertTrue(module.probe_cooldown_active(probe_cooldown, now))
        self.assertFalse(module.probe_cooldown_active(probe_cooldown, now + timedelta(seconds=61)))

    def test_session_watchdog_classifies_valid_user_session(self) -> None:
        module = load_module()
        event = module.classify_session_watchdog_response({"status": 200, "payload": {"id": "user-1"}})

        self.assertEqual(event["classification"], "authenticated")
        self.assertTrue(event["authenticated"])

    def test_session_watchdog_classifies_auth_required(self) -> None:
        module = load_module()
        event = module.classify_session_watchdog_response({"status": 401, "payload": None})

        self.assertEqual(event["classification"], "auth_required")
        self.assertFalse(event["authenticated"])

    def test_auth_required_session_suppresses_live_cycle_actions(self) -> None:
        module = load_module()
        cycle = {"run_id": "cycle-1", "started_at": "2026-05-18T03:00:00+00:00"}
        session_event = {"classification": "auth_required", "authenticated": False}

        module.apply_session_watchdog_to_cycle(cycle, session_event)

        self.assertEqual(cycle["session_state"], "auth_required")
        self.assertEqual(cycle["pending_refresh"]["reason"], "session_auth_required")
        self.assertEqual(cycle["submit_ready"]["reason"], "session_auth_required")
        self.assertEqual(cycle["probe_batch"]["reason"], "session_auth_required")
        self.assertEqual(cycle["open_slots"], 0)


if __name__ == "__main__":
    unittest.main()
