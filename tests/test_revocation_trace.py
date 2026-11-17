"""Independent review of the recorded timelines using an event-only oracle."""
from collections import Counter
import json
from pathlib import Path

import unittest

ROOT = Path(__file__).resolve().parents[1]


class RevocationTraceTests(unittest.TestCase):
    def test_independent_event_oracle(self):
        data = json.loads((ROOT / "results/destination_revocation_v1.json").read_bytes())
        derived = {}
        seen = set()
        for row in data["runs"]:
            case = row["scenario"], row["policy"]
            assert case not in seen
            seen.add(case)
            current, active, requested = 1, True, False
            first_effect = None
            fence_records = {}
            bad = between = denied = 0
            receipts = set()
            disposition = "unknown"
            for event in row["events"]:
                action, outcome = event["step"], event.get("status")
                assert (event["destination_epoch"], event["destination_active"]) == (current, active)
                if action == "request":
                    requested = True
                if action == "fence":
                    assert active
                    fence_records[current] = first_effect
                    current, active = current + 1, False
                if action == "replay_fence":
                    assert current == 3 and active
                if action in ("fence", "replay_fence") and "fence" in event:
                    barrier = event["fence"]
                    frozen = fence_records[barrier["retired_epoch"]]
                    assert barrier["closed_epoch"] == barrier["retired_epoch"] + 1
                    assert (barrier["effect"]["accepted_epoch"] if barrier["effect"] else None) == frozen
                if action == "renew":
                    assert not active
                    current, active = current + 1, True
                    requested = False
                    assert event["new_epoch"] == current
                if action.startswith("arrive") and outcome == "applied":
                    assert first_effect is None
                    sent_under = event["carried_epoch"]
                    valid = active and sent_under == current
                    bad += not valid
                    between += valid and requested
                    first_effect = sent_under
                if action.startswith("arrive") and outcome in ("applied", "same_receipt"):
                    receipts.add(event["receipt_id"])
                    if action.endswith("_ack"):
                        disposition = "effect_confirmed"
                if outcome == "destination_denied":
                    denied += 1
                    assert not active or current != event["carried_epoch"]
                if action == "recover" and outcome in ("effect_confirmed", "fenced_no_effect"):
                    assert first_effect is not None if outcome == "effect_confirmed" else (
                        first_effect is None and not active and fence_records[current - 1] is None
                    )
                    disposition = outcome
            assert len(receipts) == (first_effect is not None) == row["effect_count"]
            assert (bad, between, denied, disposition) == (row["invalid_effects"], row["propagation_effects"],
                                                             row["denied_arrivals"], row["settlement"])
            assert (row["source_status"] == "sent") == (disposition == "effect_confirmed")
            totals = derived.setdefault(row["policy"], Counter())
            totals.update(runs=1, invalid_effects=bad, propagation_effects=between,
                          confirmed=disposition == "effect_confirmed", fenced_no_effect=disposition == "fenced_no_effect",
                          unknown=disposition == "unknown",
                          recorded_but_unconfirmed=first_effect is not None and disposition != "effect_confirmed")
        self.assertEqual(len(seen), 56)
        self.assertEqual(derived, data["summary"])
