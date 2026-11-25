"""Verify the witness boundary across independent SQLite files and processes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from resumecert import provider_failover as routing, revocable_delivery as delivery
from resumecert import terminal_evidence as evidence, witnessed_authority as protected
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import Notice
from resumecert.provider_authority import AuthorityUnavailable, ProviderAuthority, create_authority, create_projection


def backup(path, destination):
    with closing(connect(path)) as db, closing(connect(destination)) as saved:
        db.backup(saved)


class WitnessedAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "provider.sqlite3"
        self.witness = self.root / "witness.sqlite3"
        self.authority = ProviderAuthority(self.path, "primary", "fixture")
        with closing(connect(self.path)) as db, closing(connect(self.witness)) as witness:
            create_authority(db, "primary", "fixture")
            protected.create_witness(witness)
        self.service = protected.enroll_fresh(self.authority, self.witness)
        self.notice = Notice("operation", "inert effect")
        self.other = Notice("other", "independent inert effect")
        with self.service._session() as (db, _):
            for notice in (self.notice, self.other):
                delivery._register_in_transaction(db, notice)
        self.dispatch = routing.Dispatch("primary", self.notice, 1)
        self.key = b"x" * 32

    def test_stale_image_cannot_create_an_effect_or_issue_a_false_retirement(self):
        earlier = self.root / "earlier.sqlite3"
        latest = self.root / "latest.sqlite3"
        backup(self.path, earlier)
        first = self.service.apply(self.dispatch)
        revision = self.service.verify_current()
        backup(self.path, latest)
        shutil.copyfile(earlier, self.path)
        with self.assertRaises(protected.StateMismatch):
            self.service.apply(self.dispatch)
        for terminal in (False, True):
            with self.assertRaises(protected.StateMismatch):
                self.service.issue(self.dispatch, self.key, terminal=terminal)
        shutil.copyfile(latest, self.path)
        self.assertEqual(self.service.verify_current(), revision)
        self.assertEqual(self.service.apply(self.dispatch), first)
        self.assertEqual(self.service.verify_current(), revision)

    def test_same_revision_different_content_cannot_rebuild_a_projection(self):
        path = self.root / "sibling.sqlite3"
        sibling_witness = self.root / "sibling-witness.sqlite3"
        backup(self.path, path)
        backup(self.witness, sibling_witness)
        sibling = protected.WitnessedAuthority(replace(self.authority, path=path), sibling_witness)
        sibling.apply(replace(self.dispatch, notice=self.other))
        self.service.issue(self.dispatch, self.key, terminal=True)
        self.assertEqual(sibling.verify_current(), self.service.verify_current())
        candidate = protected.WitnessedAuthority(replace(self.authority, path=path), self.witness)
        with closing(connect(self.root / "projection.sqlite3")) as projection:
            create_projection(projection)
            with self.assertRaises(protected.StateMismatch):
                candidate.refresh_projection(projection, self.dispatch)
            self.assertEqual(projection.execute("SELECT count(*) FROM provider_projection").fetchone(), (0,))
            self.assertEqual(self.service.refresh_projection(projection, self.dispatch), "retired_empty")

    def test_missing_witness_neither_creates_a_store_nor_changes_provider(self):
        backup_path = self.root / "witness-copy.sqlite3"
        self.witness.rename(backup_path)
        with self.assertRaises(AuthorityUnavailable):
            self.service.apply(self.dispatch)
        self.assertFalse(self.witness.exists())
        backup_path.rename(self.witness)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
        self.assertEqual(self.service.verify_current(), 1)

    def test_two_images_with_one_head_can_commit_only_one_divergent_transition(self):
        clone = self.root / "clone.sqlite3"
        backup(self.path, clone)
        ready = Barrier(2)

        class ConcurrentCandidate(protected.WitnessedAuthority):
            def _head(self, witness):
                head = super()._head(witness)
                ready.wait(timeout=5)
                return head

        def apply(path, notice):
            service = ConcurrentCandidate(replace(self.authority, path=path), self.witness)
            try:
                return service.apply(replace(self.dispatch, notice=notice)).receipt.operation_key
            except protected.StateMismatch:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            a = workers.submit(apply, self.path, self.notice)
            b = workers.submit(apply, clone, self.other)
            outcomes = [future.result(timeout=10) for future in (a, b)]
        self.assertEqual(outcomes.count(None), 1)
        winner = self.path if outcomes[0] is not None else clone
        loser = clone if winner == self.path else self.path
        for path, effects in ((winner, 1), (loser, 0)):
            with closing(connect(path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM handoff_effects").fetchone(), (effects,))
        self.assertEqual(protected.WitnessedAuthority(replace(self.authority, path=winner),
                                                     self.witness).verify_current(), 2)

    def test_real_exit_after_witness_commit_keeps_old_state_quarantined(self):
        script = """
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from resumecert.effect_delivery import Notice
from resumecert.provider_authority import ProviderAuthority
from resumecert.provider_failover import Dispatch
from resumecert.witnessed_authority import WitnessedAuthority

