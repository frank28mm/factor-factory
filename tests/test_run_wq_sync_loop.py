from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


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

    def test_default_continuous_interval_is_fast_polling(self) -> None:
        module = load_module()
        original_argv = sys.argv[:]
        try:
            sys.argv = ["run_wq_sync_loop.py"]
            args = module.parse_args()
        finally:
            sys.argv = original_argv

        self.assertEqual(args.interval_seconds, 15)

    def test_submission_gate_lock_does_not_stop_when_real_quota_remains(self) -> None:
        module = load_module()

        stopped = module.submission_quota_stop(
            {
                "remaining_submission_quota": 1,
                "submission_gate_locked": True,
            }
        )

        self.assertIsNone(stopped)

    def test_submission_quota_stops_only_when_real_remaining_quota_is_zero(self) -> None:
        module = load_module()

        stopped = module.submission_quota_stop(
            {
                "remaining_submission_quota": 0,
                "submission_gate_locked": False,
            }
        )

        self.assertEqual(stopped, {"stage": "submission_quota", "reason": "remaining_submission_quota_zero"})

    def test_continuous_loop_keeps_running_when_submission_quota_is_zero(self) -> None:
        module = load_module()

        self.assertTrue(module.continuous_should_continue_after_stop({"stage": "submission_quota", "reason": "remaining_submission_quota_zero"}))
        self.assertTrue(module.continuous_should_continue_after_stop({"stage": "submission_quota", "reason": "submission_gate_locked"}))
        self.assertFalse(module.continuous_should_continue_after_stop({"stage": "pending_refresh", "reason": "auth_required"}))

    def test_positive_submit_limit_runs_submit_ready_stage(self) -> None:
        module = load_module()
        commands: list[str] = []

        args = SimpleNamespace(
            dry_run=False,
            offline_plan=False,
            max_running=0,
            submit_ready_limit=4,
            pending_refresh_limit=0,
            waiting_refresh_limit=0,
            probe_batch_limit=0,
            auto_replenish=False,
            pool_id=None,
            batch_id=None,
            target_id=None,
            profile_replenish=True,
            replenish_min_ready=0,
            replenish_batch_size=0,
            replenish_pool_id="tp-stage3-analyst-earnings-event-reset-v0",
            fallback_replenish_pool_id=[],
            replenish_batch_prefix="test",
            run_id="submit-test",
            max_cycles=1,
            no_sleep=True,
            interval_seconds=15,
            rate_limit_cooldown_seconds=600,
            probe_rate_limit_cooldown_seconds=180,
        )

        def fake_run_json(command: list[str]) -> dict[str, object]:
            command_text = " ".join(command)
            commands.append(command_text)
            if "check_wq_session.py" in command_text:
                return {"classification": "authenticated", "authenticated": True, "status": 200}
            if "submit_ready_alphas.py" in command_text:
                return {"selected_count": 2, "submitted_count": 2}
            return {}

        module.run_json = fake_run_json
        module.maintenance = lambda: []
        module.pending_count = lambda: 0
        module.submission_quota_status = lambda: {"remaining_submission_quota": 4, "submission_gate_locked": False}
        module.maybe_replenish = lambda _args, _run_id: {"enabled": False, "generated_count": 0}

        cycle = module.run_cycle(args, "auto-submit-test")

        self.assertTrue(cycle["auto_submit"])
        self.assertEqual(cycle["submit_ready"]["selected_count"], 2)
        self.assertTrue(any("submit_ready_alphas.py" in command for command in commands))


class PublicStartupEntrypointTest(unittest.TestCase):
    def test_root_start_script_exposes_continuous_entrypoint_without_legacy_once(self) -> None:
        script = ROOT / "start_factor_factory.sh"
        content = script.read_text(encoding="utf-8")

        self.assertTrue(script.exists())
        self.assertIn("start-continuous", content)
        self.assertIn("stop-continuous", content)
        self.assertIn("status", content)
        self.assertIn("distribution/run_macos.sh", content)
        self.assertIn("--submit-ready-limit 4", content)
        self.assertNotIn("run_wq_sync_once.sh", content)
        self.assertNotIn("install-agent", content)
        self.assertNotIn("start-agent", content)
        self.assertNotIn("stop-agent", content)

    def test_macos_distribution_loop_is_fast_and_auto_submit_enabled(self) -> None:
        content = (ROOT / "distribution" / "run_macos.sh").read_text(encoding="utf-8")

        self.assertIn('FACTOR_FACTORY_INTERVAL_SECONDS:-15', content)
        self.assertIn('FACTOR_FACTORY_SUBMIT_READY_LIMIT:-4', content)
        self.assertIn("--max-cycles 0", content)
        self.assertIn("--submit-ready-limit", content)

    def test_legacy_five_minute_once_script_is_removed_from_repo(self) -> None:
        self.assertFalse((ROOT / "scripts" / "run_wq_sync_once.sh").exists())


if __name__ == "__main__":
    unittest.main()
