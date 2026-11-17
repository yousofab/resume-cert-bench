import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from resumecert import revocable_delivery as delivery
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import DeliveryConflict, Notice


class RevocableDeliveryTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.path = root / "destination.sqlite3"
        self.sink = self.enterContext(closing(connect(self.path)))
        self.notice = Notice("archive-handoff-1", "synthetic handoff packet")
        delivery.create_destination(self.sink)
        self.assertEqual(delivery.register(self.sink, self.notice), 1)

    def test_fenced_absence_survives_restart_and_does_not_claim_future_absence(self):
        fence = delivery.revoke(self.sink, self.notice, 1)
        self.assertIsNone(fence.effect)
        with closing(connect(self.path)) as recovered:
            self.assertEqual(delivery.fence_status(recovered, self.notice, 1), fence)
            with self.assertRaises(delivery.DispatchDenied):
                delivery.apply(recovered, self.notice, 1)
            renewed = delivery.regrant(recovered, self.notice, fence.closed_epoch)
            self.assertEqual(renewed, 3)
            self.assertEqual(delivery.regrant(recovered, self.notice, fence.closed_epoch), 3)
            self.assertEqual(delivery.revoke(recovered, self.notice, 1), fence)
            self.assertEqual(delivery.authority(recovered, self.notice), (3, True))
            with self.assertRaises(delivery.DispatchDenied):
                delivery.apply(recovered, self.notice, 1)
            effect = delivery.apply(recovered, self.notice, renewed)
            self.assertEqual(effect.accepted_epoch, 3)
            self.assertEqual(delivery.fence_status(recovered, self.notice, 1), fence)

    def test_reconciliation_after_revoke_preserves_original_receipt(self):
        self.assertIsNone(delivery.fence_status(self.sink, self.notice, 1))
        self.assertIsNone(delivery.reconcile(self.sink, self.notice))
        original = delivery.apply(self.sink, self.notice, 1)
        fence = delivery.revoke(self.sink, self.notice, 1)
        self.assertEqual(fence.effect, original)
        self.assertEqual(delivery.reconcile(self.sink, self.notice), original)
        with self.assertRaises(delivery.DispatchDenied):
            delivery.apply(self.sink, self.notice, 1)
        self.assertEqual(delivery.apply(self.sink, self.notice, delivery.regrant(
            self.sink, self.notice, fence.closed_epoch)), original)
        self.assertEqual(self.sink.execute("SELECT count(*) FROM handoff_effects").fetchone(), (1,))

    def test_fence_failure_rolls_back_authority_change(self):
        self.sink.executescript("""
            CREATE TRIGGER fixture_reject_fence AFTER INSERT ON handoff_fences
            BEGIN SELECT RAISE(ABORT, 'fault while recording fence'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "fault while recording fence"):
            delivery.revoke(self.sink, self.notice, 1)
        self.assertEqual(delivery.authority(self.sink, self.notice), (1, True))
        self.assertIsNone(delivery.fence_status(self.sink, self.notice, 1))
        self.assertEqual(delivery.apply(self.sink, self.notice, 1).accepted_epoch, 1)

    def test_reused_key_cannot_change_the_payload_or_reopen_the_grant(self):
        delivery.revoke(self.sink, self.notice, 1)
        self.assertEqual(delivery.register(self.sink, self.notice), 2)
        self.assertEqual(delivery.authority(self.sink, self.notice), (2, False))
        other = replace(self.notice, payload="another payload")
        for call in (delivery.register, delivery.reconcile):
            with self.assertRaises(DeliveryConflict):
                call(self.sink, other)

    def test_sql_guard_rejects_stale_effects_even_with_manual_insert(self):
        delivery.revoke(self.sink, self.notice, 1)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "handoff authority is stale"):
            self.sink.execute("""
                INSERT INTO handoff_effects (operation_key, payload_digest, payload, accepted_epoch)
                VALUES (?, ?, ?, ?)
            """, (self.notice.operation_key, self.notice.payload_digest, self.notice.payload, 1))
        self.assertIsNone(delivery.reconcile(self.sink, self.notice))

    def test_real_concurrent_commit_and_revoke_have_only_legal_outcomes(self):
        ready = Barrier(2)

        def run(call):
            with closing(sqlite3.connect(self.path, autocommit=True, timeout=4)) as connection:
                ready.wait(timeout=4)
                try:
                    return call(connection, self.notice, 1)
                except delivery.DispatchDenied:
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            applying = executor.submit(run, delivery.apply)
            revoking = executor.submit(run, delivery.revoke)
            recorded = applying.result(timeout=8)
            fence = revoking.result(timeout=8)
        self.assertEqual(fence.effect, recorded)
        self.assertEqual(delivery.reconcile(self.sink, self.notice), recorded)
        self.assertEqual(delivery.authority(self.sink, self.notice), (2, False))
        with self.assertRaises(delivery.DispatchDenied):
            delivery.apply(self.sink, self.notice, 1)
