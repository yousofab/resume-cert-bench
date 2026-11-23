"""Recount provider histories and verify how new evidence separates old views."""
from collections import Counter
import hashlib
import hmac
import json
from pathlib import Path
import unittest

class TerminalEvidenceTraceTests(unittest.TestCase):
    def load_result(self):
        return json.loads((Path(__file__).resolve().parents[1] / "results/terminal_evidence_v1.json").read_bytes())

    def test_histories_receipts_and_terminal_fences_match_recorded_claims(self):
        data = self.load_result()
        cases = set()
        totals = {}
        for run in data["runs"]:
            case = run["scenario"], run["policy"]
            self.assertNotIn(case, cases)
            cases.add(case)
            a, b = set(), set()
            terminal_empty = False
            for event in run["events"]:
                step = event["step"]
                if "evidence" in event:
                    envelope = event["evidence"]
                    body = json.loads(envelope["payload"])
                    fixture_key = hashlib.sha256(
                        f"PUBLIC ResumeCert fixture key ONLY {body['provider_id']}".encode()).digest()
                    mac = hmac.new(fixture_key, envelope["payload"].encode(), hashlib.sha256).hexdigest()
                    authentic = hmac.compare_digest(mac, envelope["mac"])
                    self.assertEqual(authentic, run["scenario"] != "tampered_mac")
                    if event.get("status") in ("secondary", "confirmed", "already_confirmed"):
                        self.assertTrue(authentic)
                        encode = lambda obj: json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                        expected_key = hashlib.sha256(encode(["archive-notice/v1", "terminal-evidence-study", 1]).encode()).hexdigest()
                        expected_body = encode({"archive_id": 1, "kind": "archive-notice/v1",
                                                "label": "Synthetic evidence-backed handoff"})
                        self.assertEqual(body["operation_key"], expected_key)
                        self.assertEqual(body["payload_digest"], hashlib.sha256(expected_body.encode()).hexdigest())
                        self.assertEqual(body["epoch"], 1)
                        if run["policy"] == "bound_terminal_release":
                            self.assertIn((body["kind"], body["purpose"]),
                                          (("recorded", "outcome"), ("retired_empty", "terminal")))
                if step == "terminal" and event.get("evidence_kind") == "retired_empty":
                    self.assertFalse(a)
                    terminal_empty = True
                if step in ("a_apply", "a_late") and "receipt_id" in event:
                    if terminal_empty and not run["boundary_control"]:
                        self.fail("primary committed after permanent empty retirement")
                    a.add(event["receipt_id"])
                if step in ("b_apply", "b_silent") and "receipt_id" in event:
                    if run["policy"] == "bound_terminal_release" and not run["boundary_control"]:
                        self.assertTrue(terminal_empty)
                    b.add(event["receipt_id"])
                self.assertEqual(event["effects_by_provider"], [len(a), len(b)])
                if event["source_status"] == "sent":
                    self.assertTrue(a if event["settled_provider"] == "primary" else b)
            self.assertEqual(run["effects_by_provider"], [len(a), len(b)])
            count = len(a) + len(b)
            confirmed = run["confirmed_provider"] is not None
            self.assertEqual(run["clean_confirmed"], confirmed and count == 1)
            self.assertEqual(run["duplicate"], count > 1)
            self.assertEqual(run["unresolved"], not confirmed)
            total = totals.setdefault(run["policy"], Counter(runs=0, clean_confirmed=0, duplicates=0,
                supported_duplicates=0, unresolved=0, unresolved_empty=0, unresolved_with_effect=0))
            total.update(runs=1, clean_confirmed=int(confirmed and count == 1), duplicates=int(count > 1),
                supported_duplicates=int(count > 1 and not run["boundary_control"]),
                unresolved=int(not confirmed), unresolved_empty=int(not confirmed and count == 0),
                unresolved_with_effect=int(not confirmed and count > 0))
        self.assertEqual(len(cases), 60)
        self.assertEqual(totals, data["summary"])

    def test_provider_terminal_evidence_distinguishes_equal_legacy_recovery_views(self):
        rows = {r["scenario"]: r for r in self.load_result()["runs"] if "additional_evidence_witness" in r}
        empty = rows["terminal_empty"]
        positive = rows["committed_before_terminal"]
        left = dict(empty["additional_evidence_witness"])
        right = dict(positive["additional_evidence_witness"])
        self.assertEqual(left.pop("new_evidence_kind"), "retired_empty")
        self.assertEqual(right.pop("new_evidence_kind"), "recorded")
        self.assertEqual(left, right)
        self.assertEqual(left["legacy_outcome"], "unknown")
        self.assertEqual(empty["confirmed_provider"], "secondary")
        self.assertEqual(positive["confirmed_provider"], "primary")
        self.assertTrue(empty["clean_confirmed"])
        self.assertTrue(positive["clean_confirmed"])

if __name__ == "__main__":
    unittest.main()
