"""Exercise replay durability, exact predecessor binding and bounded outcomes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from resumecert import authority_redo as redo, witnessed_authority as witnessed
from resumecert import provider_failover as routing, revocable_delivery as delivery
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import Notice, encode
from resumecert.provider_authority import ProviderAuthority, create_authority


class AuthorityRedoTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.root / "provider.sqlite3"
        self.witness = self.root / "witness.sqlite3"
        self.authority = ProviderAuthority(self.path, "primary", "fixture")
        with closing(connect(self.path)) as db, closing(connect(self.witness)) as witness:
            create_authority(db, "primary", "fixture")
            witnessed.create_witness(witness)
            redo.create_redo_log(witness)
        witnessed.enroll_fresh(self.authority, self.witness)
        self.service = redo.RecoverableAuthority(self.authority, self.witness)
        notice = Notice("operation", "inert effect")
        self.dispatch = routing.Dispatch("primary", notice, 1)
        with self.service._session(("register", notice, None)) as (db, _):
            delivery._register_in_transaction(db, notice)

    def state(self):
        with self.authority._connect() as db, self.service._witness() as witness:
            return (self.service._revision(db), self.service._head(witness)[0],
                    db.execute("SELECT count(*) FROM handoff_effects").fetchone()[0])

    def crash(self):
        script = """
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from resumecert.authority_redo import RecoverableAuthority
from resumecert.effect_delivery import Notice
from resumecert.provider_authority import ProviderAuthority
from resumecert.provider_failover import Dispatch

class ExitBeforeCommit(ProviderAuthority):
    @contextmanager
    def _connect(self):
        with super()._connect() as db:
            def trace(sql):
                if sql.startswith("UPDATE authority_revision"):
                    os._exit(84)
            db.set_trace_callback(trace)
            yield db

provider, witness = map(Path, sys.argv[1:])
service = RecoverableAuthority(ExitBeforeCommit(provider, "primary", "fixture"), witness)
service.apply(Dispatch("primary", Notice("operation", "inert effect"), 1))
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.path), str(self.witness)],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 84, result.stderr.decode())
        self.assertEqual(self.state(), (1, 2, 0))

    def test_real_exit_after_witness_commit_reconstructs_one_effect_and_receipt(self):
        self.crash()
        with self.assertRaises(witnessed.StateMismatch):
            self.service.apply(self.dispatch)
        self.assertEqual(self.service.recover(), redo.Recovery(2, True))
        self.assertEqual(self.service.recover(), redo.Recovery(2, False))
        self.assertEqual(self.service.apply(self.dispatch).receipt.effect_id, 1)
        self.assertEqual(self.state(), (2, 2, 1))

    def test_witness_write_failure_keeps_head_and_effect_at_predecessor(self):
        with closing(connect(self.witness)) as db:
            db.executescript("""CREATE TRIGGER interrupt_redo BEFORE INSERT ON authority_redo
                                BEGIN SELECT RAISE(ABORT, 'journal unavailable'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "journal unavailable"):
            self.service.apply(self.dispatch)
        self.assertEqual(self.state(), (1, 1, 0))
        with closing(connect(self.witness)) as db:
            self.assertEqual(db.execute("SELECT revision FROM authority_redo").fetchone(), (1,))
            db.execute("DROP TRIGGER interrupt_redo")
        self.service.apply(self.dispatch)
        self.assertEqual(self.state(), (2, 2, 1))

    def test_wrong_reconstructed_state_rolls_back_and_recipe_can_be_retried(self):
        self.crash()
        notice = self.dispatch.notice
        with closing(connect(self.witness)) as db:
            saved = db.execute("SELECT command FROM authority_redo").fetchone()[0]
            db.execute("UPDATE authority_redo SET command=?",
                       (encode(["resume-cert-redo/v1", "retire", notice.operation_key, notice.payload, 1]),))
        with self.assertRaisesRegex(witnessed.StateMismatch, "differs from witnessed successor"):
            self.service.recover()
        self.assertEqual(self.state(), (1, 2, 0))
        with self.authority._connect() as db:
            self.assertIsNone(routing.retirement(db, notice))
        with closing(connect(self.witness)) as db:
            db.execute("UPDATE authority_redo SET command=?", (saved,))
        self.assertEqual(self.service.recover(), redo.Recovery(2, True))

    def test_parallel_recovery_calls_materialize_one_transition(self):
        self.crash()
        start = Barrier(2)

        def recover():
            start.wait(timeout=5)
            return self.service.recover()

        with ThreadPoolExecutor(max_workers=2) as workers:
            jobs = [workers.submit(recover) for _ in range(2)]
            outcomes = [job.result(timeout=10) for job in jobs]
        self.assertCountEqual(outcomes, [redo.Recovery(2, True), redo.Recovery(2, False)])
        self.assertEqual(self.state(), (2, 2, 1))


class RedoTraceTests(unittest.TestCase):
    def test_recount_schedules_and_keep_invalid_candidates_quarantined(self):
        path = Path(__file__).resolve().parents[1] / "results/witness_redo_v1.json"
        data = json.loads(path.read_bytes())
        totals = {name: Counter(confirmed=0, unresolved=0, restored_transitions=0, current_authority=0)
                  for name in data["summary"]}
        self.assertEqual(len({(run["scenario"], run["policy"]) for run in data["runs"]}), 24)
        for row in data["runs"]:
            end = row["trace"][-1]
            confirmed = end["settled_provider"] is not None and sum(end["effect_rows"]) == 1
            self.assertEqual(confirmed, row["confirmed"])
            totals[row["policy"]]["confirmed" if confirmed else "unresolved"] += 1
            totals[row["policy"]]["current_authority"] += end["authority_current"]
            for event in row["trace"]:
                self.assertLessEqual(sum(event["effect_rows"]), 1)
                if event.get("status") == "repaired":
                    totals[row["policy"]]["restored_transitions"] += 1
                    self.assertTrue(event["authority_current"])
                if event.get("status") == "injected_provider_rollback":
                    self.assertLess(event["revision"], event["witness_revision"])
            if row["scenario"] in ("two_revisions_behind", "changed_redo_command", "divergent_predecessor_content"):
                self.assertEqual(end["status"], "quarantined")
                self.assertFalse(end["authority_current"])
        self.assertEqual(totals, data["summary"])

    def test_same_local_view_can_hide_an_already_executed_external_effect(self):
        path = Path(__file__).resolve().parents[1] / "results/witness_redo_v1.json"
        first, second = json.loads(path.read_bytes())["external_effect_control"]
        self.assertEqual(first["local_before_repair"], second["local_before_repair"])
        self.assertEqual(first["local_witness_head"], second["local_witness_head"])
        self.assertEqual([first["withhold_send"], second["withhold_send"]], [0, 1])
        self.assertEqual([first["blind_resend"], second["blind_resend"]], [1, 2])
