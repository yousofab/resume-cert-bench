"""Regress atomic witness pruning, migration, and concurrent multi-revision replay."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from resumecert import authority_redo as redo, authority_redo_chain as chain
from resumecert import revocable_delivery as delivery, witnessed_authority as witnessed
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import Notice
from resumecert.provider_authority import ProviderAuthority, create_authority
from resumecert.provider_failover import Dispatch


class BoundedRedoTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.witness = self.root / "witness.sqlite3"
        with closing(connect(self.witness)) as db:
            witnessed.create_witness(db)
            chain.create_redo_chain(db)
        self.authority = self.enroll("primary")
        self.service = chain.BoundedRedoAuthority(self.authority, self.witness, 2)
        self.key = b"k" * 32

    def enroll(self, name):
        path = self.root / f"{name}.sqlite3"
        with closing(connect(path)) as db:
            create_authority(db, name, "fixture")
        authority = ProviderAuthority(path, name, "fixture")
        witnessed.enroll_fresh(authority, self.witness)
        return authority

    def register(self, service, key):
        notice = Notice(key, "inert payload")
        with service._session(("register", notice, None)) as (db, _):
            epoch = delivery._register_in_transaction(db, notice)
        return Dispatch(service.authority.provider_id, notice, epoch)

    def save(self, name):
        backup = self.root / name
        with self.authority._connect() as db, closing(connect(backup)) as target:
            db.backup(target)
        return backup

    def test_failed_prune_reverts_head_append_and_provider_effect(self):
        primary = chain.BoundedRedoAuthority(self.authority, self.witness, 1)
        dispatch = self.register(primary, "one")
        secondary = chain.BoundedRedoAuthority(self.enroll("secondary"), self.witness, 1)
        self.register(secondary, "other")
        with closing(connect(self.witness)) as db:
            db.execute("""
                CREATE TRIGGER interrupt_prune BEFORE DELETE ON authority_redo_chain
                BEGIN SELECT RAISE(ABORT, 'prune interrupted'); END
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "prune interrupted"):
            primary.apply(dispatch)
        self.assertEqual(primary.verify_current(), 1)
        with primary.authority._connect() as db:
            self.assertIsNone(delivery.reconcile(db, dispatch.notice))
        with closing(connect(self.witness)) as db:
            query = "SELECT provider_id, revision FROM authority_redo_chain ORDER BY provider_id, revision"
            self.assertEqual(db.execute(query).fetchall(), [("primary", 1), ("secondary", 1)])
            db.execute("DROP TRIGGER interrupt_prune")
            primary.apply(dispatch)
            self.assertEqual(db.execute(query).fetchall(), [("primary", 2), ("secondary", 1)])
        self.assertEqual(primary.verify_current(), 2)
        self.assertEqual(secondary.verify_current(), 1)

    def test_existing_witness_can_switch_with_current_backup_and_no_history_backfill(self):
        empty = self.save("empty.backup")
        with closing(connect(self.witness)) as db:
            redo.create_redo_log(db)
        legacy = redo.RecoverableAuthority(self.authority, self.witness)
        dispatch = self.register(legacy, "one")
        current = self.save("current.backup")
        self.assertEqual(self.service.recover(), chain.ChainRecovery(1, 0))
        self.service.apply(dispatch)
        shutil.copyfile(empty, self.authority.path)
        with self.assertRaisesRegex(witnessed.StateMismatch, "incomplete"):
            self.service.recover()
        shutil.copyfile(current, self.authority.path)
        self.assertEqual(self.service.recover(), chain.ChainRecovery(2, 1))

    def test_concurrent_recovery_replays_once_and_preserves_empty_retirement(self):
        self.register(self.service, "one")
        backup = self.save("provider.backup")
        dispatch = self.register(self.service, "two")
        proof = self.service.issue(dispatch, self.key, terminal=True)
        shutil.copyfile(backup, self.authority.path)
        start = Barrier(2, timeout=5)

        def recover():
            start.wait()
            return self.service.recover()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: recover(), range(2)))
        self.assertCountEqual(outcomes, [chain.ChainRecovery(3, 2), chain.ChainRecovery(3, 0)])
        self.assertEqual(self.service.issue(dispatch, self.key, terminal=True), proof)
        with self.assertRaises(delivery.DispatchDenied):
            self.service.apply(dispatch)
        with self.authority._connect() as db:
            self.assertIsNone(delivery.reconcile(db, dispatch.notice))

    def test_divergent_predecessor_and_non_integer_revision_are_quarantined(self):
        dispatch = self.register(self.service, "one")
        backup = self.save("provider.backup")
        self.service.apply(dispatch)
        self.service.issue(dispatch, self.key, terminal=True)
        shutil.copyfile(backup, self.authority.path)
        with self.authority._connect() as db:
            db.execute("UPDATE handoff_authority SET payload_digest=?", ("0" * 64,))
            divergent = witnessed._commitment(db)
        with self.assertRaisesRegex(witnessed.StateMismatch, "discontinuous"):
            self.service.recover()
        with self.authority._connect() as db:
            self.assertEqual(witnessed._commitment(db), divergent)
            db.execute("UPDATE authority_revision SET revision='invalid'")
        with self.assertRaisesRegex(witnessed.StateMismatch, "invalid authority or witness revision"):
            self.service.recover()
