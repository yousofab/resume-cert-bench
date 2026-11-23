"""Check provider evidence purpose, exact claim binding, and durable handoff."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from resumecert import open_tail, provider_failover as routing, revocable_delivery as delivery
from resumecert import terminal_evidence as evidence
from resumecert.archive_transfer import connect
from scripts.run_terminal_evidence import setup, KEYS, STREAM


class TerminalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.message, self.dispatch = setup(self.root)
        self.source = self.enterContext(closing(connect(self.root / "source.sqlite3")))
        self.guard = self.enterContext(closing(connect(self.root / "guard.sqlite3")))
        self.a = self.enterContext(closing(connect(self.root / "primary.sqlite3")))
        self.b = self.enterContext(closing(connect(self.root / "secondary.sqlite3")))

    def terminal(self):
        return evidence.issue(self.a, self.dispatch, KEYS["primary"], terminal=True)

    def complete_secondary(self):
        dispatch = evidence.reserve(self.source, self.guard, 1, self.b, STREAM)
        result = routing.apply_dispatch(self.b, dispatch)
        certificate = evidence.issue(self.b, dispatch, KEYS["secondary"])
        self.assertEqual(evidence.accept(self.source, self.guard, 1, certificate, KEYS), "confirmed")
        return result

    def test_authenticated_lookup_cannot_release_a_live_attempt(self):
        observation = evidence.issue(self.a, self.dispatch, KEYS["primary"])
        self.assertEqual(evidence.verify(observation, KEYS)["kind"], "lookup_empty")
        with self.assertRaises(evidence.InsufficientEvidence):
            evidence.accept(self.source, self.guard, 1, observation, KEYS)
        self.assertEqual(routing._route(self.source, 1)[4], "primary")
        routing.apply_dispatch(self.a, self.dispatch)
        positive = evidence.issue(self.a, self.dispatch, KEYS["primary"])
        self.assertEqual(evidence.accept(self.source, self.guard, 1, positive, KEYS), "confirmed")

    def test_terminal_empty_enables_one_secondary_and_blocks_late_primary(self):
        certificate = self.terminal()
        self.assertEqual(evidence.accept(self.source, self.guard, 1, certificate, KEYS), "secondary")
        self.assertEqual(evidence.accept(self.source, self.guard, 1, certificate, KEYS), "secondary")
        self.complete_secondary()
        with self.assertRaises(delivery.DispatchDenied):
            routing.apply_dispatch(self.a, self.dispatch)
        self.assertEqual(sum(p.execute("SELECT count(*) FROM handoff_effects").fetchone()[0]
                             for p in (self.a, self.b)), 1)
        self.assertEqual(evidence.accept(self.source, self.guard, 1, certificate, KEYS), "already_confirmed")

    def test_positive_provider_evidence_resolves_a_missing_projection_receipt(self):
        routing.apply_dispatch(self.a, self.dispatch)
        with closing(connect(self.root / "projection.sqlite3")) as projection:
            self.assertEqual(open_tail.recover(projection, self.guard, self.message).status, "unknown")
            positive = evidence.issue(self.a, self.dispatch, KEYS["primary"])
            self.assertEqual(evidence.accept(self.source, self.guard, 1, positive, KEYS), "confirmed")
            self.assertEqual(open_tail.recover(projection, self.guard, self.message).status, "recorded")
        self.assertEqual(routing._route(self.source, 1)[5], "primary")

    def test_valid_macs_still_require_operation_body_provider_generation_and_purpose(self):
        original = json.loads(self.terminal().payload)
        cases = [("operation_key", "a" * 64), ("payload_digest", "b" * 64),
                 ("provider_id", "secondary"), ("epoch", 3), ("closed_epoch", 9),
                 ("purpose", "observation"), ("epoch", True), ("version", "another-version")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                body = dict(original)
                body[field] = value
                if field == "epoch" and value == 3:
                    body["closed_epoch"] = 4
                bad = evidence._sign(body, KEYS[body["provider_id"]])
                with self.assertRaises(evidence.EvidenceError):
                    evidence.accept(self.source, self.guard, 1, bad, KEYS)
        self.assertEqual(routing._route(self.source, 1)[4], "primary")
        self.assertEqual(self.guard.execute("SELECT count(*) FROM accepted_provider_evidence").fetchone(), (0,))

    def test_canonical_encoding_and_pinned_key_are_required(self):
        certificate = self.terminal()
        wrong_trust = {**KEYS, "primary": b"x" * 32}
        with self.assertRaises(evidence.EvidenceError):
            evidence.accept(self.source, self.guard, 1, certificate, wrong_trust)
        spaced = json.dumps(json.loads(certificate.payload), indent=2)
        mac = hmac.new(KEYS["primary"], spaced.encode(), hashlib.sha256).hexdigest()
        with self.assertRaises(evidence.EvidenceError):
            evidence.accept(self.source, self.guard, 1, evidence.Evidence(spaced, mac), KEYS)
        with self.assertRaises(evidence.EvidenceError):
            evidence.accept(self.source, self.guard, 1, evidence.Evidence(certificate.payload, "نامعتبر"), KEYS)

    def test_failed_evidence_storage_cannot_switch_the_source_route(self):
        certificate = self.terminal()
        self.guard.executescript("""CREATE TRIGGER fixture_fail_evidence BEFORE INSERT ON accepted_provider_evidence
            BEGIN SELECT RAISE(ABORT, 'evidence storage failed'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "evidence storage failed"):
            evidence.accept(self.source, self.guard, 1, certificate, KEYS)
        self.assertEqual(routing._route(self.source, 1)[4], "primary")
        self.assertEqual(self.guard.execute("SELECT count(*) FROM accepted_provider_evidence").fetchone(), (0,))
        self.guard.execute("DROP TRIGGER fixture_fail_evidence")
        self.assertEqual(evidence.accept(self.source, self.guard, 1, certificate, KEYS), "secondary")

    def test_real_exit_after_guard_commit_replays_evidence_without_live_provider_lookup(self):
        certificate = self.terminal()
        code = """
import os
import sys
from resumecert.archive_transfer import connect
from resumecert.terminal_evidence import Evidence, accept
from scripts.run_terminal_evidence import KEYS
s, g = map(connect, sys.argv[1:3])
s.create_function('fixture_exit', 0, lambda: os._exit(83))
s.executescript('''CREATE TRIGGER fixture_exit_on_route BEFORE UPDATE ON failover_routes
BEGIN SELECT fixture_exit(); END;''')
accept(s, g, 1, Evidence(sys.argv[3], sys.argv[4]), KEYS)
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.root / "source.sqlite3"),
                                str(self.root / "guard.sqlite3"), certificate.payload, certificate.mac],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 83, child.stderr.decode())
        self.assertEqual(self.guard.execute("SELECT count(*) FROM accepted_provider_evidence").fetchone(), (1,))
        self.assertEqual(routing._route(self.source, 1)[4], "primary")
        self.source.execute("DROP TRIGGER fixture_exit_on_route")
        # Recovery takes only source, guard and pinned keys; there is no backend query.
        self.assertEqual(evidence.recover_saved(self.source, self.guard, 1, "primary", KEYS), "secondary")
        self.complete_secondary()
        with self.assertRaises(delivery.DispatchDenied):
            routing.apply_dispatch(self.a, self.dispatch)

    def test_primary_commit_racing_terminalization_yields_only_one_global_effect(self):
        ready = Barrier(2)

        def worker(close):
            with closing(sqlite3.connect(self.root / "primary.sqlite3", autocommit=True, timeout=5)) as primary:
                ready.wait(timeout=5)
                if close:
                    return evidence.issue(primary, self.dispatch, KEYS["primary"], terminal=True)
                try:
                    return routing.apply_dispatch(primary, self.dispatch)
                except delivery.DispatchDenied:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            apply_future = pool.submit(worker, False)
            close_future = pool.submit(worker, True)
            applied = apply_future.result(timeout=10)
            certificate = close_future.result(timeout=10)
        body = evidence.verify(certificate, KEYS)
        self.assertEqual(body["kind"], "recorded" if applied is not None else "retired_empty")
        result = evidence.accept(self.source, self.guard, 1, certificate, KEYS)
        if result == "secondary":
            self.complete_secondary()
        self.assertEqual(sum(p.execute("SELECT count(*) FROM handoff_effects").fetchone()[0]
                             for p in (self.a, self.b)), 1)

    def test_secondary_reply_loss_recovers_with_its_own_provider_evidence(self):
        evidence.accept(self.source, self.guard, 1, self.terminal(), KEYS)
        dispatch = evidence.reserve(self.source, self.guard, 1, self.b, STREAM)
        routing.apply_dispatch(self.b, dispatch)
        self.assertEqual(routing._route(self.source, 1)[1], "pending")
        positive = evidence.issue(self.b, dispatch, KEYS["secondary"])
        self.assertEqual(evidence.accept(self.source, self.guard, 1, positive, KEYS), "confirmed")
        self.assertEqual(evidence.accept(self.source, self.guard, 1, positive, KEYS), "already_confirmed")
        self.assertEqual(routing._route(self.source, 1)[5], "secondary")

    def test_conflicting_evidence_cannot_replace_an_accepted_terminal_decision(self):
        certificate = self.terminal()
        evidence.accept(self.source, self.guard, 1, certificate, KEYS)
        body = json.loads(certificate.payload)
        body.update(kind="recorded", purpose="outcome", effect_id=1, closed_epoch=None)
        conflicting = evidence._sign(body, KEYS["primary"])
        with self.assertRaises(evidence.EvidenceError):
            evidence.accept(self.source, self.guard, 1, conflicting, KEYS)
        with self.assertRaises(sqlite3.IntegrityError):
            self.guard.execute("DELETE FROM accepted_provider_evidence")
        with self.assertRaises(sqlite3.IntegrityError):
            self.guard.execute("UPDATE accepted_provider_evidence SET mac = 'changed'")
        self.assertEqual(routing._route(self.source, 1)[4], "secondary")

if __name__ == "__main__":
    unittest.main()
