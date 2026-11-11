from dataclasses import replace
import unittest

from resumecert.contract import ActionContract, Query
from resumecert.demo import DOCUMENT_RELEASE, report
from resumecert.inference import infer
from resumecert.records import Decision
from resumecert.simulator import BudgetExceeded, Simulation
from resumecert.verifier import verify


class SimulatorTests(unittest.TestCase):
    def document(self, version=2, budget=2, run="first", snapshot="snapshot-a", **kwargs):
        return Simulation(DOCUMENT_RELEASE, {"version": version, "approved_version": 1},
                          snapshot_id=snapshot, run_id=run, budget=budget,
                          checkpoint="Version 1 was approved before interruption.",
                          resume_message="Resume the pending release.", **kwargs)

    def test_identical_history_does_not_determine_current_permission(self):
        a = self.document(version=1, run="other", snapshot="snapshot-b")
        b = self.document()
        self.assertEqual(a.context.checkpoint, b.context.checkpoint)
        self.assertEqual(a.context.resume_message, b.context.resume_message)
        for sim, expected in ((a, Decision.ALLOW), (b, Decision.BLOCK)):
            with self.subTest(expected=expected):
                blind = infer(sim.context)
                self.assertIs(blind.decision, Decision.DEFER)
                self.assertTrue(verify(sim.audit(), blind).accepted)
                evidence = (sim.read("version_and_approval"),)
                certificate = infer(sim.context, evidence)
                self.assertIs(certificate.decision, expected)
                self.assertTrue(verify(sim.audit(), certificate).accepted)

    def test_reads_charge_actual_cost_and_denials_are_non_mutating(self):
        sim = self.document()
        before = sim.audit()
        with self.assertRaises(ValueError):
            sim.read("undeclared")
        self.assertEqual(sim.audit(), before)
        first = sim.read("version")
        one = sim.audit()
        with self.assertRaises(BudgetExceeded):
            sim.read("version_and_approval")
        self.assertEqual(sim.audit(), one)
        second = sim.read("version")
        self.assertEqual((first.ordinal, second.ordinal, sim.audit().spent), (1, 2, 2))
        cert = infer(sim.context, (first, second))
        self.assertIs(cert.decision, Decision.DEFER)
        self.assertTrue(verify(sim.audit(), cert).accepted)

    def test_forged_claims_and_missing_read_are_rejected(self):
        sim = self.document()
        start = infer(sim.context)
        e = sim.read("version_and_approval")
        cert = infer(sim.context, (e,))
        for bad in (start, replace(cert, decision=Decision.ALLOW), replace(cert, spent=1),
                    replace(cert, compatible_worlds=2),
                    replace(cert, reads=(replace(e, cost=1),)),
                    replace(cert, reads=(replace(e, values=(("version", 1),
                                                            ("approved_version", 1))),))):
            with self.subTest(bad=bad):
                self.assertFalse(verify(sim.audit(), bad).accepted)

    def test_replay_fails_across_run_snapshot_and_contract(self):
        sim = self.document()
        cert = infer(sim.context, (sim.read("version_and_approval"),))
        changed = replace(DOCUMENT_RELEASE, version="2")
        other_contract = Simulation(changed, {"version": 2, "approved_version": 1},
                                    snapshot_id="snapshot-a", run_id="first", budget=2)
        for other in (self.document(run="second"), self.document(snapshot="snapshot-b"),
                      other_contract):
            other.read("version_and_approval")
            self.assertEqual(verify(other.audit(), cert).reason, "scope_mismatch")

    def test_declared_initial_current_facts_count_as_evidence(self):
        sim = self.document(budget=1, initial_facts={"approved_version": 1})
        cert = infer(sim.context, (sim.read("version"),))
        self.assertIs(cert.decision, Decision.BLOCK)
        self.assertTrue(verify(sim.audit(), cert).accepted)
        self.assertFalse(verify(sim.audit(), replace(cert, initial_facts=())).accepted)
        with self.assertRaises(ValueError):
            self.document(initial_facts={"approved_version": 2})

    def test_verifier_checks_snapshot_truth_and_domain_types(self):
        contract = ActionContract("typed", "1", (("x", (True, 1)),),
                                  (Query("x", ("x",)),),
                                  ("eq", ("field", "x"), ("literal", True)))
        sim = Simulation(contract, {"x": 1}, snapshot_id="opaque", run_id="run", budget=1)
        real = sim.read("x")
        self.assertIs(infer(sim.context, (real,)).decision, Decision.BLOCK)
        forged = replace(real, values=(("x", True),))
        certificate = infer(sim.context, (forged,))
        self.assertIs(certificate.decision, Decision.ALLOW)
        self.assertEqual(verify(sim.audit(), certificate).reason, "transcript_mismatch")
        self.assertEqual(verify(replace(sim.audit(), reads=(forged,)), certificate).reason,
                         "invalid_audit_read")

    def test_admissibility_constrains_compatible_worlds(self):
        constrained = replace(DOCUMENT_RELEASE, admissibility=DOCUMENT_RELEASE.precondition)
        live = {"version": 1, "approved_version": 1}
        sim = Simulation(constrained, live, snapshot_id="one", run_id="run", budget=0)
        live["version"] = 2
        cert = infer(sim.context)
        self.assertIs(cert.decision, Decision.ALLOW)
        self.assertEqual(cert.compatible_worlds, 2)
        self.assertTrue(verify(sim.audit(), cert).accepted)
        with self.assertRaises(ValueError):
            Simulation(constrained, live, snapshot_id="two", run_id="run", budget=0)

    def test_contradictory_evidence_fails_closed_and_witness_reproduces(self):
        results = report()
        witness = results["witness"]
        self.assertEqual(witness["verified_runs"], 64)
        two = witness["rows"][2]
        self.assertEqual(two["fixed"]["defer"], 4)
        self.assertEqual(two["adaptive"]["defer"], 0)
        self.assertEqual(two["fixed"]["total_cost"], two["adaptive"]["total_cost"])
        inconsistent = results["document_release"]["contradictory_observations"]
        self.assertEqual(inconsistent["decision"], "inconsistent")
        self.assertEqual(inconsistent["compatible_worlds"], 0)
        self.assertFalse(inconsistent["against_trusted_audit"]["accepted"])
        self.assertFalse(inconsistent["against_corrupted_audit"]["accepted"])

    def test_invalid_contracts_reject_mutation_and_unbounded_enumeration(self):
        for invalid in (
            {"domains": (["version", (1, 2)], ("approved_version", (1, 2)))},
            {"domains": (("version", tuple(range(257))), ("approved_version", (1, 2)))},
            {"queries": (Query("bad", ("version",), True),)},
            {"precondition": ("eq", ("field", "unknown"), ("literal", 1))},
            {"admissibility": ("eq", ("field", "version"), ("literal", 3))},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(DOCUMENT_RELEASE, **invalid)
