"""Independently verify ledger and route claims made by the recorded traces."""
from collections import Counter
import json
from pathlib import Path
import unittest


class ProviderTraceTests(unittest.TestCase):
    def test_event_projection_matches_persisted_benchmark_counts(self):
        data = json.loads((Path(__file__).resolve().parents[1] /
                           "results/provider_failover_v1.json").read_bytes())
        seen = set()
        totals = {}
        for row in data["runs"]:
            key = row["scenario"], row["policy"]
            self.assertNotIn(key, seen)
            seen.add(key)
            rigid = row["policy"] == "permanent_retirement_switch"
            a, b = set(), set()
            retired_empty = False
            for event in row["events"]:
                step, effect = event["step"], event.get("effect_id")
                if step == "retire":
                    self.assertEqual(event["had_prior_effect"], bool(a))
                    retired_empty = not a
                if step in ("a_arrive", "a_fresh_arrival") and effect is not None:
                    if retired_empty:
                        self.fail("primary committed after an empty permanent retirement")
                    a.add(effect)
                if step in ("b_arrive", "b_silent") and effect is not None:
                    if rigid and not retired_empty:
                        self.fail("secondary started without an empty permanent retirement")
                    b.add(effect)
            self.assertEqual(row["effects_by_provider"], [len(a), len(b)])
            if rigid:
                self.assertLessEqual(len(a) + len(b), 1)
            if row["confirmed_provider"] == "primary":
                self.assertTrue(a)
            elif row["confirmed_provider"] == "secondary":
                self.assertTrue(b)
            count = totals.setdefault(row["policy"], Counter())
            confirmed = row["confirmed_provider"] is not None
            count.update(duplicate=len(a) + len(b) > 1, clean_confirmed=confirmed and len(a) + len(b) == 1,
                         unresolved=not confirmed, secondary_used=len(b))
        self.assertEqual(len(seen), 64)
        self.assertEqual(totals, data["summary"])