class ExitAtRevision(ProviderAuthority):
    @contextmanager
    def _connect(self):
        with super()._connect() as db:
            def trace(sql):
                if sql.startswith('UPDATE authority_revision'):
                    os._exit(79)
            db.set_trace_callback(trace)
            yield db

provider, witness = map(Path, sys.argv[1:])
service = WitnessedAuthority(ExitAtRevision(provider, 'primary', 'fixture'), witness)
service.apply(Dispatch('primary', Notice('operation', 'inert effect'), 1))
"""
        child = subprocess.run([sys.executable, "-c", script, str(self.path), str(self.witness)],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 79, child.stderr.decode())
        with closing(connect(self.path)) as db, closing(connect(self.witness)) as witness:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_effects").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT revision FROM authority_revision").fetchone(), (1,))
            self.assertEqual(self.service._head(witness)[0], 2)
        with self.assertRaises(protected.StateMismatch):
            self.service.verify_current()

    def test_read_and_repeated_retirement_leave_witness_at_same_head(self):
        proof = self.service.issue(self.dispatch, self.key, terminal=True)
        revision = self.service.verify_current()
        self.assertEqual(evidence.verify(proof, {"primary": self.key})["kind"], "retired_empty")
        self.assertEqual(self.service.issue(self.dispatch, self.key, terminal=True), proof)
        with self.assertRaises(delivery.DispatchDenied):
            self.service.apply(self.dispatch)
        self.assertEqual(self.service.verify_current(), revision)


class WitnessedAuthorityTraceTests(unittest.TestCase):
    def test_recount_results_and_same_revision_control(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "results/authority_rollback_v1.json").read_bytes())
        totals = {policy: Counter(single_confirmed=0, duplicate=0, unresolved=0, boundary_duplicates=0)
                  for policy in data["summary"]}
        pairs = set()
        for run in data["runs"]:
            scenario, policy = run["scenario"], run["policy"]
            self.assertNotIn((scenario, policy), pairs)
            pairs.add((scenario, policy))
            history = [e["created"] for e in run["events"] if "created" in e]
            self.assertEqual(history, run["history"])
            if run["settled_provider"] is not None:
                self.assertIn(run["settled_provider"], [row[0] for row in history])
            count = len(history)
            if run["boundary"]:
                self.assertEqual(count, 2)
                totals[policy]["boundary_duplicates"] += 1
                continue
            key = "duplicate" if count > 1 else "single_confirmed" if count == 1 and run["settled_provider"] else "unresolved"
            totals[policy][key] += 1
            if policy == "revision_and_commitment":
                self.assertLessEqual(count, 1)
            if scenario.startswith("same_revision_sibling"):
                observed = next(e for e in run["events"] if e["step"] == "verify")
                self.assertEqual(observed["candidate_revision"], observed["witness_revision"])
                self.assertFalse(observed["matching_content"])
                self.assertEqual(observed["status"], "quarantined" if policy == "revision_and_commitment" else "admitted")
        self.assertEqual(len(pairs), 39)
        self.assertEqual(totals, data["summary"])

    def test_crash_gap_and_committed_then_rolled_back_have_same_gate_observation(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "results/authority_rollback_v1.json").read_bytes())
        names = ("witness_ahead_after_failed_commit", "older_image_cannot_issue_terminal_absence")
        runs = {run["scenario"]: run for run in data["runs"] if run["policy"] == "revision_and_commitment"}
        views = [next(event for event in runs[name]["events"] if event["step"] == "verify") for name in names]
        self.assertEqual(views[0], views[1])
        self.assertEqual(views[0]["status"], "quarantined")
        self.assertEqual([len(runs[name]["history"]) for name in names], [0, 1])
