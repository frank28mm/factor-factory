from __future__ import annotations

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_ledgers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_ledgers", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProbePoolRuleVersionTest(unittest.TestCase):
    def test_old_candidate_without_current_rule_version_is_not_probe_ready(self) -> None:
        module = load_module()
        candidate = {
            "candidate_id": "old-cand",
            "status": "probe_blocked",
            "review_status": "needs_human_gate",
            "local_precheck": {"decision": "ready_for_manual_gate"},
            "params": {
                "task_pool_id": "profile-stage2-field-blend-v15",
                "task_pool_auto_submit": False,
                "seed_candidate_id": "seed",
                "pre_probe_gate_passed": True,
            },
        }

        self.assertFalse(module.is_probe_ready(candidate, None, {}, {}, {}))
        self.assertIn("legacy_candidate_rule_version", module.probe_rule_gate_failures(candidate))

    def test_current_profile_stage3_candidate_is_probe_ready(self) -> None:
        module = load_module()
        candidate = {
            "candidate_id": "new-cand",
            "status": "probe_blocked",
            "review_status": "needs_human_gate",
            "local_precheck": {"decision": "ready_for_manual_gate"},
            "params": {
                "generation_rule_version": module.CURRENT_GENERATION_RULE_VERSION,
                "task_pool_id": "profile-stage2-field-blend-v15",
                "task_pool_auto_submit": False,
                "seed_candidate_id": "seed",
                "pre_probe_gate_passed": True,
                "task_pool_variant_family": "profile_stage3_pv_gated_blend",
                "analyst_field": "est_eps",
                "fundamental_field": "sales",
                "pv_gate_field": "volume",
                "wq_neutralization": "SUBINDUSTRY",
                "wq_decay": 4,
                "wq_truncation": 0.08,
            },
        }

        seed_result = {
            "metrics": {"sharpe": 1.3, "fitness": 1.1, "turnover": 0.1},
            "failed_checks": [],
            "checks": [],
        }
        scoring = {
            "official_platform_gates": {
                "delay_1": {"sharpe_min": 1.25, "fitness_min": 1.0},
                "turnover_range": {"min": 0.01, "max": 0.7},
            }
        }

        self.assertTrue(module.is_probe_ready(candidate, None, {}, {"seed": seed_result}, scoring))
        self.assertEqual(module.probe_rule_gate_failures(candidate), [])


if __name__ == "__main__":
    unittest.main()
