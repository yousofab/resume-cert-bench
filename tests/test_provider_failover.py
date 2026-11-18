import sqlite3
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from resumecert import provider_failover as routing, revocable_delivery as delivery
from resumecert.archive_repair import checkpoint, create_repair_schema, resume
from resumecert.archive_transfer import FIELDS, connect, seed
from resumecert.effect_delivery import DeliveryConflict, create_source_schema, prepare_notice


class ProviderFailoverTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.source_path = self.root / "source.sqlite3"
        self.primary_path = self.root / "primary.sqlite3"
        self.secondary_path = self.root / "secondary.sqlite3"
        self.source = self.enterContext(closing(connect(self.source_path)))
        self.primary = self.enterContext(closing(connect(self.primary_path)))
        self.secondary = self.enterContext(closing(connect(self.secondary_path)))
        routing.create_provider(self.primary, "primary")
        routing.create_provider(self.secondary, "secondary")
        create_repair_schema(self.source)
        create_source_schema(self.source)
        routing.create_routes(self.source)
        seed(self.source, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
        self.notice = prepare_notice(self.source, "test-source", 1, "Synthetic packet")
        checkpoint(self.source, 1, 4)
        with self.assertRaises(DeliveryConflict):
            routing.enroll(self.source, 1, "primary", "secondary")
        self.assertEqual(resume(self.source, 1, "reuse_only", 0).status, "committed")
        routing.enroll(self.source, 1, "primary", "secondary")

    def begin(self):
        return routing.begin_dispatch(self.source, 1, self.primary)

    def test_unknown_primary_preserves_route_and_cannot_route_to_another_provider(self):
        self.assertEqual(routing.recover_failover(self.source, 1, self.primary), "unknown")
        request = self.begin()
        self.assertEqual(routing.recover_failover(self.source, 1, None), "unknown")
        with self.assertRaises(DeliveryConflict):
            routing.recover_failover(self.source, 1, self.secondary)
        with self.assertRaises(DeliveryConflict):
            routing.begin_dispatch(self.source, 1, self.secondary)
        with self.assertRaises(DeliveryConflict):
            routing.apply_dispatch(self.secondary, request)
        with self.assertRaises(DeliveryConflict):
            routing.enroll(self.source, 1, "primary", "unapproved")
        self.assertIsNone(delivery.reconcile(self.primary, self.notice))

    def test_ordinary_fence_remains_insufficient_even_after_restart(self):
        request = self.begin()
        fence = delivery.revoke(self.primary, self.notice, request.epoch)
        self.assertIsNone(fence.effect)
        self.assertEqual(routing.recover_failover(self.source, 1, self.primary), "unknown")
        delivery.regrant(self.primary, self.notice, fence.closed_epoch)
        with self.assertRaises(delivery.DispatchDenied):
            routing.retire(self.primary, self.notice, request.epoch)
        self.assertIsNone(routing.retirement(self.primary, self.notice))

    def test_positive_receipt_is_recovered_even_without_retirement(self):
        request = self.begin()
        before = routing.apply_dispatch(self.primary, request)
        self.assertEqual(routing.recover_failover(self.source, 1, self.primary), "primary_confirmed")
        self.assertTrue(routing.confirm(self.source, 1, self.primary))
        self.assertEqual(before, routing.retire(self.primary, self.notice, request.epoch).effect)
        self.assertEqual(routing.recover_failover(self.source, 1, self.primary), "already_confirmed")
        with self.assertRaises(DeliveryConflict):
            routing.begin_dispatch(self.source, 1, self.secondary)

    def test_failed_retirement_rolls_back_fence_and_preserves_authority(self):
        pending = self.begin()
        self.primary.executescript("""
            CREATE TRIGGER fixture_reject_retirement AFTER INSERT ON permanent_retirements
            BEGIN SELECT RAISE(ABORT, 'retirement storage failure'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "retirement storage failure"):
            routing.retire(self.primary, self.notice, pending.epoch)
        self.assertIsNone(delivery.fence_status(self.primary, self.notice, pending.epoch))
        self.assertIsNone(routing.retirement(self.primary, self.notice))
        self.assertEqual(delivery.authority(self.primary, self.notice), (pending.epoch, True))

    def test_real_process_exit_after_retirement_and_durable_secondary_route(self):
        old = self.begin()
        child_code = """
import os
import sys
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import Notice
from resumecert.provider_failover import retire

store, key, payload = sys.argv[1:]
fence = retire(connect(store), Notice(key, payload), 1)
assert fence.effect is None
os._exit(23)
"""
        child = subprocess.run([sys.executable, "-c", child_code, str(self.primary_path),
                                self.notice.operation_key, self.notice.payload],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 23, child.stderr.decode())
        self.assertEqual(routing.recover_failover(self.source, 1, self.primary), "secondary")
        with closing(connect(self.source_path)) as recovered:
            self.assertEqual(routing.recover_failover(recovered, 1, None), "secondary")
            new = routing.begin_dispatch(recovered, 1, self.secondary)
            receipt = routing.apply_dispatch(self.secondary, new)
            self.assertEqual(routing.apply_dispatch(self.secondary, new), receipt)
            self.assertTrue(routing.confirm(recovered, 1, self.secondary))
            self.assertEqual(recovered.execute("SELECT settled_provider FROM failover_routes").fetchone(),
                             ("secondary",))
        with self.assertRaises(delivery.DispatchDenied):
            routing.apply_dispatch(self.primary, old)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot reopen"):
            delivery.regrant(self.primary, self.notice, 2)
        with self.assertRaises(DeliveryConflict):
            routing.retire(self.primary, replace(self.notice, payload="changed"), 1)
        self.assertIsNone(delivery.reconcile(self.primary, self.notice))

    def test_apply_and_retire_race_only_allows_one_provider_to_create_effect(self):
        request = self.begin()
        ready = Barrier(2)

        def worker(call, value):
            with closing(sqlite3.connect(self.primary_path, autocommit=True, timeout=5)) as primary:
                ready.wait(timeout=5)
                try:
                    return call(primary, value)
                except delivery.DispatchDenied:
                    return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            attempt = workers.submit(worker, routing.apply_dispatch, request)
            close = workers.submit(worker, lambda db, item: routing.retire(db, item.notice, item.epoch), request)
            record = attempt.result(timeout=10)
            fence = close.result(timeout=10)
        self.assertEqual(fence.effect, record)
        selected = routing.recover_failover(self.source, 1, self.primary)
        if record is None:
            self.assertEqual(selected, "secondary")
            routing.apply_dispatch(self.secondary, routing.begin_dispatch(self.source, 1, self.secondary))
        else:
            self.assertEqual(selected, "primary_confirmed")
        self.assertEqual(sum(db.execute("SELECT count(*) FROM handoff_effects").fetchone()[0]
                             for db in (self.primary, self.secondary)), 1)
