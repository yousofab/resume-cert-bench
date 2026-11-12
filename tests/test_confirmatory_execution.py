import unittest

from resumecert.confirmatory.design import build_design, load_plan
from resumecert.confirmatory.execution import cases_for, schedule, summarize
from resumecert.records import Decision


class ConfirmatoryExecutionTests(unittest.TestCase):
    def test_registered_schedule_includes_one_outside_budget_reference_per_snapshot(self):
        plan = load_plan()
        specs, pairs, controls = build_design(plan)
        paired, separate = cases_for(pairs, controls)
        contracts = {(spec.name, "primary"): spec.contract for spec in specs}
        cells = tuple(schedule(plan, paired, separate, contracts))
        self.assertEqual(len(cells), 3648)
        self.assertEqual(len({(mode, case.snapshot_id, budget, policy)
                              for mode, case, budget, policy in cells}), len(cells))
        full = [(case, budget) for mode, case, budget, _ in cells if mode == "full_read"]
        self.assertEqual({case.snapshot_id for case, _ in full}, {c.snapshot_id for c in paired})
        self.assertTrue(all(budget == len(contracts[case.family, "primary"].queries)
                            for case, budget in full))

    def test_diagnostics_use_truth_denominators_and_count_unchanged_prefixes(self):
        common = {"key": "synthetic-pair", "budget": 2, "certified": False}
        wrong = dict(common, truth=Decision.BLOCK, claimed=Decision.ALLOW, verified=False,
                     spent=0, hindsight_floor=3, compatible_worlds_by_prefix=[4])
        deferred = dict(common, truth=Decision.ALLOW, claimed=Decision.DEFER, verified=True,
                        spent=2, hindsight_floor=2, compatible_worlds_by_prefix=[4, 4, 2])
        stats = summarize([wrong, deferred], paired=True)
        self.assertEqual(stats["raw_false_allow_per_blocked"], {"numerator": 1, "denominator": 1})
        self.assertEqual(stats["raw_false_allow_per_case"], {"numerator": 1, "denominator": 2})
        self.assertEqual(stats["rejected_per_submitted"], {"numerator": 1, "denominator": 2})
        self.assertEqual(stats["verified_deferral_per_allowed"], {"numerator": 1, "denominator": 1})
        self.assertEqual(stats["noninformative_reads"], 1)
        self.assertEqual(stats["floor_above_budget"], 1)
        self.assertEqual(stats["affordable_verified_deferrals"], 1)
        self.assertIsNone(summarize([deferred])["raw_false_allow_per_blocked"])
