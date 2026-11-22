"""Recount historical creations and receipt claims without importing the service."""

from collections import Counter
import json
from pathlib import Path
import unittest


class OpenTailTraceTests(unittest.TestCase):
    def test_equal_recovery_views_can_hide_zero_or_one_historical_effect(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "results/open_tail_v1.json").read_bytes())
        pair = {r["scenario"]: r for r in data["runs"] if "recovery_view" in r}
        no_effect, had_effect = pair["failed_effect_restored"], pair["unsealed_restored"]
        self.assertEqual(no_effect["recovery_view"], had_effect["recovery_view"])
        self.assertEqual(no_effect["effects_by_operation"][0], 0)
        self.assertEqual(had_effect["effects_by_operation"][0], 1)
        self.assertEqual((no_effect["final_recovery"], had_effect["final_recovery"]), ("unknown", "unknown"))

    def test_creation_history_source_acknowledgements_and_boundary_control(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "results/open_tail_v1.json").read_bytes())
        seen = set()
        totals = {}
        for run in data["runs"]:
            case = run["scenario"], run["policy"]
            self.assertNotIn(case, seen)
            seen.add(case)
            creations = []
            first_key, next_key = run["operation_keys"]
            for event in run["events"]:
                for created in event["created"]:
                    self.assertIn(created["operation_key"], (first_key, next_key))
                    creations.append((created["operation_key"], created["receipt_id"]))
                self.assertEqual(event["oracle_effects"], len(creations))
                if event["step"] == "recover" and event.get("outcome") == "recorded":
                    self.assertEqual(event["receipt_operation_key"], first_key)
                    self.assertIn((first_key, event["receipt_id"]), creations)
                if run["policy"] == "write_ahead_claim" and event["step"] == "compact":
                    self.assertEqual(event["claim_rows"], 0)
            by_key = Counter(key for key, _ in creations)
            first = by_key[first_key]
            self.assertEqual(run["effects_by_operation"], [first, by_key[next_key]])
            known = run["final_recovery"] == "recorded"
            self.assertEqual(run["source_status"], "sent" if known else "pending")
            if run["boundary_control"]:
                self.assertGreater(first, 1)
            elif run["policy"] == "write_ahead_claim":
                self.assertLessEqual(first, 1)
            count = totals.setdefault(run["policy"], Counter(runs=0, duplicate_runs=0,
                                      supported_duplicate_runs=0, known_single=0, unknown_with_effect=0,
                                      unknown_without_effect=0, forgotten=0))
            state = run["final_recovery"]
            count.update(runs=1, duplicate_runs=int(first > 1),
                         supported_duplicate_runs=int(first > 1 and not run["boundary_control"]),
                         known_single=int(first == 1 and known),
                         unknown_with_effect=int(first == 1 and state == "unknown"),
                         unknown_without_effect=int(first == 0 and state == "unknown"),
                         forgotten=int(state == "forgotten"))
        self.assertEqual(len(seen), 63)
        self.assertEqual(totals, data["summary"])


if __name__ == "__main__":
    unittest.main()
