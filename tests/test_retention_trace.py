"""Recount delivery observations separately from the retention implementation."""

from collections import Counter
import json
from pathlib import Path
import unittest


class RetentionTraceTests(unittest.TestCase):
    def test_observer_history_and_boundary_controls_match_recorded_claims(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "results/retention_v1.json").read_bytes())
        cases = set()
        summary = {}
        for run in data["runs"]:
            case = run["scenario"], run["policy"]
            self.assertNotIn(case, cases)
            cases.add(case)
            projected = []
            first_key = None
            closed = False
            for event in run["events"]:
                if event["step"] in ("compact", "close_fail"):
                    closed = True
                if event.get("created"):
                    projected.append([event["provider"], event["operation_key"]])
                    if event["step"] in ("a_apply", "a_retry", "failover"):
                        first_key = event["operation_key"]
                    if run["policy"] == "independent_floor" and closed:
                        self.assertIn(event["step"], ("next_apply", "other_apply"))
                self.assertEqual(event["oracle_effects"], len(projected))
                if run["policy"] == "independent_floor" and closed and event["step"] == "lookup":
                    self.assertIn(event.get("resolution", event.get("status")), ("forgotten", "guard_unavailable"))
            self.assertEqual(projected, run["observed_effects"])
            first_count = sum(key == first_key for _, key in projected) if first_key is not None else 0
            self.assertEqual(first_count, run["first_operation_effects"])
            self.assertEqual(first_count > 1, run["duplicate"])
            if run["scenario"] == "unclosed_provider_restore_control":
                self.assertEqual(first_count, 2)
            count = summary.setdefault(run["policy"], Counter(runs=0, duplicate_runs=0,
                                       closed_prefix_duplicate_runs=0, forgotten_lookups=0, blocked_attempts=0))
            count.update(runs=1, duplicate_runs=int(first_count > 1),
                         closed_prefix_duplicate_runs=int(first_count > 1 and run["closed_prefix_case"]),
                         forgotten_lookups=sum(e.get("resolution") == "forgotten" for e in run["events"]),
                         blocked_attempts=sum(e.get("status") == "closed" for e in run["events"]))
        self.assertEqual(len(cases), 54)
        self.assertEqual(summary, data["summary"])


if __name__ == "__main__":
    unittest.main()
