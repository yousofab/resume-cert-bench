from dataclasses import replace
from pathlib import Path
import json
import unittest

from resumecert.demo import ROUTED_APPROVAL
from resumecert.pilot.fixtures import matched_cases, validate
from resumecert.pilot.policies import planner
from resumecert.pilot.study import study


class MatchedPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results, _, cls.rows = study()

    def test_matching_validator_catches_branch_leakage_and_unmatched_delta(self):
        cases = matched_cases()
        self.assertEqual(len(validate(cases)), 24)
        for bad in (replace(cases[0], surface="leak"), replace(cases[0], live=cases[0].before)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate((bad,) + cases[1:])

    def test_exact_tree_matches_independently_enumerated_witness(self):
        p = planner(ROUTED_APPROVAL, ())
        expected = json.loads((Path(__file__).resolve().parents[1] / "proof/witness_result.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual([p.tree(p.start, tuple(range(3)), b)[0] for b in range(4)],
                         [r["best_adaptive_certified"] for r in expected["table"]])

    def test_strata_denominators_and_full_read_reference(self):
        r = self.results
        for p, overall in r["primary"]["2"].items():
            for dim, n in (("family", 16), ("surface", 24), ("arm", 24)):
                parts = [part[p] for part in r[f"budget_2_by_{dim}"].values()]
                self.assertTrue(all(part["n"] == n for part in parts))
                for key in ("certified_allow", "certified_block", "total_read_cost"):
                    self.assertEqual(sum(part[key] for part in parts), overall[key])
        self.assertEqual(r["full_read"]["pairs_both_certified"], 24)
        self.assertEqual(r["full_read"]["rejected_claims"], 0)

    def test_budget_and_certificate_soundness(self):
        self.assertTrue(all(r["spent"] <= r["budget"] for r in self.rows))
        self.assertTrue(all(r["claimed"] == r["truth"] for r in self.rows if r["certified"]))
        controls = self.results["controls"]
        self.assertEqual(controls["uninterrupted_current"]["0"]["fixed"]["pairs_both_certified"], None)
        self.assertEqual(controls["uninterrupted_current"]["0"]["fixed"]["certified_allow"], 3)
        self.assertEqual(controls["uninterrupted_current"]["0"]["fixed"]["certified_block"], 3)
        self.assertEqual(controls["interrupted_unchanged"]["0"]["fixed"]["verified_deferrals"], 6)
