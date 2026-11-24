"""Exercise the retained authority at real SQLite crash and race boundaries."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import shutil
import json
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from resumecert import provider_authority as durable, provider_failover as routing
from resumecert import terminal_evidence as evidence, revocable_delivery as delivery
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import Notice


class ProviderAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "retained.sqlite3"
        self.projection = self.enterContext(closing(connect(self.root / "projection.sqlite3")))
        durable.create_projection(self.projection)
        self.service = durable.ProviderAuthority(self.path, "primary", "fixture-root")
        self.notice = Notice("inert-test-operation", "Synthetic archive handoff")
        self.key = b"x" * 32  # Public fixture material.
        with closing(connect(self.path)) as db:
            durable.create_authority(db, "primary", "fixture-root")
            epoch = delivery.register(db, self.notice)
        self.dispatch = routing.Dispatch("primary", self.notice, epoch)

    def run_child(self, action):
        code = """
import os
from pathlib import Path
import sys
from resumecert.effect_delivery import Notice
from resumecert.provider_authority import ProviderAuthority
from resumecert.provider_failover import Dispatch
path, key, payload, action = sys.argv[1:]
service = ProviderAuthority(Path(path), 'primary', 'fixture-root')
dispatch = Dispatch('primary', Notice(key, payload), 1)
if action == 'apply':
    service.apply(dispatch)
else:
    service.issue(dispatch, b'x' * 32, terminal=True)
os._exit(87)
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.path), self.notice.operation_key, self.notice.payload, action],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 87, result.stderr.decode())

    def test_commit_survives_process_exit_before_receipt_or_projection(self):
        self.run_child("apply")
        self.assertEqual(self.projection.execute("SELECT count(*) FROM provider_projection").fetchone(), (0,))
        attestation = self.service.issue(self.dispatch, self.key, terminal=True)
        body = evidence.verify(attestation, {"primary": self.key})
        self.assertEqual(body["kind"], "recorded")
        self.assertEqual(self.service.refresh_projection(self.projection, self.dispatch), "recorded")
        self.assertEqual(self.projection.execute("SELECT effect_id FROM provider_projection").fetchone(),
                         (body["effect_id"],))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_effects").fetchone(), (1,))

    def test_retirement_survives_process_exit_before_source_gets_evidence(self):
        self.run_child("retire")
        with self.assertRaises(delivery.DispatchDenied):
            self.service.apply(self.dispatch)
        attestation = self.service.issue(self.dispatch, self.key, terminal=True)
        self.assertEqual(evidence.verify(attestation, {"primary": self.key})["kind"], "retired_empty")
        self.assertEqual(self.service.refresh_projection(self.projection, self.dispatch), "retired_empty")

    def test_missing_or_substituted_authority_fails_closed(self):
        original = self.path.with_suffix(".saved")
        self.path.rename(original)
        with self.assertRaises(durable.AuthorityUnavailable):
            self.service.apply(self.dispatch)
        self.assertFalse(self.path.exists())
        with closing(connect(self.path)) as db:
            durable.create_authority(db, "primary", "other-root")
            delivery.register(db, self.notice)
        with self.assertRaises(durable.AuthorityUnavailable):
            self.service.issue(self.dispatch, self.key, terminal=True)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM permanent_retirements").fetchone(), (0,))
        shutil.copyfile(original, self.path)
        self.assertEqual(self.service.apply(self.dispatch).receipt.operation_key, self.notice.operation_key)

    def test_projection_write_failure_does_not_change_committed_authority(self):
        first = self.service.apply(self.dispatch)
        self.projection.execute("PRAGMA query_only=ON")
        with self.assertRaises(sqlite3.OperationalError):
            self.service.refresh_projection(self.projection, self.dispatch)
        self.assertEqual(self.service.apply(self.dispatch), first)
        self.projection.execute("PRAGMA query_only=OFF")
        self.assertEqual(self.service.refresh_projection(self.projection, self.dispatch), "recorded")

    def test_projection_verifies_scope_and_never_grants_authority(self):
        old = self.service.refresh_projection(self.projection, self.dispatch)
        self.assertEqual(old, "unresolved")
        with self.assertRaises(ValueError):
            self.service.refresh_projection(self.projection, replace(self.dispatch, provider_id="secondary"))
        self.projection.execute("UPDATE provider_projection SET status = 'recorded', effect_id = 900")
        self.assertEqual(self.service.refresh_projection(self.projection, self.dispatch), "unresolved")
        self.assertEqual(self.service.apply(self.dispatch).receipt.effect_id, 1)

    def test_commit_and_permanent_retirement_share_one_order(self):
        ready = Barrier(2)

        def apply():
            ready.wait(timeout=5)
            try:
                return self.service.apply(self.dispatch)
            except delivery.DispatchDenied:
                return None

        def retire():
            ready.wait(timeout=5)
            return self.service.issue(self.dispatch, self.key, terminal=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempted = pool.submit(apply)
            retired = pool.submit(retire)
            effect = attempted.result(timeout=10)
            proof = retired.result(timeout=10)
        body = evidence.verify(proof, {"primary": self.key})
        self.assertEqual(body["kind"], "recorded" if effect else "retired_empty")
        with self.assertRaises(delivery.DispatchDenied):
            self.service.apply(self.dispatch)


class ProviderAuthorityTraceTests(unittest.TestCase):
    def test_recount_historical_creations_and_verify_restore_boundary(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "results/provider_authority_v1.json").read_bytes())
        cases = set()
        totals = {policy: Counter() for policy in data["summary"]}
        for run in data["runs"]:
            case = run["scenario"], run["policy"]
            self.assertNotIn(case, cases)
            cases.add(case)
            history = [event["created"] for event in run["events"] if "created" in event]
            self.assertEqual(history, run["history"])
            primary, secondary = [
                sum(row[0] == provider and row[1] == run["operation_key"] for row in history)
                for provider in ("primary", "secondary")]
            self.assertEqual(run["effects_by_provider"], [primary, secondary])
            confirmed = run["settled_provider"] is not None
            count = primary + secondary
            if confirmed:
                self.assertGreater(primary if run["settled_provider"] == "primary" else secondary, 0)
            if run["boundary_control"]:
                totals[run["policy"]]["boundary_duplicates"] += count > 1
            else:
                totals[run["policy"]].update(confirmed_single=int(confirmed and count == 1),
                    duplicate_runs=int(count > 1), unresolved=int(not confirmed))
            if run["policy"] == "retained_authority":
                self.assertEqual(primary + secondary, 2 if run["boundary_control"] else 1)
                if run["scenario"] == "authority_remains_unavailable":
                    self.assertIsNone(run["settled_provider"])
                if run["scenario"] == "unrelated_authorized_work_after_restore":
                    self.assertEqual([event["next_status"] for event in run["events"] if "next_status" in event],
                                     ["confirmed"])
                    self.assertEqual(len(history), 2)
                if run["scenario"] == "stale_projection_can_be_rebuilt":
                    self.assertEqual([event["status"] for event in run["events"] if event["step"] == "inspect_a"],
                                     ["unresolved"])
                    self.assertEqual([event["status"] for event in run["events"] if event["step"] == "refresh_a"],
                                     ["unresolved", "recorded", "recorded"])
        self.assertEqual(len(cases), 51)
        self.assertEqual(totals, data["summary"])
