import copy
from dataclasses import replace
import json
import unittest

from resumecert.confirmatory.design import Pair, audit_design, build_design, load_plan
from resumecert.confirmatory.scoring import PrimaryRecord, score_primary
from resumecert.records import Decision
from scripts.verify_freeze import MANIFEST, verify


class ConfirmatoryDesignTests(unittest.TestCase):
    def test_public_commitment_reproduces_without_policy_runs(self):
        manifest = json.loads(MANIFEST.read_bytes())
        result = verify(manifest)
        self.assertEqual((result["held_out_pairs"], result["controls"], result["held_out_policy_runs"]),
                         (48, 8, 0))
        for name in ("resumecert/pilot/policies.py", "study/confirmatory-v1/plan.json"):
            tampered = copy.deepcopy(manifest)
            tampered["sha256"][name] = "0" * 64
            with self.assertRaisesRegex(ValueError, "frozen source hash mismatch"):
                verify(tampered)

    def test_wrong_control_arm_and_fixture_commitment_fail(self):
        p = load_plan()
        fs, pairs, controls = build_design(p)
        with self.assertRaisesRegex(ValueError, "pair labels or shared public context"):
            audit_design(p, fs, (replace(pairs[0], benign=pairs[0].pivotal), *pairs[1:]), controls)
        manifest = json.loads(MANIFEST.read_bytes())
        manifest["design_audit"]["fixture_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "design-audit commitment changed"):
            verify(manifest)

    def test_scorer_requires_all_cells_and_respects_strata(self):
        # Deliberately invented mini table; no generated test case or policy run.
        q = (("vote_a", 1), ("vote_b", 0), ("vote_c", 0), ("vote_d", 0), ("required_yes", 1))
        c = (("capacity", 2), ("committed", 0), ("request_size", 1), ("service_open", 1))
        p1 = Pair("quorum_release", "toy-q", q, True, "low", "", "", "",
                  q, q, "q0", "q1")
        p2 = Pair("capacity_reservation", "toy-c", c, True, "low", "", "", "",
                  c, c, "c0", "c1")
        plan = load_plan()
        plan["primary"]["denominator"] = 4
        rows = [
            PrimaryRecord(sid, policy, 2,
                          Decision.ALLOW if sid == "q0" or sid == "q1" and policy == "adaptive"
                          else Decision.DEFER, True, 2)
            for sid in ("q0", "q1", "c0", "c1") for policy in ("adaptive", "fixed")
        ]
        scored = score_primary(plan, (p1, p2), rows)
        self.assertEqual(scored["interpretation"], "mixed")
        self.assertEqual(scored["pooled"]["discordance"],
                         {"both": 1, "a_only": 1, "f_only": 0, "neither": 2})
        self.assertEqual((scored["pooled"]["delta_numerator"], scored["pooled"]["delta_denominator"]),
                         (1, 4))
        self.assertEqual(scored["pooled"]["pairs_fully_certified"], {"adaptive": 1, "fixed": 0})
        for bad in (rows[:-1], rows + [rows[0]], [replace(rows[0], spent=3), *rows[1:]],
                    [replace(rows[0], verified=False), *rows[1:]],
                    [replace(rows[0], claimed=Decision.BLOCK), *rows[1:]]):
            with self.assertRaises(ValueError):
                score_primary(plan, (p1, p2), bad)
